"""The hermetic road of test_the_whole_udp_chain_by_name_e2e is itself a working road: a plain client walks it by hand
(no browser), the way a browser behind a proxy must: SOCKS5 UDP ASSOCIATE, the NAME of the TURN server asked of the DoH
through a SOCKS5 CONNECT (the answer is an address), then an Allocate sent to that ADDRESS through the relay, answered 401
and, with the credentials, with an answer signed as a real TURN server signs it, and the source the TURN server saw is the
gateway's exit and not the client. A name sent along the relay (address type 3) or a DNS query to port 53 gets no answer, and
a CONNECT that carries the page's name is refused. If this helper were wrong, the e2e could never go green; this says it can."""
import hashlib
import hmac
import socket
import ssl
import struct
import time

import pytest

import _turn_chain as c
from _doh_servers import DOH_BOOTSTRAP, DOH_HOST, DOH_PATH, DOH_PORT, QTYPE_A, QTYPE_AAAA, encode_query

pytestmark = pytest.mark.unit


def _socks_hello(chain):
    t = socket.create_connection(("127.0.0.1", chain.port), timeout=5)
    t.sendall(b"\x05\x01\x00")
    assert t.recv(2) == b"\x05\x00"
    return t


def _socks_associate(chain):
    t = _socks_hello(chain)
    t.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
    rep = t.recv(10)
    assert rep[1] == 0
    return t, (socket.inet_ntoa(rep[4:8]), struct.unpack("!H", rep[8:10])[0])


def _socks_connect(chain, host, port, by_name=False):
    """(socket, reply code) of a CONNECT by address, or by name (address type 3)."""
    t = _socks_hello(chain)
    target = (b"\x03" + bytes([len(host)]) + host.encode()) if by_name else (b"\x01" + socket.inet_aton(host))
    t.sendall(b"\x05\x01\x00" + target + struct.pack("!H", port))
    rep = b""
    while len(rep) < 10:
        chunk = t.recv(10 - len(rep))
        if not chunk:
            break
        rep += chunk
    return t, (rep[1] if len(rep) > 1 else None)


def _via(relay_sock, relay, host_ip, port, payload):
    relay_sock.sendto(b"\x00\x00\x00\x01" + socket.inet_aton(host_ip) + struct.pack("!H", port) + payload, relay)
    data, _ = relay_sock.recvfrom(65536)
    return data[10:]


def _http_over_tls(sock, chain, method, target, body=b"", content_type=None, host=DOH_HOST):
    """One HTTP/1.1 request over TLS on a tunnel, trusting the certificate of the chain's DoH."""
    context = ssl.create_default_context(cafile=str(chain.doh.certificate_path))
    context.set_alpn_protocols(["http/1.1"])
    tls = context.wrap_socket(sock, server_hostname=host)
    head = "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\nContent-Length: %d\r\n" % (method, target, host, len(body))
    if content_type:
        head += "Content-Type: %s\r\n" % content_type
    tls.sendall(head.encode() + b"\r\n" + body)
    data = b""
    while True:
        chunk = tls.recv(65536)
        if not chunk:
            break
        data += chunk
    tls.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), payload


def _resolve_through_doh(chain, name, qtype=QTYPE_A, target=DOH_BOOTSTRAP):
    """What a browser does for a name: a CONNECT to the DoH through the gateway, then the query (RFC 8484 POST)."""
    t, code = _socks_connect(chain, target, DOH_PORT, by_name=target == DOH_HOST)
    assert code == 0
    status, payload = _http_over_tls(t, chain, "POST", DOH_PATH, encode_query(name, qtype), "application/dns-message")
    assert status == 200
    return payload


def test_a_client_walks_the_whole_road_and_the_turn_server_sees_the_gateways_exit():
    with c.Chain(rtt_ms=20, relay_host="127.0.0.1") as chain:
        # the name of the TURN server is asked of the DoH, through a CONNECT: the answer is an address
        answer = _resolve_through_doh(chain, c.TURN_NAME)
        assert socket.inet_ntoa(answer[-4:]) == c.TURN_IP
        assert [(q.name, q.qtype, q.via) for q in chain.doh.queries] == [(c.TURN_NAME, QTYPE_A, "chain")]
        assert chain.doh_connects() == [(DOH_BOOTSTRAP, DOH_PORT)]
        # the UDP relay of the gateway, and an Allocate to the ADDRESS the DoH gave
        t, relay = _socks_associate(chain)
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(5)
        # an Allocate without credentials: 401 with a realm and a nonce
        tid = b"\x01" * 12
        alloc = struct.pack("!HHI", 0x0003, 8, c.MAGIC) + tid + struct.pack("!HH", 0x0019, 4) + b"\x11\x00\x00\x00"
        r1 = _via(u, relay, c.TURN_IP, c.TURN_PORT, alloc)
        assert struct.unpack("!H", r1[:2])[0] == 0x0113
        attrs = c._attrs(r1)
        assert attrs[0x0014] == c.REALM.encode() and attrs[0x0015] == c.NONCE.encode()
        # with the credentials: a success signed with the long-term key
        user = b"challenge-1"
        key = hashlib.md5(b"challenge-1:" + c.REALM.encode() + b":" + c.PASSWORD.encode()).digest()
        body = (c._attr(0x0019, b"\x11\x00\x00\x00") + c._attr(0x0006, user) + c._attr(0x0014, c.REALM.encode()) +
                c._attr(0x0015, c.NONCE.encode()))
        req = c._message(0x0003, b"\x02" * 12, body, key)
        r2 = _via(u, relay, c.TURN_IP, c.TURN_PORT, req)
        assert struct.unpack("!H", r2[:2])[0] == 0x0103
        # the signature of the answer is the one a client with the same credentials computes
        mi_at = r2.index(struct.pack("!HH", 0x0008, 20))
        head = struct.pack("!H", 0x0103) + struct.pack("!H", mi_at - 20 + 24) + r2[4:20]
        assert hmac.new(key, head + r2[20:mi_at], hashlib.sha1).digest() == r2[mi_at + 4:mi_at + 24]
        mapped = c._attrs(r2)[0x0020]
        port = struct.unpack("!H", mapped[2:4])[0] ^ (c.MAGIC >> 16)
        ip = socket.inet_ntoa(struct.pack("!I", struct.unpack("!I", mapped[4:8])[0] ^ c.MAGIC))
        # the XOR-MAPPED-ADDRESS is the source the TURN server saw: the gateway's exit, not this client
        assert ip == c.EXIT_HOST and (ip, port) in [(a[0], a[1]) for a in chain.turn.allocations]
        assert {s[0] for s in chain.turn.seen} == {c.EXIT_HOST}
        # nothing was carried by name or to port 53: the only datagrams are the two Allocates, by address
        assert [(h, p, a) for h, p, a, _t in chain.datagram_log] == [(c.TURN_IP, c.TURN_PORT, 1)] * 2
        assert chain.refused == []
        u.close()
        t.close()


def test_the_page_name_is_asked_of_the_doh_and_the_page_is_reached_by_its_address():
    with c.Chain(rtt_ms=20, relay_host="127.0.0.1") as chain:
        answer = _resolve_through_doh(chain, c.PAGE_NAME, target=DOH_HOST)       # the DoH host may be sent by name too
        assert socket.inet_ntoa(answer[-4:]) == "127.0.0.1"
        # an AAAA question for a known name: the name exists, nothing of that type
        assert _resolve_through_doh(chain, c.PAGE_NAME, QTYPE_AAAA)[6:8] == b"\x00\x00"
        t, code = _socks_connect(chain, "127.0.0.1", chain.page.port)
        assert code == 0
        # the page is plain HTTP: ask it for itself, with the Host a browser sends
        t.sendall(("GET / HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n" % (c.PAGE_NAME, chain.page.port)).encode())
        data = b""
        while True:
            chunk = t.recv(65536)
            if not chunk:
                break
            data += chunk
        t.close()
        assert b"RTCPeerConnection" in data and chain.page.hits == [("%s:%d" % (c.PAGE_NAME, chain.page.port), "/")]
        assert ("127.0.0.1", chain.page.port) in chain.connects
        assert chain.doh.vias_of(c.PAGE_NAME) == {"chain"}


def test_the_gateway_resolves_nothing_a_name_on_the_relay_a_query_to_port_53_and_a_connect_by_name_get_no_answer():
    with c.Chain(rtt_ms=0, relay_host="127.0.0.1") as chain:
        t, relay = _socks_associate(chain)
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(0.6)
        alloc = struct.pack("!HHI", 0x0003, 8, c.MAGIC) + b"\x03" * 12 + struct.pack("!HH", 0x0019, 4) + b"\x11\x00\x00\x00"
        name = c.TURN_NAME.encode()
        # the TURN server written as a NAME (address type 3) is not resolved by the gateway
        u.sendto(b"\x00\x00\x00\x03" + bytes([len(name)]) + name + struct.pack("!H", c.TURN_PORT) + alloc, relay)
        # nor a DNS query to port 53, by address
        question = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"".join(
            bytes([len(p)]) + p.encode() for p in c.TURN_NAME.split(".")) + b"\x00\x00\x01\x00\x01"
        u.sendto(b"\x00\x00\x00\x01" + socket.inet_aton("192.0.2.53") + struct.pack("!H", 53) + question, relay)
        with pytest.raises(socket.timeout):
            u.recvfrom(65536)
        # a CONNECT that carries the page's name is refused, not resolved, and listed
        s, code = _socks_connect(chain, c.PAGE_NAME, chain.page.port, by_name=True)
        s.close()
        assert code == 4
        u.close()
        t.close()
        time.sleep(0.1)
        assert chain.turn.seen == [] and chain.doh.queries == []
        assert [(h, p, a) for h, p, a, _t in chain.datagram_log] == [(c.TURN_NAME, c.TURN_PORT, 3), ("192.0.2.53", 53, 1)]
        assert chain.refused == [(c.PAGE_NAME, chain.page.port)]
        # and the explanation says why the allocation could not have been asked for
        why = chain.explain({"status": "TIMEOUT"}).splitlines()[-1]
        assert "went along the UDP relay" in why and c.TURN_NAME in why


def test_a_silent_browser_is_explained_by_what_each_server_saw():
    with c.Chain(rtt_ms=0, relay_host="127.0.0.1") as chain:
        text = chain.explain({"status": "TIMEOUT"})
        assert "never asked the DoH" in text.splitlines()[-1]
        for server in ("DoH queries", "gateway: CONNECTs", "TURN server heard 0", "page server"):
            assert server in text
        _resolve_through_doh(chain, c.TURN_NAME)
        assert "no datagram reached the TURN server" in chain.explain({"status": "TIMEOUT"}).splitlines()[-1]


def test_every_step_of_the_gateway_costs_its_round_trip():
    with c.Chain(rtt_ms=130, relay_host="127.0.0.1") as chain:
        began = time.monotonic()
        t, _relay = _socks_associate(chain)
        took = time.monotonic() - began
        t.close()
    assert 0.25 <= took < 1.5, took          # the greeting and the request each wait one round trip
    assert chain.associations and chain.associations[0][1] is not None


def test_the_doh_road_is_as_far_as_the_gateway():
    with c.Chain(rtt_ms=130, relay_host="127.0.0.1") as chain:
        began = time.monotonic()
        _resolve_through_doh(chain, c.TURN_NAME)
        took = time.monotonic() - began
    # greeting + CONNECT (2 round trips) and at least the TLS flights and the query, each chunk waiting half a round trip
    assert took >= 0.5, took
