"""The witness of the loss e2e (``_witness.py``) and the opt-in source binding of the test gateway: both are what lets a test tell a direct
send from a drop, so both are checked without a browser.

Hermetic and portable: the sources are 127.0.0.2 and 127.0.0.3 (any 127/8 address binds on Linux and on Windows), no root needed.
"""
from __future__ import annotations

import socket
import struct
import time

import pytest

from _socks_servers import Gateway
from _witness import Witness

pytestmark = pytest.mark.unit


def _until(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_the_witness_writes_down_the_source_of_a_datagram_and_answers_a_binding_request():
    with Witness("127.0.0.1") as w:
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.bind(("127.0.0.2", 0))
        c.settimeout(3)
        request = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + b"\x07" * 12
        c.sendto(request, ("127.0.0.1", w.port))
        answer, _ = c.recvfrom(2048)
        assert answer[0:2] == b"\x01\x01" and answer[8:20] == b"\x07" * 12
        # the mapped address is the source the witness saw
        xport, = struct.unpack("!H", answer[26:28])
        assert (xport ^ 0x2112) == c.getsockname()[1]
        assert [h[0] for h in w.heard()["udp"]] == ["127.0.0.2"]
        c.close()


def test_the_witness_writes_down_the_source_of_a_connection_and_separates_the_exit_from_the_rest():
    with Witness("127.0.0.1") as w:
        for source in ("127.0.0.2", "127.0.0.3"):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((source, 0))
            s.connect(("127.0.0.1", w.port))
            s.close()
        assert _until(lambda: len(w.heard()["tcp"]) == 2)
        assert sorted(h[0] for h in w.heard()["tcp"]) == ["127.0.0.2", "127.0.0.3"]
        other = w.heard_from_other_than(["127.0.0.2"])
        assert [h[0] for h in other["tcp"]] == ["127.0.0.3"] and other["udp"] == []


def test_a_gateway_whose_tcp_leaves_from_its_exit_is_told_apart_from_a_client_that_connects_direct():
    with Witness("127.0.0.1") as w:
        gateway = Gateway(udp="refuse", tcp_exit="203.0.113.7", udp_exit="203.0.113.7", exit_host="127.0.0.2", tcp_from_exit=True)
        try:
            c = socket.create_connection(("127.0.0.1", gateway.port), timeout=3)
            c.sendall(b"\x05\x01\x00")
            assert c.recv(2) == b"\x05\x00"
            c.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", w.port))
            assert c.recv(10)[1] == 0                                # the CONNECT succeeded
            c.close()
            assert _until(lambda: len(w.heard()["tcp"]) == 1)
            assert [h[0] for h in w.heard()["tcp"]] == ["127.0.0.2"], "the connection of the gateway did not leave from its exit"
        finally:
            gateway.close()
