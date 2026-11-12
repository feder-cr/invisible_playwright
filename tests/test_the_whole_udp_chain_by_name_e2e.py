"""The whole chain a leak test walks, by NAME, behind a SOCKS5 gateway that carries UDP, all on loopback (hermetic).

A page asks for a TURN allocation at a server written as a NAME (`turn:turn.relay-only.test:3478`), as the real leak-test pages
do: no STUN, so a server-reflexive candidate can only be born from a successful allocation. Its logic is the logic of such a page:
a tick of 2 seconds from the start of the page that marks TIMEOUT, cleared only after a POST made when the srflx arrives; the
server compares the address of that srflx with the source the TURN server saw for the allocation.

The only road to anything is a gateway at 130 ms of round trip (every step of its control connection, every datagram, every
chunk of a tunnel): the TURN server is at an unroutable documentation address that only the gateway maps to it; the names (the
TURN's and the page's) are known only to a DoH server (a `FakeDoh`, with a certificate for `mozilla.cloudflare-dns.com`) that
only the gateway reaches, by a CONNECT; and the gateway's datagrams leave from 127.0.0.2 so that a TURN server hearing 127.0.0.1
proves the browser went around the gateway. The gateway resolves nothing: it answers no DNS query and no name sent as SOCKS5
address type 3, on the relay or in a CONNECT.

What the product must do, to be what a retail Firefox is with no proxy at all (the same candidates, in the same time):
  * the name of the ICE server is resolved by the DoH of Firefox (`network.trr.mode` 3, found with no DNS at 162.159.61.4),
    whose request leaves THROUGH the proxy of the context: a SOCKS5 CONNECT to 162.159.61.4:443; no local resolution;
  * the UDP to the address the DoH gave leaves through the gateway's relay (address type 1);
  * and all of it, with the DoH and the handshakes of the associations the browser needs, fits in the tick of the page.

It fails on an engine that does not keep this (the one of the wrong candidate), and says WHY: the explanation lists what the
DoH, the gateway, the TURN server and the page each saw. The engine refuses to resolve a name that is not a literal behind a
proxy, so the DoH is never asked (no CONNECT to it reaches the gateway) and the browser never reaches the TURN server; then
the handshakes of its associations are serial (each costs round trips of the gateway before a socket is usable).

The browser accepts the certificate of the FakeDoh the way `test_doh_through_the_proxy_e2e.py` does: the page is opened in a
context made with `ignore_https_errors=True` (applied per user context to the DoH request of the lookup of that context), and
the provider is the FakeDoh's own name (`doh.gateway.test`, declared through `extra_prefs`): the DoH of Mozilla is HSTS-preloaded
and Firefox never accepts an override for it.
"""
import pytest

import _turn_chain as chain_mod
from _doh_servers import DOH_PROVIDER_PREFS
from _udp_line import declared_udp, egress_by_proxy, measure_servers

pytestmark = pytest.mark.e2e

#: Loopback goes through the proxy too (as in test_doh_through_the_proxy_e2e.py), so the road of the page is observable.
PREFS = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": ""}

#: ⛔ The variant with the lab's exit on loopback (127.0.0.2). The TURN server tells the browser, in the XOR-MAPPED-ADDRESS of its
#: answer, the address it heard the Allocate from: here a loopback one, and a retail Firefox throws that answer away as "bogus"
#: (`nr_stun_transport_addr_check` in stun_client_ctx.cpp) unless the ICE context allows loopback, which this pref does. It is the lab's
#: address, not a behaviour of the product: on the real network the mapped address is the exit's, and the variant above (an exit that
#: is not loopback, no pref) proves that on Linux. This one runs on Windows too: the exit above is an address added to an interface
#: (administrator and `ip`), which is Linux only until the runner of Windows does the same (work of the release after firefox-63).
PREFS_LOOPBACK_EXIT = {**PREFS, "media.peerconnection.ice.loopback": True}


def _the_core_is_told_the_exit(monkeypatch, chain):
    # The probe of the core that asks for the TCP exit goes nowhere (the world has no network but the gateway): it is told
    # the exit of the chain, the address its datagrams leave from. The core's UDP measurement that follows is NOT replaced:
    # it asks the chain's relay for a STUN Binding for real and compares what comes back with that exit, so the road
    # is declared ``coherent`` by measurement and the engine sends the page's UDP to the relay.
    try:
        from invisible_core import _geo
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(chain), raising=False)
    except Exception:   # noqa: BLE001
        pass
    try:
        from invisible_playwright import InvisiblePlaywright
        monkeypatch.setattr(InvisiblePlaywright, "_assert_uscita_invariata", lambda self: None, raising=False)
    except Exception:   # noqa: BLE001
        pass


@pytest.fixture
def gateway(monkeypatch):
    # ⛔ THE EXIT OF THE LAB IS NOT A LOOPBACK ADDRESS. The TURN server tells the browser, in the XOR-MAPPED-ADDRESS of its answer, the
    # address it heard the Allocate from: the gateway's exit. nICEr throws away an answer whose mapped address is loopback as "bogus"
    # (stun_client_ctx.cpp nr_stun_transport_addr_check, unless the pref `media.peerconnection.ice.loopback` is on, which it is not in a
    # product), so the exit is a documentation address held on `lo` (root, Linux: the suite of this test is listed for Linux only).
    with chain_mod.lab_exit_address() as exit_ip, chain_mod.Chain(rtt_ms=130, exit_host=exit_ip) as chain:
        _the_core_is_told_the_exit(monkeypatch, chain)
        yield chain


@pytest.fixture
def gateway_on_loopback(monkeypatch):
    with chain_mod.Chain(rtt_ms=130) as chain:     # the exit is chain_mod.EXIT_HOST, 127.0.0.2
        _the_core_is_told_the_exit(monkeypatch, chain)
        yield chain


def test_a_turn_srflx_by_name_arrives_through_the_relay_before_the_tick(gateway, firefox_binary, engine_at_least):
    """The exit of the lab is an address that is not loopback, and no pref: the product's own setting (Linux, root)."""
    _walk(gateway, firefox_binary, engine_at_least, PREFS)


def test_a_turn_srflx_by_name_arrives_through_the_relay_when_the_exit_of_the_lab_is_loopback(gateway_on_loopback, firefox_binary,
                                                                                          engine_at_least):
    """The exit of the lab is 127.0.0.2 and the ICE context allows loopback (the pref): the same walk on every system, Windows too."""
    _walk(gateway_on_loopback, firefox_binary, engine_at_least, PREFS_LOOPBACK_EXIT)


def _walk(gateway, firefox_binary, engine_at_least, prefs):
    import h2  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    engine_at_least("firefox-63")
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(headless=True, binary_path=firefox_binary, timezone="America/New_York", locale="en-US",
                             proxy=gateway.proxy, udp_stun_servers=measure_servers(gateway),
                             extra_prefs={**prefs, **DOH_PROVIDER_PREFS}) as browser:
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.goto(gateway.page.url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function("window.__done === true", timeout=9000)
        except Exception:   # noqa: BLE001
            pass
        try:
            page.wait_for_function("window.__result.candidates.some(c => c.indexOf(' typ relay ') >= 0)", timeout=3000)
        except Exception:   # noqa: BLE001
            pass
        result = page.evaluate("window.__result")
    declared = declared_udp(gateway.proxy, measure_servers(gateway))
    assert declared == "coherent", "the core did not measure the chain's UDP coherent (%s): the engine would not use the relay" % declared + gateway.explain(result)
    ok = result.get("status") == "DONE" and gateway.page.verdicts and gateway.page.verdicts[-1]["ok"]
    assert ok, "the page did not get a server-reflexive candidate that the TURN server saw, before its tick of 2 s:" + gateway.explain(result)
    # what the PAGE reads: a server-reflexive candidate that is the gateway's exit, and the relay of the TURN server it asked
    srflx = (result.get("srflx") or "").split()
    assert len(srflx) > 5 and srflx[4] == gateway.exit_ip, "the srflx of the page is not the exit of the gateway %s: %r" % (gateway.exit_ip, result.get("srflx"))
    relays = [c.split() for c in (result.get("candidates") or []) if " typ relay " in c]
    assert relays and (relays[0][4], int(relays[0][5])) == chain_mod.RELAYED, "the page has no relay candidate of the TURN server: %r" % (relays,) + gateway.explain(result)
