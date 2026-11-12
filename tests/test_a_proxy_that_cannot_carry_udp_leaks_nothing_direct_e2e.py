"""A LOSS IS NOT A DROP: behind a proxy that cannot carry the UDP of a PeerConnection, nothing of the browser reaches a server direct.

The fail-closed rule of the UDP line is "the UDP of a PeerConnection behind a proxy that carries none goes NOWHERE: not to the relay, and
never direct, around the proxy". The e2e that check "no srflx" cannot tell the rule from a leak: in a laboratory whose servers sit at
documentation addresses nothing routes, a datagram sent direct arrives nowhere, exactly as a dropped one. Here the STUN server and the TCP
TURN listener of the page (a WITNESS, ``_witness.py``) stand at an address the browser CAN reach directly (a documentation address held
on ``lo``: root, Linux, the suite ``wrapper.e2e.udp.exit``), and the gateways leave from ANOTHER such address, the exit. So:

  * in each fail-closed scenario - a proxy that refuses UDP, one whose UDP leaves from another address than its TCP, one whose relay never
    answers, an HTTP proxy - the witness hears NO datagram from anyone and NO connection from a source that is not the exit; a send direct
    fails this test;
  * the controls, which are what makes the green mean something: the same page with NO proxy is heard by the witness (the laboratory does
    see a direct send), and behind a coherent gateway the witness hears the datagrams from the EXIT only (the relay is seen too).
"""
from __future__ import annotations

import pytest

import _turn_chain as chain_mod
from _doh_servers import FakeDoh
from _host_address import this_host_address
from _http_proxy import HttpProxy
from _socks_servers import EXIT, OTHER_EXIT, Gateway
from _udp_line import declared_udp, egress_by_proxy, measure_servers
from _witness import Witness
from test_webrtc_realness import _e2e_binary, _launch, _launch_behind  # noqa: F401

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

#: Where the witness stands: a documentation address (RFC 5737, TEST-NET-2) held on lo, reachable by the browser directly.
WITNESS_ADDRESS = "198.51.100.10"
#: The exit of the gateways (the address their datagrams and connections leave from): another documentation address on lo.
EXIT_ADDRESS = chain_mod.LAB_EXIT
#: How long the page waits after the offer: the first retransmissions of STUN and TURN (0, 0.1, 0.3, 0.7, 1.5, 3.1 s) have all been sent.
WAIT_MS = 5000

_PROBE_JS = """async (target) => {
  const pc = new RTCPeerConnection({iceServers: [
    {urls: ['stun:' + target]},
    {urls: ['turn:' + target + '?transport=udp'], username: 'u', credential: 'p'},
    {urls: ['turn:' + target + '?transport=tcp'], username: 'u', credential: 'p'}]});
  pc.createDataChannel('x');
  const found = [];
  pc.onicecandidate = (e) => { if (e.candidate) found.push(e.candidate.candidate); };
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((resolve) => setTimeout(resolve, %d));
  return found;
}""" % WAIT_MS


@pytest.fixture
def witness():
    with chain_mod.lab_exit_address(EXIT_ADDRESS), chain_mod.lab_exit_address(WITNESS_ADDRESS):
        w = Witness(WITNESS_ADDRESS)
        try:
            yield w
        finally:
            w.close()


def _gateway(**kw) -> Gateway:
    """A SOCKS5 gateway whose datagrams and connections leave from the exit address, with a relay on this host's address."""
    kw.setdefault("tcp_exit", EXIT)
    kw.setdefault("udp_exit", EXIT)
    return Gateway(relay_host=this_host_address(), exit_host=EXIT_ADDRESS, tcp_from_exit=True, **kw)


def _gather(page_launcher, w: Witness):
    with page_launcher() as browser:
        page = browser.new_context().new_page()
        page.goto("about:blank")
        return page.evaluate(_PROBE_JS, w.target)


# (id, how the gateway is made, what the core must have declared for its UDP)
SOCKS_SCENARIOS = [
    ("the_proxy_refuses_udp", lambda: _gateway(udp="refuse"), "no"),
    ("the_udp_of_the_proxy_leaves_from_another_address", lambda: _gateway(udp="relay", udp_exit=OTHER_EXIT), "no"),
    ("the_relay_never_answers", lambda: _gateway(udp="relay", stun_answers=False), "unmeasured"),
]


@pytest.mark.parametrize("make,declared", [(m, d) for _i, m, d in SOCKS_SCENARIOS], ids=[i for i, _m, _d in SOCKS_SCENARIOS])
def test_behind_a_socks5_proxy_that_cannot_carry_the_udp_nothing_is_sent_direct(monkeypatch, witness, make, declared):
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo
    gateway = make()
    try:
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(gateway))
        found = _gather(lambda: _launch_behind(gateway, binary_path=binary), witness)
        assert declared_udp(gateway.proxy, measure_servers(gateway)) == declared, "the scenario is not the one it says"
    finally:
        gateway.close()
    leaked = witness.heard_from_other_than([EXIT_ADDRESS])
    assert not witness.heard()["udp"], (
        "the witness heard a datagram behind a proxy that carries no UDP: %s (the exit is %s)" % (witness.heard()["udp"], EXIT_ADDRESS))
    assert not leaked["tcp"], "a connection to the witness did not come from the proxy's exit: %s" % (leaked["tcp"],)
    assert not [c for c in found if " typ srflx " in c or " typ relay " in c], "a candidate that nothing allocated: %s" % (found,)


def test_behind_an_http_proxy_nothing_is_sent_direct(monkeypatch, witness):
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo
    doh = FakeDoh({})
    proxy = HttpProxy("http-proxy", doh)
    try:
        monkeypatch.setattr(_geo, "discover_egress_ip", lambda *a, **k: EXIT)
        found = _gather(lambda: _launch(proxy=proxy.proxy, binary_path=binary), witness)
    finally:
        proxy.close()
        doh.close()
    assert not witness.heard()["udp"], "the witness heard a datagram behind an HTTP proxy: %s" % (witness.heard()["udp"],)
    assert not witness.heard()["tcp"], (
        "the witness heard a connection behind an HTTP proxy that refuses it (the proxy never connected): %s" % (witness.heard()["tcp"],))
    assert not [c for c in found if " typ srflx " in c or " typ relay " in c], "a candidate that nothing allocated: %s" % (found,)


def test_the_control_with_no_proxy_the_witness_hears_the_browser_direct(monkeypatch, witness):
    """Without this the green above proves nothing: the laboratory must SEE a direct datagram and a direct connection."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo
    monkeypatch.setattr(_geo, "discover_egress_ip", lambda *a, **k: EXIT)       # the world has no network: nothing to wait for
    _gather(lambda: _launch(binary_path=binary), witness)
    heard = witness.heard()
    assert heard["udp"], "the witness heard no datagram of a browser with no proxy: the laboratory does not see a direct send"
    assert heard["tcp"], "the witness heard no connection of a browser with no proxy: the laboratory does not see a direct connection"
    assert not [h for h in heard["udp"] + heard["tcp"] if h[0] == EXIT_ADDRESS], "a direct send came from the exit of the gateways"


def test_the_control_behind_a_coherent_gateway_the_witness_hears_the_exit_only(monkeypatch, witness):
    """The relay is seen too: the datagrams and the connections of a proxy that carries UDP arrive FROM ITS EXIT, and from nowhere else."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo
    gateway = _gateway(udp="relay")
    try:
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(gateway))
        _gather(lambda: _launch_behind(gateway, binary_path=binary), witness)
        assert declared_udp(gateway.proxy, measure_servers(gateway)) == "coherent"
    finally:
        gateway.close()
    heard = witness.heard()
    assert heard["udp"], "the witness heard no datagram through a coherent relay"
    assert not witness.heard_from_other_than([EXIT_ADDRESS])["udp"], (
        "a datagram came from a source that is not the exit of the relay: %s" % (witness.heard_from_other_than([EXIT_ADDRESS])["udp"],))
    assert not witness.heard_from_other_than([EXIT_ADDRESS])["tcp"], (
        "a connection came from a source that is not the exit of the proxy: %s" % (witness.heard_from_other_than([EXIT_ADDRESS])["tcp"],))
