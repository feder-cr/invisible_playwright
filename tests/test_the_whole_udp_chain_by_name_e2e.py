"""The whole chain a leak test walks, by NAME, behind a SOCKS5 gateway that carries UDP, all on loopback (hermetic).

A page asks for a TURN allocation at a server written as a NAME (`turn:turn.relay-only.test:3478`), as the real leak-test pages
do: no STUN, so a server-reflexive candidate can only be born from a successful allocation. Its logic is the logic of such a page:
a tick of 2 seconds from the start of the page that marks TIMEOUT, cleared only after a POST made when the srflx arrives; the
server compares the address of that srflx with the source the TURN server saw for the allocation.

The only road to anything is a gateway at 130 ms of round trip (every step of its control connection, every datagram):
the TURN server is at an unroutable documentation address that only the gateway maps to it, the name is known only to a resolver
that only the gateway reaches (the gateway also resolves a name sent to it as SOCKS5 address type 3), and the gateway's
datagrams leave from 127.0.0.2 so that a TURN server hearing 127.0.0.1 proves the browser went around the gateway.

What the product must do, to be what a retail Firefox is with no proxy at all (the same candidates, in the same time):
  * the name of the ICE server is resolved at the exit, with no local resolution;
  * the UDP to the TURN server leaves through the gateway's relay;
  * and all of it, with the handshakes of the associations the browser needs, fits in the tick of the page.

It fails today, and says WHY (the explanation lists what each server saw): the engine refuses to resolve a name that is not a literal
behind a proxy, so the browser never reaches the TURN server, and the handshakes of its associations are serial (each costs
round trips of the gateway before a socket is usable).
"""
import pytest

import _turn_chain as chain_mod

pytestmark = pytest.mark.e2e

EXIT = "203.0.113.7"   # what the product's own probe of the egress is told: RFC 5737 TEST-NET-3


@pytest.fixture
def gateway(monkeypatch):
    # The probes of the core that ask for the egress go nowhere: the world has no network but the gateway.
    for module, name in (("invisible_core._geo", "discover_egress_ip"),):
        try:
            mod = __import__(module, fromlist=[name])
            monkeypatch.setattr(mod, name, lambda *a, **k: EXIT, raising=False)
        except Exception:   # noqa: BLE001
            pass
    with chain_mod.Chain(rtt_ms=130) as chain:
        yield chain


def test_a_turn_srflx_by_name_arrives_through_the_relay_before_the_tick(gateway, firefox_binary, engine_at_least):
    engine_at_least("firefox-62")
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(headless=True, binary_path=firefox_binary, timezone="America/New_York", locale="en-US",
                             proxy=gateway.proxy) as browser:
        page = browser.new_context().new_page()
        page.goto(gateway.page.url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function("window.__done === true", timeout=9000)
        except Exception:   # noqa: BLE001
            pass
        result = page.evaluate("window.__result")
    ok = result.get("status") == "DONE" and gateway.page.verdicts and gateway.page.verdicts[-1]["ok"]
    assert ok, "the page did not get a server-reflexive candidate that the TURN server saw, before its tick of 2 s:" + gateway.explain(result)
