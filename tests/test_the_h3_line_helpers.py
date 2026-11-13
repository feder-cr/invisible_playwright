"""The server and the judge of the HTTP/3 e2e work, without a browser (hermetic, loopback).

A retail Firefox (Windows 159, DoH forced like our core, 15 sessions per condition) never reads h3 at the first visit
behind a proxy: with UDP free h2/h2/h3 in 9, h2/h2/h2 in 4, h2/h3/h3 in 2; with UDP blocked h2/h2/h2 in 15 of 15 with no delay of
the load; with no proxy and the native DNS (loopback) h2, h3, h3. The e2e visits the page 3 times with no proxy and 5 times
behind any proxy and asserts that. These tests hold what it stands on: the local server really speaks HTTP/2 on its TCP port (if it
spoke HTTP/1.1 the first visit could never read ``h2``), says ``Alt-Svc: h3`` there, and answers the same document over HTTP/3;
and the function that judges the visits names what is wrong.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import time

import pytest

import _udp_line as line
from _h3_server import PAGE, H3Server

pytestmark = pytest.mark.unit


# -- the server ---------------------------------------------------------------------------------------------------------
def _tls_client(server, alpn):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(alpn)
    raw = socket.create_connection((server.address, server.port), timeout=10)
    return context.wrap_socket(raw, server_hostname=server.address)


def _h2_get(server, path):
    import h2.config
    import h2.connection
    import h2.events

    tls = _tls_client(server, ["h2", "http/1.1"])
    try:
        assert tls.selected_alpn_protocol() == "h2", "the TCP side did not negotiate h2"
        conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8"))
        conn.initiate_connection()
        conn.send_headers(1, [(":method", "GET"), (":authority", server.address), (":scheme", "https"), (":path", path)],
                          end_stream=True)
        tls.sendall(conn.data_to_send())
        headers, body, ended = {}, b"", False
        while not ended:
            data = tls.recv(65536)
            assert data, "the server closed the connection before the end of the stream"
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.ResponseReceived):
                    headers = dict(event.headers)
                elif isinstance(event, h2.events.DataReceived):
                    body += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    ended = True
            out = conn.data_to_send()
            if out:
                tls.sendall(out)
        return headers, body
    finally:
        tls.close()


def test_the_tcp_side_speaks_http2_and_says_where_h3_is():
    with H3Server("127.0.0.1") as server:
        headers, body = _h2_get(server, "/?v=1")
        assert headers[":status"] == "200"
        assert headers["alt-svc"] == 'h3=":%d"; ma=3600' % server.port
        assert headers["content-type"].startswith("text/html")
        assert body == PAGE
        assert server.tcp_alpn == ["h2"] and server.tcp_requests == ["/?v=1"]


def test_a_second_request_on_the_same_connection_is_answered_too():
    with H3Server("127.0.0.1") as server:
        for path in ("/?v=1", "/?v=2"):
            assert _h2_get(server, path)[1] == PAGE
        assert server.tcp_requests == ["/?v=1", "/?v=2"]


def test_a_client_that_offers_only_http1_is_answered_with_the_alt_svc_too():
    with H3Server("127.0.0.1") as server:
        tls = _tls_client(server, ["http/1.1"])
        try:
            tls.sendall(b"GET /?v=1 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            data = b""
            while True:
                chunk = tls.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            tls.close()
        assert data.startswith(b"HTTP/1.1 200 OK") and data.endswith(PAGE)
        assert ('Alt-Svc: h3=":%d"; ma=3600' % server.port).encode() in data
        assert server.tcp_alpn == ["http/1.1"]


def test_the_same_document_is_answered_over_http3_and_the_peer_is_recorded():
    from aioquic.asyncio.client import connect
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.h3.connection import H3_ALPN, H3Connection
    from aioquic.h3.events import DataReceived, HeadersReceived
    from aioquic.quic.configuration import QuicConfiguration

    class Client(QuicConnectionProtocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.http = None
            self.status, self.body = None, b""
            self.finished = asyncio.Event()

        def quic_event_received(self, event):
            if self.http is None:
                return
            for http_event in self.http.handle_event(event):
                if isinstance(http_event, HeadersReceived):
                    self.status = dict(http_event.headers).get(b":status")
                elif isinstance(http_event, DataReceived):
                    self.body += http_event.data
                    if http_event.stream_ended:
                        self.finished.set()

        async def get(self, authority, path):
            self.http = H3Connection(self._quic)
            stream = self._quic.get_next_available_stream_id()
            self.http.send_headers(stream, [(b":method", b"GET"), (b":scheme", b"https"),
                                            (b":authority", authority.encode()), (b":path", path.encode())], end_stream=True)
            self.transmit()
            await asyncio.wait_for(self.finished.wait(), 10)

    async def fetch(server):
        configuration = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, verify_mode=ssl.CERT_NONE)
        async with connect(server.address, server.port, configuration=configuration, create_protocol=Client) as client:
            await client.get("%s:%d" % (server.address, server.port), "/?v=2")
            return client.status, client.body

    with H3Server("127.0.0.1") as server:
        status, body = asyncio.run(fetch(server))
        assert status == b"200" and body == PAGE
        assert server.quic_peers == ["127.0.0.1"] and server.h3_requests == ["/?v=2"]


# -- the sequence a retail Firefox reads, and the judge -------------------------------------------------------------------
H2 = ["h2"] * 5
FAST = [900, 300, 310, 305, 290]
PROXY_ROADS = ("socks5_coherent", "socks5_no_udp", "socks5_mismatched", "socks5_unmeasured", "http_proxy")
H2_ROADS = ("socks5_no_udp", "socks5_mismatched", "socks5_unmeasured", "http_proxy")


def test_the_number_of_visits_of_each_road():
    assert line.visits_for("direct") == 3
    for road in PROXY_ROADS:
        assert line.visits_for(road) == 5, road


def test_with_no_proxy_the_sequence_is_h2_h3_h3_and_nothing_else():
    assert line.judge_visits("direct", ["h2", "h3", "h3"], [900, 300, 310]) == []
    for wrong in (["h2", "h2", "h3"], ["h2", "h2", "h2"], ["h3", "h3", "h3"], ["h2", "h3", "h2"]):
        assert line.judge_visits("direct", wrong, [900, 300, 310]), wrong


@pytest.mark.parametrize("sequence", [
    ["h2", "h2", "h3", "h3", "h3"],     # the commonest retail sequence (9 of 15)
    ["h2", "h3", "h3", "h3", "h3"],     # retail, 2 of 15
    ["h2", "h2", "h2", "h2", "h3"],     # h3 as late as the fifth visit
    ["h2", "h2", "h2", "h3", "h3"],
])
def test_behind_a_gateway_with_udp_free_h3_may_come_at_any_visit_after_the_first(sequence):
    assert line.judge_visits("socks5_coherent", sequence, FAST) == []


@pytest.mark.parametrize("sequence", [
    ["h3", "h3", "h3", "h3", "h3"],     # h3 at visit 1
    ["h3", "h2", "h3", "h3", "h3"],
    ["h2", "h2", "h2", "h2", "h2"],     # never h3 in five visits: the engine refuses h3 behind the proxy
    ["h2", "h3", "h2", "h3", "h3"],     # h3 goes back to h2
    ["h2", "h2", "h3", "h3", "h2"],
])
def test_behind_a_gateway_with_udp_free_these_sequences_are_errors(sequence):
    assert line.judge_visits("socks5_coherent", sequence, FAST), sequence


def test_h3_on_the_first_visit_is_an_error_and_says_so_on_every_road():
    assert any("visit 1 read h3" in p for p in line.judge_visits("direct", ["h3", "h3", "h3"], [900, 300, 310]))
    for road in PROXY_ROADS:
        problems = line.judge_visits(road, ["h3", "h3", "h3", "h3", "h3"], FAST)
        assert any("visit 1 read h3" in p for p in problems), (road, problems)


def test_the_judge_names_what_the_retail_reads():
    problems = line.judge_visits("socks5_coherent", H2, FAST)
    assert len(problems) == 1 and "none of the 5 visits read h3" in problems[0] and "9 of 15" in problems[0], problems
    problems = line.judge_visits("socks5_no_udp", ["h2", "h2", "h3", "h3", "h3"], FAST)
    assert any("15 of 15" in p for p in problems), problems


def test_h3_where_udp_is_dropped_is_an_error():
    for road in H2_ROADS:
        for sequence in (["h2", "h2", "h3", "h3", "h3"], ["h2", "h2", "h2", "h2", "h3"], ["h2", "h3", "h3", "h3", "h3"]):
            assert line.judge_visits(road, sequence, FAST), (road, sequence)
        assert line.judge_visits(road, H2, FAST) == [], road


def test_an_unknown_road_is_refused():
    for call in (line.expected_protocols, line.visits_for):
        with pytest.raises(KeyError):
            call("socks5_maybe")


def test_a_later_load_that_waits_for_a_fallback_is_an_error_where_udp_is_dropped_and_only_there():
    slow = [900, 900 + line.LOAD_MARGIN_MS + 1, 310, 305, 290]
    problems = line.judge_visits("socks5_no_udp", H2, slow)
    assert len(problems) == 1 and "visit 2 loaded" in problems[0], problems
    assert line.judge_visits("socks5_no_udp", H2, [900, 900 + line.LOAD_MARGIN_MS, 310, 305, 290]) == []
    for number in (3, 4, 5):             # every later visit is judged, not only the second
        walls = list(FAST)
        walls[number - 1] = 900 + line.LOAD_MARGIN_MS + 1
        problems = line.judge_visits("http_proxy", H2, walls)
        assert len(problems) == 1 and "visit %d loaded" % number in problems[0], (number, problems)
    # where UDP is free the loads are not judged (an h3 handshake has its own cost)
    assert line.judge_visits("socks5_coherent", ["h2", "h2", "h3", "h3", "h3"], slow) == []
    assert line.judge_visits("direct", ["h2", "h3", "h3"], [900, 900 + line.LOAD_MARGIN_MS + 1, 310]) == []


def test_a_series_of_the_wrong_length_is_an_error():
    assert line.judge_visits("direct", ["h2", "h3"], [900, 300])
    assert line.judge_visits("direct", ["h2", "h3", "h3"], [900, 300])
    assert line.judge_visits("socks5_coherent", ["h2", "h2", "h3"], [900, 300, 310])
    assert line.judge_visits("socks5_coherent", ["h2", "h2", "h3", "h3", "h3"], [900, 300, 310])
    assert line.judge_visits("socks5_no_udp", ["h2", "h2", "h2"], [900, 300, 310])


# ------------------------------------------------------------------------------------------ the first visit is h2 by fixture
# The retail reads h3 at the first visit when the DNS answer carries the HTTPS record (type 65): the third retail run did in 3
# sessions of 15. The e2e expects h2 first only because the fixture has no such record.
def test_a_page_reached_by_an_address_has_no_https_record_to_wait_for():
    assert line.no_https_record("192.0.2.1") == []
    assert line.no_https_record("[2001:db8::1]") == []


def test_a_page_reached_by_a_name_may_have_one_and_the_check_says_so():
    problems = line.no_https_record("site.test")
    assert problems and "HTTPS record" in problems[0]


def test_the_fake_doh_never_serves_a_type_65_with_data_and_the_check_reads_what_it_served():
    import _doh_servers as doh_servers

    server = doh_servers.FakeDoh({"site.test": "192.0.2.1"})
    try:
        query = doh_servers.parse_query(doh_servers.encode_query("site.test", doh_servers.QTYPE_HTTPS))
        message = doh_servers.answer_for({"site.test": "192.0.2.1"}, query)
        assert int.from_bytes(message[6:8], "big") == 0, "a type 65 answer with data: the h2-first expectation no longer holds"
        assert server.https_served == []
        assert line.no_https_record("192.0.2.1", server) == []
        server.https_served.append("site.test")        # a FakeDoh that learned to serve the record
        problems = line.no_https_record("192.0.2.1", server)
        assert problems and "site.test" in problems[0]
    finally:
        server.close()


# -- the relay of the gateway: a send that fails is said, and a relay that leaves from the host's address works ----------------
# The e2e once read "the server saw QUIC from []" on Windows: the relay sent from 127.0.0.1 to an address of its own host (WinError 10051),
# the send ran in a timer that ignored every OSError, and the relay had still logged the datagram as carried.
def _associate(gateway):
    """A SOCKS5 UDP ASSOCIATE on ``gateway`` with no credentials: the control connection (kept open) and the relay's address."""
    control = socket.create_connection(("127.0.0.1", gateway.port), timeout=5)
    control.sendall(b"\x05\x01\x00")
    assert control.recv(2) == b"\x05\x00"
    control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
    reply = b""
    while len(reply) < 10:
        reply += control.recv(10 - len(reply))
    assert reply[1] == 0, "the association was refused: %r" % reply
    return control, (socket.inet_ntoa(reply[4:8]), int.from_bytes(reply[8:10], "big"))


def _wrapped(host, port, payload):
    return b"\x00\x00\x00\x01" + socket.inet_aton(host) + port.to_bytes(2, "big") + payload


def test_a_send_the_relay_cannot_make_is_recorded_and_the_report_of_the_e2e_says_it():
    import test_http3_through_the_proxy_e2e as e2e
    from _socks_servers import coherent

    with coherent() as gateway:
        assert gateway.send_errors == []

        def refused():
            raise OSError(10051, "A socket operation was attempted to an unreachable network")

        gateway._later(refused, "the datagram to 203.0.113.9:443")
        assert len(gateway.send_errors) == 1 and gateway.send_errors[0].startswith("the datagram to 203.0.113.9:443: OSError")
        class Server:
            tcp_alpn, quic_peers = ["h2"], []
        said = e2e._report("socks5_coherent", ["h2"], [1], Server, gateway)
        assert "sends of the relay that failed" in said and "10051" in said


def test_a_relay_that_leaves_from_the_address_of_the_host_reaches_a_server_on_it_and_is_told_by_its_port():
    from _host_address import this_host_address
    from _socks_servers import coherent

    host = this_host_address()
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind((host, 0))
    server.settimeout(5)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind((host, 0))                                  # an engine's UDP sockets are bound to an interface
    with coherent(relay_host=host, exit_host=host) as gateway:
        gateway.forward_only_to = {host}
        control, relay = _associate(gateway)
        try:
            client.sendto(_wrapped(host, server.getsockname()[1], b"quic" * 300), relay)
            data, peer = server.recvfrom(4096)
            assert data == b"quic" * 300
            assert peer[0] == host and peer[1] in gateway.exit_ports, "the relay's port is not in exit_ports: %s" % (gateway.exit_ports,)
            direct = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            direct.bind((host, 0))
            direct.sendto(b"direct", server.getsockname())
            data, peer = server.recvfrom(4096)
            assert data == b"direct" and peer[1] not in gateway.exit_ports, "a client that came direct looks like the relay"
            direct.close()
            # the browser's own QUIC to a public address is recorded as carried and goes nowhere: the lab stays closed
            client.sendto(_wrapped("203.0.113.9", 443, b"background"), relay)
            time.sleep(0.3)
            assert ("203.0.113.9", 443) in {(h, p) for h, p, _d in gateway.datagrams}
            assert gateway.send_errors == [], gateway.send_errors
        finally:
            control.close()
            client.close()
            server.close()


def test_the_fake_stun_of_a_relay_that_leaves_from_the_address_of_the_host_answers_through_the_relay():
    from _host_address import this_host_address
    from _socks_servers import MEASURE_NUMBER, STUN_PORT, coherent

    host = this_host_address()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind((host, 0))
    client.settimeout(5)
    with coherent(relay_host=host, exit_host=host) as gateway:
        control, relay = _associate(gateway)
        try:
            request = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + b"txid-of-12by"
            client.sendto(_wrapped(MEASURE_NUMBER, STUN_PORT, request), relay)
            answer, _peer = client.recvfrom(4096)
            assert answer[3] == 1 and socket.inet_ntoa(answer[4:8]) == MEASURE_NUMBER and int.from_bytes(answer[8:10], "big") == STUN_PORT
            assert answer[10:12] == b"\x01\x01", "not a STUN Binding success: %r" % answer[10:20]
            assert gateway.send_errors == [], gateway.send_errors
        finally:
            control.close()
            client.close()


# -- the port the server takes -------------------------------------------------------------------------------------------
# The known-bad case, seen on the Windows runner (run 43230437094, wrapper.e2e.web: three tests red with "no port free for both TCP
# and UDP", and on a work machine twice): Windows hands out ephemeral TCP ports in sequence and keeps excluded ranges of a hundred
# ports for UDP in the same zone, so the old loop (fifty times "the system's pick") got fifty consecutive numbers inside one
# refused range and gave up. A fake socket layer does the same here, without the machine.
_REFUSED_UDP = range(50000, 50200)


class _FakeTcpServer:
    """A TCP server whose port 0 is the next number of a sequence (50000, 50001, ...), as Windows picks."""

    next_pick = 50000
    opened: list = []

    def __init__(self, where, handler):
        address, port = where
        if port == 0:
            port = _FakeTcpServer.next_pick
            _FakeTcpServer.next_pick += 1
        self.server_address = (address, port)
        self.closed = False
        _FakeTcpServer.opened.append(self)

    def server_close(self):
        self.closed = True


class _FakeUdpSocket:
    tried: list = []

    def __init__(self, family, kind):
        pass

    def bind(self, where):
        _FakeUdpSocket.tried.append(where[1])
        if where[1] in _REFUSED_UDP or _FakeUdpSocket.refuse_all:
            raise PermissionError(10013, "An attempt was made to access a socket in a way forbidden by its access permissions")

    refuse_all = False

    def close(self):
        pass


def _fake_layer(monkeypatch, refuse_all=False):
    import types

    import _h3_server

    _FakeTcpServer.next_pick, _FakeTcpServer.opened = 50000, []
    _FakeUdpSocket.tried, _FakeUdpSocket.refuse_all = [], refuse_all
    monkeypatch.setattr(_h3_server, "_TcpServer", _FakeTcpServer)
    monkeypatch.setattr(_h3_server, "socket", types.SimpleNamespace(
        AF_INET=socket.AF_INET, SOCK_DGRAM=socket.SOCK_DGRAM, socket=_FakeUdpSocket))
    return _h3_server


def test_the_server_takes_a_number_both_tcp_and_udp_bind_outside_a_refused_udp_range(monkeypatch):
    module = _fake_layer(monkeypatch)
    server, port = module._tcp_server_on_a_port_udp_binds("127.0.0.1")
    assert port not in _REFUSED_UDP, "the server took a number UDP refuses: %d" % port
    assert server.server_address[1] == port
    assert _FakeUdpSocket.tried[0] in _REFUSED_UDP, "the fake did not make the system's first pick a refused one"
    assert all(s.closed for s in _FakeTcpServer.opened if s is not server), "a TCP server of a refused number was left open"


def test_a_server_that_can_bind_nothing_says_so_after_a_bounded_number_of_tries(monkeypatch):
    module = _fake_layer(monkeypatch, refuse_all=True)
    with pytest.raises(RuntimeError) as raised:
        module._tcp_server_on_a_port_udp_binds("127.0.0.1")
    assert "both TCP and UDP" in str(raised.value) and "127.0.0.1" in str(raised.value), str(raised.value)
    import _ports

    assert len(_FakeUdpSocket.tried) == _ports.TRIES, len(_FakeUdpSocket.tried)


def test_the_server_closes_its_event_loop_on_its_own_thread_when_it_closes():
    """A loop that is only stopped is closed later by BaseEventLoop.__del__ on whatever thread the garbage collector runs in, and on
    Windows the proactor's close then waits for the UDP receive still pending on it, for ever (Windows hunt of 13/11, run
    43377799126: the HTTP/3 e2e hung inside a later goto, the test thread in base_events.__del__ -> proactor close -> _poll).
    Known-bad: the server before this change stops the loop and never closes it."""
    with H3Server("127.0.0.1") as server:
        loop, thread = server._loop, server._thread
    assert not thread.is_alive(), "the server's thread is still running after close()"
    assert loop.is_closed(), "the server's event loop was left for the garbage collector to close"
