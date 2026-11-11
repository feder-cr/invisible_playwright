"""The hermetic road of test_the_whole_udp_chain_by_name_e2e is itself a working road: a plain client walks it by hand
(no browser): SOCKS5 UDP ASSOCIATE, the name asked of the resolver through the relay, an Allocate answered 401 then, with
the credentials, with an answer signed as a real TURN server signs it, and the source the TURN server saw is the gateway's
exit and not the client. If this helper were wrong, the e2e could never go green; this says it can."""
import hashlib
import hmac
import socket
import struct
import time

import pytest

import _turn_chain as c

pytestmark = pytest.mark.unit


def _socks_associate(chain):
    t = socket.create_connection(("127.0.0.1", chain.port), timeout=5)
    t.sendall(b"\x05\x01\x00")
    assert t.recv(2) == b"\x05\x00"
    t.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
    rep = t.recv(10)
    assert rep[1] == 0
    return t, (socket.inet_ntoa(rep[4:8]), struct.unpack("!H", rep[8:10])[0])


def _via(relay_sock, relay, host_ip, port, payload):
    relay_sock.sendto(b"\x00\x00\x00\x01" + socket.inet_aton(host_ip) + struct.pack("!H", port) + payload, relay)
    data, _ = relay_sock.recvfrom(65536)
    return data[10:]


def _dns_query(name):
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for label in name.split("."):
        q += bytes([len(label)]) + label.encode()
    return q + b"\x00\x00\x01\x00\x01"


def test_a_client_walks_the_whole_road_and_the_turn_server_sees_the_gateways_exit():
    with c.Chain(rtt_ms=20, relay_host="127.0.0.1") as chain:
        t, relay = _socks_associate(chain)
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(5)
        # the name, asked of "a resolver on the Internet" through the relay: it is the world's only resolver
        answer = _via(u, relay, "192.0.2.53", 53, _dns_query(c.TURN_NAME))
        assert socket.inet_ntoa(answer[-4:]) == c.TURN_IP
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
        t.close()


def test_every_step_of_the_gateway_costs_its_round_trip():
    with c.Chain(rtt_ms=130, relay_host="127.0.0.1") as chain:
        began = time.monotonic()
        t, _relay = _socks_associate(chain)
        took = time.monotonic() - began
        t.close()
    assert 0.25 <= took < 1.5, took          # the greeting and the request each wait one round trip
    assert chain.associations and chain.associations[0][1] is not None
