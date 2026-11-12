"""The witness binds TCP and UDP on one port number even where the system's picks fall, one after the other, in a range UDP may not bind.

The known-bad case, seen on a Windows work machine: ``Witness("127.0.0.1")`` failed with WinError 10013 in three unit tests, while the
push before had passed them. Windows hands out ephemeral TCP ports in sequence and keeps excluded ranges of a hundred ports for UDP in
the same zone; the old witness asked the system for a TCP port twenty times, got twenty consecutive numbers inside one UDP range, and
gave up. Here a fake socket layer does the same, without the machine: the system's TCP picks are 50000, 50001, ... and UDP refuses
50000-50199. The old witness is red on it (20 refusals), this one finds a number outside the range. No browser, no real socket.
"""
from __future__ import annotations

import types

import pytest

import _witness

pytestmark = pytest.mark.unit

REFUSED = range(50000, 50200)


class _Layer:
    """A socket module whose TCP picks are sequential and whose UDP refuses REFUSED (or everything, with ``refuse_all``)."""

    AF_INET, SOCK_STREAM, SOCK_DGRAM, SOL_SOCKET, SO_REUSEADDR = 2, 1, 2, 1, 2

    def __init__(self, refuse_all=False):
        self.next_pick = 50000
        self.refuse_all = refuse_all
        self.udp_tries = []
        self.inet_aton = _witness.socket.inet_aton

    def socket(self, family, kind):
        return _Socket(self, kind)


class _Socket:
    def __init__(self, layer, kind):
        self.layer, self.kind, self.port = layer, kind, None

    def setsockopt(self, *args):
        pass

    def bind(self, where):
        address, port = where
        if self.kind == _Layer.SOCK_STREAM:
            if port == 0:
                port = self.layer.next_pick
                self.layer.next_pick += 1
            self.port = port
            return
        self.layer.udp_tries.append(port)
        if self.layer.refuse_all or port in REFUSED:
            raise PermissionError(10013, "An attempt was made to access a socket in a way forbidden by its access permissions")
        self.port = port

    def getsockname(self):
        return ("127.0.0.1", self.port)

    def listen(self, backlog):
        pass

    def recvfrom(self, size):
        raise OSError("closed")

    def accept(self):
        raise OSError("closed")

    def close(self):
        pass


def test_the_witness_binds_both_on_one_number_outside_a_refused_udp_range(monkeypatch):
    layer = _Layer()
    monkeypatch.setattr(_witness, "socket", types.SimpleNamespace(**{k: getattr(layer, k) for k in (
        "AF_INET", "SOCK_STREAM", "SOCK_DGRAM", "SOL_SOCKET", "SO_REUSEADDR", "inet_aton", "socket")}))
    w = _witness.Witness("127.0.0.1")
    try:
        assert w.port not in REFUSED, "the witness took a number UDP refuses: %d" % w.port
        assert w._tcp.port == w._udp.port == w.port, "TCP and UDP are not on one number"
        assert layer.udp_tries[0] in REFUSED, "the fake did not make the system's first pick a refused one: %r" % layer.udp_tries[:3]
    finally:
        w.close()


def test_a_witness_that_can_bind_nothing_says_so_after_a_bounded_number_of_tries(monkeypatch):
    layer = _Layer(refuse_all=True)
    monkeypatch.setattr(_witness, "socket", types.SimpleNamespace(**{k: getattr(layer, k) for k in (
        "AF_INET", "SOCK_STREAM", "SOCK_DGRAM", "SOL_SOCKET", "SO_REUSEADDR", "inet_aton", "socket")}))
    with pytest.raises(OSError) as raised:
        _witness.Witness("127.0.0.1")
    assert "both TCP and UDP" in str(raised.value) and "127.0.0.1" in str(raised.value), str(raised.value)
    assert len(layer.udp_tries) == _witness.TRIES, "the witness tried %d times, not %d" % (len(layer.udp_tries), _witness.TRIES)


def test_a_port_that_is_asked_for_is_tried_once_and_its_refusal_raised(monkeypatch):
    layer = _Layer()
    monkeypatch.setattr(_witness, "socket", types.SimpleNamespace(**{k: getattr(layer, k) for k in (
        "AF_INET", "SOCK_STREAM", "SOCK_DGRAM", "SOL_SOCKET", "SO_REUSEADDR", "inet_aton", "socket")}))
    with pytest.raises(PermissionError):
        _witness.Witness("127.0.0.1", 50050)
    assert layer.udp_tries == [50050]
