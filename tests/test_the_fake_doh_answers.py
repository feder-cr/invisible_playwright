"""The helpers of the DoH e2e (`_doh_servers.py`) do what the e2e relies on, tested with plain Python clients.

The e2e tests of `test_doh_through_the_proxy_e2e.py` are written against a contract no engine in this tree has yet, so
what can be proven now is that the TOOLS are right: the DNS codec, the DoH server over HTTP/1.1 and over h2, and the
SOCKS5 gateway that hands a CONNECT to the DoH address to the DoH server, tagged with its own name. A helper that
answers wrongly would turn the e2e red or green for no reason, and nobody would know which.
"""
from __future__ import annotations

import base64
import socket
import ssl
import struct
import threading

import pytest

import _doh_servers as ds
from _doh_servers import (DOH_BOOTSTRAP, DOH_HOST, DOH_PATH, DohGateway, FakeDoh, QTYPE_A, QTYPE_AAAA,
                          encode_query, parse_query)

pytestmark = pytest.mark.unit

NAMES = {"page-a.test": "127.0.0.1", "other.test": "127.0.0.2"}


# ------------------------------------------------------------------ a tiny parser, independent of the server's
def read_response(message: bytes):
    """(id, flags, rcode, question name, [(type, ttl, rdata)]) read the way a client reads it."""
    ident, flags, qd, an, ns, ar = struct.unpack("!6H", message[:12])
    assert (qd, ns, ar) == (1, 0, 0), (qd, ns, ar)
    i, labels = 12, []
    while message[i]:
        labels.append(message[i + 1:i + 1 + message[i]].decode())
        i += 1 + message[i]
    i += 1 + 4  # the root label, QTYPE, QCLASS
    answers = []
    for _ in range(an):
        assert message[i:i + 2] == b"\xc0\x0c", "the owner name is a pointer to the question"
        rtype, rclass, ttl, rdlength = struct.unpack("!HHIH", message[i + 2:i + 12])
        assert rclass == 1
        answers.append((rtype, ttl, message[i + 12:i + 12 + rdlength]))
        i += 12 + rdlength
    assert i == len(message), "no trailing bytes"
    return ident, flags, flags & 0xF, ".".join(labels), answers


def _tls(host_port, *, alpn):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(alpn)
    raw = socket.create_connection(host_port, timeout=10)
    return context.wrap_socket(raw, server_hostname=DOH_HOST)


def _h1_request(tls, method, target, body=b"", content_type=None, close=False):
    head = "%s %s HTTP/1.1\r\nHost: %s\r\nAccept: application/dns-message\r\n" % (method, target, DOH_HOST)
    if content_type:
        head += "Content-Type: %s\r\n" % content_type
    if method == "POST":
        head += "Content-Length: %d\r\n" % len(body)
    if close:
        head += "Connection: close\r\n"
    tls.sendall(head.encode() + b"\r\n" + body)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = tls.recv(65536)
        assert chunk, "the server closed without answering"
        data += chunk
    header, _, rest = data.partition(b"\r\n\r\n")
    lines = header.decode().split("\r\n")
    status = int(lines[0].split()[1])
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
    length = int(headers["content-length"])
    while len(rest) < length:
        rest += tls.recv(65536)
    return status, headers, rest


def _get_target(message):
    return "%s?dns=%s" % (DOH_PATH, base64.urlsafe_b64encode(message).rstrip(b"=").decode())


@pytest.fixture
def doh():
    with FakeDoh(NAMES) as server:
        yield server


@pytest.fixture
def doh_h1_only():
    with FakeDoh(NAMES, h2=False) as server:
        yield server


# -------------------------------------------------------------------------------------------------- the codec
def test_a_query_is_parsed_from_its_labels():
    q = parse_query(encode_query("Page-A.test.", QTYPE_AAAA, ident=7))
    assert (q.ident, q.name, q.qtype, q.qclass) == (7, "page-a.test", QTYPE_AAAA, 1)


@pytest.mark.parametrize("junk", [b"", b"\x00" * 5, b"\x00" * 12, encode_query("a.test")[:-2],
                                  encode_query("a.test")[:12] + b"\xc0\x0c" + b"\x00\x01\x00\x01"])
def test_a_message_that_is_not_a_query_is_refused(junk):
    with pytest.raises(ValueError):
        parse_query(junk)


def test_the_response_codec_round_trips_through_the_independent_parser():
    q = parse_query(encode_query("page-a.test", QTYPE_A, ident=9))
    ident, flags, rcode, name, answers = read_response(ds.answer_for(NAMES, q))
    assert (ident, rcode, name) == (9, 0, "page-a.test")
    assert flags & 0x8000 and flags & 0x0100, "QR set, RD copied"
    assert answers == [(QTYPE_A, ds.DOH_TTL, socket.inet_aton("127.0.0.1"))]


# ------------------------------------------------------------------------------------------- HTTP/1.1, POST and GET
@pytest.mark.parametrize("how", ["POST", "GET"])
def test_an_a_query_for_a_known_name_is_answered_over_http1(doh_h1_only, how):
    tls = _tls(("127.0.0.1", doh_h1_only.port), alpn=["http/1.1"])
    try:
        message = encode_query("page-a.test", QTYPE_A, ident=0)
        if how == "POST":
            status, headers, body = _h1_request(tls, "POST", DOH_PATH, message, "application/dns-message")
        else:
            status, headers, body = _h1_request(tls, "GET", _get_target(message))
        assert status == 200 and headers["content-type"] == "application/dns-message"
        _ident, _flags, rcode, name, answers = read_response(body)
        assert (rcode, name) == (0, "page-a.test")
        assert answers == [(QTYPE_A, 30, bytes([127, 0, 0, 1]))]
    finally:
        tls.close()
    assert [(q.name, q.qtype, q.via) for q in doh_h1_only.queries] == [("page-a.test", QTYPE_A, "direct")]
    assert doh_h1_only.alpn_of("direct") == {"http/1.1"}


def test_an_unknown_name_is_nxdomain_over_http1(doh_h1_only):
    tls = _tls(("127.0.0.1", doh_h1_only.port), alpn=["http/1.1"])
    try:
        status, _h, body = _h1_request(tls, "POST", DOH_PATH, encode_query("nobody.test"), "application/dns-message")
    finally:
        tls.close()
    assert status == 200, "a name that does not exist is a DNS answer, not an HTTP error"
    _i, _f, rcode, name, answers = read_response(body)
    assert (rcode, name, answers) == (3, "nobody.test", [])
    assert doh_h1_only.queries_for("nobody.test"), "the miss is recorded too"


def test_an_aaaa_query_for_a_known_name_is_empty_noerror_over_http1(doh_h1_only):
    tls = _tls(("127.0.0.1", doh_h1_only.port), alpn=["http/1.1"])
    try:
        _s, _h, body = _h1_request(tls, "POST", DOH_PATH, encode_query("page-a.test", QTYPE_AAAA),
                                   "application/dns-message")
    finally:
        tls.close()
    _i, _f, rcode, _n, answers = read_response(body)
    assert (rcode, answers) == (0, [])


def test_http1_keeps_the_connection_for_the_next_query(doh_h1_only):
    tls = _tls(("127.0.0.1", doh_h1_only.port), alpn=["http/1.1"])
    try:
        for name in ("page-a.test", "other.test"):
            status, _h, body = _h1_request(tls, "POST", DOH_PATH, encode_query(name), "application/dns-message")
            assert status == 200 and read_response(body)[4]
    finally:
        tls.close()
    assert len(doh_h1_only.connections) == 1 and len(doh_h1_only.queries) == 2


@pytest.mark.parametrize("request_, status", [
    (("POST", DOH_PATH, b"junk", "application/dns-message"), 400),
    (("POST", DOH_PATH, b"", "text/plain"), 415),
    (("GET", DOH_PATH, b"", None), 400),
    (("GET", "/elsewhere", b"", None), 404),
    (("PUT", DOH_PATH, b"", None), 405),
])
def test_what_is_not_a_doh_query_is_an_http_error_and_not_a_query(doh_h1_only, request_, status):
    method, target, body, content_type = request_
    tls = _tls(("127.0.0.1", doh_h1_only.port), alpn=["http/1.1"])
    try:
        got, _h, _b = _h1_request(tls, method, target, body, content_type, close=True)
    finally:
        tls.close()
    assert got == status
    assert doh_h1_only.queries == []
    assert doh_h1_only.bad_requests[-1][3] == status


def test_the_certificate_covers_the_doh_host_and_loopback(doh):
    """A client that VERIFIES (trusting the certificate file) accepts both names the DoH may be reached by."""
    for server_name in (DOH_HOST, "127.0.0.1"):
        context = ssl.create_default_context(cafile=str(doh.certificate_path))
        raw = socket.create_connection(("127.0.0.1", doh.port), timeout=10)
        tls = context.wrap_socket(raw, server_hostname=server_name)
        tls.close()
    context = ssl.create_default_context(cafile=str(doh.certificate_path))
    raw = socket.create_connection(("127.0.0.1", doh.port), timeout=10)
    with pytest.raises(ssl.SSLCertVerificationError):
        context.wrap_socket(raw, server_hostname="some-other-host.test")
    raw.close()


def test_a_client_that_refuses_the_certificate_is_listed_as_a_tls_failure(doh):
    context = ssl.create_default_context()  # verifies, and does not trust the self-signed certificate
    raw = socket.create_connection(("127.0.0.1", doh.port), timeout=10)
    with pytest.raises(ssl.SSLError):
        context.wrap_socket(raw, server_hostname=DOH_HOST)
    raw.close()
    assert ds.wait_until(lambda: doh.tls_failures, 5), "the failed handshake is recorded"


# ------------------------------------------------------------------------------------------------------- h2
def _h2_query(doh, method, message, raw=None):
    """One DoH query over h2, on `raw` (a connected socket, e.g. a tunnel) or straight to the server."""
    h2_config = pytest.importorskip("h2.config")
    h2_connection = pytest.importorskip("h2.connection")
    h2_events = pytest.importorskip("h2.events")
    if raw is None:
        tls = _tls(("127.0.0.1", doh.port), alpn=["h2", "http/1.1"])
    else:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(["h2", "http/1.1"])
        tls = context.wrap_socket(raw, server_hostname=DOH_HOST)
    assert tls.selected_alpn_protocol() == "h2"
    conn = h2_connection.H2Connection(config=h2_config.H2Configuration(client_side=True, header_encoding="utf-8"))
    conn.initiate_connection()
    headers = [(":method", method), (":scheme", "https"), (":authority", DOH_HOST),
               (":path", DOH_PATH if method == "POST" else _get_target(message)), ("accept", "application/dns-message")]
    if method == "POST":
        headers += [("content-type", "application/dns-message"), ("content-length", str(len(message)))]
        conn.send_headers(1, headers)
        conn.send_data(1, message, end_stream=True)
    else:
        conn.send_headers(1, headers, end_stream=True)
    tls.sendall(conn.data_to_send())
    status, body, ended = None, b"", False
    while not ended:
        data = tls.recv(65536)
        assert data, "the server closed without answering"
        for event in conn.receive_data(data):
            if isinstance(event, h2_events.ResponseReceived):
                status = dict(event.headers)[":status"]
            elif isinstance(event, h2_events.DataReceived):
                body += event.data
                conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(event, h2_events.StreamEnded):
                ended = True
        tls.sendall(conn.data_to_send())
    tls.close()
    return status, body


@pytest.mark.parametrize("how", ["POST", "GET"])
def test_an_a_query_is_answered_over_h2(doh, how):
    status, body = _h2_query(doh, how, encode_query("page-a.test", QTYPE_A))
    assert status == "200"
    _i, _f, rcode, name, answers = read_response(body)
    assert (rcode, name, answers) == (0, "page-a.test", [(QTYPE_A, 30, bytes([127, 0, 0, 1]))])
    assert doh.alpn_of("direct") == {"h2"}


def test_a_miss_and_an_aaaa_are_answered_over_h2(doh):
    _s, body = _h2_query(doh, "POST", encode_query("nobody.test"))
    assert read_response(body)[2:] == (3, "nobody.test", [])
    _s, body = _h2_query(doh, "GET", encode_query("page-a.test", QTYPE_AAAA))
    assert (read_response(body)[2], read_response(body)[4]) == (0, [])
    assert [(q.name, q.qtype) for q in doh.queries] == [("nobody.test", 1), ("page-a.test", QTYPE_AAAA)]


def test_a_server_that_does_not_offer_h2_selects_http1_for_a_client_that_asks_for_both(doh_h1_only):
    tls = _tls(("127.0.0.1", doh_h1_only.port), alpn=["h2", "http/1.1"])
    try:
        assert tls.selected_alpn_protocol() == "http/1.1"
    finally:
        tls.close()


# ---------------------------------------------------------------------------------------- the gateway (H2)
def _socks_connect(gateway_port, host, port, *, credentials=None, by_name=False):
    """A plain SOCKS5 client: returns (socket, reply code)."""
    s = socket.create_connection(("127.0.0.1", gateway_port), timeout=10)
    if credentials:
        s.sendall(b"\x05\x01\x02")
        assert s.recv(2) == b"\x05\x02"
        u, p = (c.encode() for c in credentials)
        s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        assert s.recv(2) == b"\x01\x00"
    else:
        s.sendall(b"\x05\x01\x00")
        assert s.recv(2) == b"\x05\x00"
    if by_name:
        target = b"\x03" + bytes([len(host)]) + host.encode()
    else:
        target = b"\x01" + socket.inet_aton(host)
    s.sendall(b"\x05\x01\x00" + target + struct.pack("!H", port))
    reply = b""
    while len(reply) < 10:
        chunk = s.recv(10 - len(reply))
        if not chunk:
            break
        reply += chunk
    return s, (reply[1] if len(reply) > 1 else None)


def _query_through(sock, name, qtype=QTYPE_A):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["http/1.1"])
    tls = context.wrap_socket(sock, server_hostname=DOH_HOST)
    try:
        status, _h, body = _h1_request(tls, "POST", DOH_PATH, encode_query(name, qtype), "application/dns-message",
                                       close=True)
    finally:
        tls.close()
    return status, body


@pytest.mark.parametrize("by_name", [False, True])
def test_a_connect_to_the_doh_address_reaches_the_doh_and_is_tagged_with_the_gateway(doh, by_name):
    with DohGateway("gw1", doh) as gw:
        target = DOH_HOST if by_name else DOH_BOOTSTRAP
        s, code = _socks_connect(gw.port, target, 443, by_name=by_name)
        assert code == 0
        status, body = _query_through(s, "page-a.test")
        assert status == 200 and read_response(body)[4][0][0] == QTYPE_A
        assert gw.connects == [(target, 443)]
        assert gw.doh_connects() == [(target, 443)]
    assert [(q.name, q.qtype, q.via) for q in doh.queries] == [("page-a.test", QTYPE_A, "gw1")]
    assert doh.vias_of("page-a.test") == {"gw1"}


def test_two_gateways_tag_their_own_queries(doh):
    with DohGateway("gw1", doh) as one, DohGateway("gw2", doh) as two:
        for gateway, name in ((one, "page-a.test"), (two, "other.test")):
            s, code = _socks_connect(gateway.port, DOH_BOOTSTRAP, 443)
            assert code == 0
            assert _query_through(s, name)[0] == 200
    assert doh.vias_of("page-a.test") == {"gw1"}
    assert doh.vias_of("other.test") == {"gw2"}


def test_the_doh_road_works_over_h2_through_the_gateway(doh):
    import h2  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    with DohGateway("gw1", doh) as gw:
        s, code = _socks_connect(gw.port, DOH_BOOTSTRAP, 443)
        assert code == 0
        status, body = _h2_query(doh, "POST", encode_query("page-a.test"), raw=s)
    assert status == "200" and read_response(body)[4]
    assert doh.alpn_of("gw1") == {"h2"}
    assert doh.vias_of("page-a.test") == {"gw1"}


def test_a_gateway_with_credentials_records_what_it_was_offered_and_serves_only_the_right_ones(doh):
    with DohGateway("gw1", doh, user="alice", password="pw-1") as gw:
        try:
            _socks_connect(gw.port, DOH_BOOTSTRAP, 443, credentials=("alice", "bad"))
        except AssertionError:
            pass  # the refusal of the auth reply is what the client helper asserts on
        s, code = _socks_connect(gw.port, DOH_BOOTSTRAP, 443, credentials=("alice", "pw-1"))
        assert code == 0
        assert _query_through(s, "page-a.test")[0] == 200
        assert gw.credentials_seen == [("alice", "bad"), ("alice", "pw-1")]
    assert doh.vias_of("page-a.test") == {"gw1"}


def test_a_connect_to_a_loopback_address_is_relayed_for_real_and_recorded(doh):
    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen(1)

    def serve():
        c, _ = echo.accept()
        c.sendall(c.recv(100).upper())
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    with DohGateway("gw1", doh) as gw:
        s, code = _socks_connect(gw.port, "127.0.0.1", echo.getsockname()[1])
        assert code == 0
        s.sendall(b"ping")
        assert s.recv(10) == b"PING"
        s.close()
        assert gw.connects == [("127.0.0.1", echo.getsockname()[1])]
    echo.close()
    assert doh.queries == []


def test_a_connect_that_would_leave_the_loopback_is_refused_and_never_resolved(doh):
    with DohGateway("gw1", doh) as gw:
        s, code = _socks_connect(gw.port, "198.51.100.77", 80)
        assert code == 2
        s.close()
        s, code = _socks_connect(gw.port, "page-a.test", 80, by_name=True)
        assert code == 4, "a name is refused, not looked up"
        s.close()
        assert gw.refused == [("198.51.100.77", 80), ("page-a.test", 80)]
        assert ("page-a.test", 80) in gw.connects, "a CONNECT by name is visible to a test that forbids it"


def test_the_first_connection_can_be_answered_late(doh):
    import time
    with DohGateway("gw1", doh, first_connection_delay=0.6) as gw:
        started = time.monotonic()
        s, code = _socks_connect(gw.port, DOH_BOOTSTRAP, 443)
        assert code == 0 and time.monotonic() - started >= 0.55
        s.close()
        started = time.monotonic()
        s, code = _socks_connect(gw.port, DOH_BOOTSTRAP, 443)
        assert code == 0 and time.monotonic() - started < 0.5, "only the first one is late"
        s.close()


def test_a_killed_gateway_refuses_and_drops_its_open_tunnels(doh):
    gw = DohGateway("gw1", doh)
    port = gw.port
    s, code = _socks_connect(port, DOH_BOOTSTRAP, 443)
    assert code == 0
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["http/1.1"])
    tls = context.wrap_socket(s, server_hostname=DOH_HOST)
    assert _h1_request(tls, "POST", DOH_PATH, encode_query("page-a.test"), "application/dns-message")[0] == 200
    gw.kill()
    with pytest.raises((OSError, AssertionError)):
        _h1_request(tls, "POST", DOH_PATH, encode_query("other.test"), "application/dns-message")
    tls.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)
    assert gw.killed and doh.vias_of("other.test") == set()
