"""The address of this host that the relay of an e2e test binds to is computed by ONE helper, and a host with no default
route (a container or a CI runner without a network, a laptop offline) SKIPS the test instead of erroring, before any
server of the test has started (it used to raise OSError in the middle of the construction of the chain, after the TURN
server, the resolver and the page were listening)."""
import types

import pytest

import _host_address
import _turn_chain

pytestmark = pytest.mark.unit


class _Probe:
    """A UDP socket of a host whose routing table is ``address`` (a string), or empty (None)."""
    def __init__(self, address):
        self.address, self.closed = address, False

    def connect(self, _target):
        if self.address is None:
            raise OSError(10051, "Network is unreachable")

    def getsockname(self):
        return (self.address, 40000)

    def close(self):
        self.closed = True


def _host_with(monkeypatch, address):
    probes = []

    def make(*_args):
        probes.append(_Probe(address))
        return probes[-1]
    monkeypatch.setattr(_host_address, "socket", types.SimpleNamespace(socket=make, AF_INET=2, SOCK_DGRAM=2))
    return probes


def test_a_host_with_a_default_route_gives_the_address_it_sends_from(monkeypatch):
    probes = _host_with(monkeypatch, "192.168.7.20")
    assert _host_address.this_host_address() == "192.168.7.20"
    assert all(p.closed for p in probes)


def test_a_host_with_no_default_route_skips_the_test(monkeypatch):
    probes = _host_with(monkeypatch, None)
    with pytest.raises(pytest.skip.Exception, match="no default route"):
        _host_address.this_host_address()
    assert all(p.closed for p in probes)


def test_the_chain_asks_the_address_before_it_starts_any_server(monkeypatch):
    started = []
    for name in ("TurnServer", "FakeDoh", "Page"):
        monkeypatch.setattr(_turn_chain, name, lambda *a, _n=name, **k: started.append(_n))
    _host_with(monkeypatch, None)
    monkeypatch.setattr(_turn_chain, "this_host_address", _host_address.this_host_address)
    with pytest.raises(pytest.skip.Exception, match="no default route"):
        _turn_chain.Chain()
    assert started == [], "a server was started before the address of this host was known: %s" % started


def test_the_chain_with_a_relay_host_of_its_own_does_not_ask_for_the_address(monkeypatch):
    def boom():
        raise AssertionError("asked")
    monkeypatch.setattr(_turn_chain, "this_host_address", boom)
    chain = _turn_chain.Chain(relay_host="127.0.0.1")
    try:
        assert chain.relay_host == "127.0.0.1"
    finally:
        chain.close()


def test_there_is_one_copy_of_the_helper():
    import pathlib
    here = pathlib.Path(__file__).parent
    for name in ("_turn_chain.py", "test_http3_through_the_proxy_e2e.py", "test_webrtc_realness.py"):
        text = (here / name).read_text(encoding="utf-8")
        assert '"192.0.2.1"' not in text, "%s has its own copy of the probe of the default route" % name
