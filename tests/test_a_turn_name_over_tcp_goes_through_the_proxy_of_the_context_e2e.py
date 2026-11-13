"""E2E: a TURN server given by NAME, over TCP, in a context with a proxy, is resolved by the DoH through THAT proxy.

EXPECTED RED ON THE PUBLISHED ENGINE firefox-63, GREEN ON firefox-64 (the engine fix is commit ac0214bff4 with 058edd6cc6). The test is not gated and
not skipped: it runs on every engine, so the candidate of firefox-64 (this branch runs there as ``wrapper_ref``) is red when the fix is missing, and
firefox-63 is red because it does not have it.

The defect. A page asks for ``turn:<name>:3478?transport=tcp``. The TCP socket that talks to the TURN server (``WebrtcTCPSocket``) did not
carry the origin attributes of its PeerConnection, so its name lookup was made in the DEFAULT user context. In a session with no proxy at
launch and a proxy on the context (the scheme of ``test_context_proxy_only_resolves_by_doh_e2e.py``) the default context resolves natively:
the lookup went to the system resolver (a question for a name of a proxied page, and the DoH never saw it); with a proxy at launch AND a
different one on the context, the DoH request left by the proxy of the LAUNCH, the wrong exit. The STUN and the TURN-over-UDP names do not
have the defect (``test_ice_names_by_the_context_proxy_e2e.py``), and the TURN over TCP by an IP does not resolve at all
(``test_webrtc_realness.py::test_the_turn_over_tcp_of_a_context_goes_through_the_proxy_of_that_context``): no test of the main line made the
browser resolve a TURN name over TCP, so nothing showed it. Found by the minimal engine's matrix of page work behind a proxy.

What the page does and what is read (two schemes, as in ``test_nothing_leaves_but_the_proxy_e2e.py``):

* ``context_only``: no launch proxy, a context with a SOCKS5 gateway. The name must reach the DoH by that gateway, the resolver of the
  machine must be asked nothing for it (the trap, Linux root), and the gateway must be handed the ADDRESS the DoH answered (198.51.100.7,
  a documentation address nothing routes: the gateway refuses it and lists it);
* ``launch_and_context``: a launch gateway and a different one for the context. The name must reach the DoH by the gateway of the CONTEXT
  and by no other, and nothing at all may go direct or ask the resolver.

Red on the defect: the DoH is never asked for the name (the lookup took the native road, or the default context's DoH request failed its
handshake by design), and on Linux the trap heard the name.
"""
from __future__ import annotations

import pytest

# The fixtures and the helpers of the DoH e2e (imported, not copied). `doh`, `gateway`, `pages`, `trap` and `watcher` are in conftest.py.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (the hermetic session is autouse and imported by name)
    DOH_ADDRESSES, ZONE, _assert_every_connection_went_through_the_gateway, _direct_to_the_doh, _hermetic_session, _is_address, _launch,
    _page_in, _why)

import _doh_servers as ds
from _doh_servers import DOH_HOST, QTYPE_A, FakeDoh

pytestmark = [pytest.mark.e2e]

#: A documentation address of its own (not the STUN one, 198.51.100.5, nor the core's measurement one, 198.51.100.6).
TURN_NUMBER = "198.51.100.7"
TURN_NAME = "turn-a.test"
TURN_PORT = 3478
TURN_ZONE = {**ZONE, TURN_NAME: TURN_NUMBER}
#: How long the page waits for the ICE agent to try the server, in milliseconds.
WAIT_MS = 6000
#: How long to wait for the DoH to be asked and for the gateway to be handed the address, in seconds.
ASKED_SECONDS = 25

_PROBE_JS = """async ([url, waitMs]) => {
  const errors = [], found = [];
  const pc = new RTCPeerConnection({iceServers: [{urls: url, username: 'u', credential: 'p'}]});
  pc.createDataChannel('d');
  pc.onicecandidateerror = (e) => errors.push(e.errorCode);
  pc.onicecandidate = (e) => { if (e.candidate) found.push(e.candidate.candidate); };
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  pc.close();
  return {errors, candidates: found.length};
}"""


@pytest.fixture
def doh():
    """The FakeDoh with the TURN name pointing at a documentation address (overrides the one of conftest.py)."""
    import h2  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip

    server = FakeDoh(TURN_ZONE)
    yield server
    server.close()


@pytest.mark.parametrize("scheme", ["context_only", "launch_and_context"])
def test_a_turn_server_named_over_tcp_is_resolved_by_the_doh_through_the_proxy_of_the_context(
        scheme, firefox_binary, doh, pages, gateway, trap, watcher):
    """EXPECTED RED on the published firefox-63 (the defect), green with the engine fix of firefox-64."""
    gw_context = gateway("gw-context")
    gw_launch = gateway("gw-launch") if scheme == "launch_and_context" else None
    gateways = [g for g in (gw_context, gw_launch) if g is not None]
    with _launch(firefox_binary, gw_launch.proxy if gw_launch else None) as browser:
        context, page = _page_in(browser, proxy=gw_context.proxy)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        result = page.evaluate(_PROBE_JS, ["turn:%s:%d?transport=tcp" % (TURN_NAME, TURN_PORT), WAIT_MS])
        asked = ds.wait_until(lambda: doh.queries_for(TURN_NAME, QTYPE_A), ASKED_SECONDS)
        handed = ds.wait_until(lambda: (TURN_NUMBER, TURN_PORT) in gw_context.connects, ASKED_SECONDS)
        context.close()
    watcher.stop()

    why = _why(doh, *gateways) + "\n  the page: %s" % (result,)
    assert asked, (
        "the DoH was never asked for the TURN name %s over TCP: the lookup of the socket took the user context of no proxy (the "
        "system resolver, or the default context's DoH request)" % TURN_NAME + why)
    assert doh.vias_of(TURN_NAME, QTYPE_A) == {gw_context.name}, (
        "%s (A) was not asked by the gateway of the context only: %s" % (TURN_NAME, sorted(doh.vias_of(TURN_NAME, QTYPE_A))) + why)
    assert handed, "the gateway of the context was never handed the address %s:%d the DoH answered" % (TURN_NUMBER, TURN_PORT) + why
    for gw in gateways:
        named = [h for h, _p in gw.connects if not _is_address(h) and h != DOH_HOST]
        assert named == [], "%s was sent a NAME in a CONNECT: %s" % (gw.name, named) + why
    if gw_launch is not None:
        assert (TURN_NUMBER, TURN_PORT) not in gw_launch.connects, "the TURN address was handed to the gateway of the LAUNCH" + why
        assert [q for q in doh.queries if q.via == gw_launch.name and q.name == TURN_NAME] == [], (
            "the name of the TURN server left by the gateway of the LAUNCH, not by the one of the context" + why)
    _assert_every_connection_went_through_the_gateway(doh, *gateways)

    assert watcher.watched_pids, "the watcher never saw a browser process (it saw: %s)" % watcher.process_names
    assert _direct_to_the_doh(watcher) == [], (
        "the browser reached a DoH (%s) or a DNS server (port 53) without the proxy: %s" % (", ".join(DOH_ADDRESSES), watcher.describe()))
    assert [c for c in watcher.direct if c.remote.rsplit(":", 1)[0] == TURN_NUMBER] == [], (
        "the browser connected straight to the TURN server, around the proxy: %s" % watcher.describe())
    if gw_launch is not None:
        assert watcher.direct == [], "the browser connected past the gateways, with every context proxied: %s" % watcher.describe()
    if trap is not None:
        assert not trap.asked(TURN_NAME), "the browser asked the resolver of the machine for the TURN name: %s" % trap.describe()
        if gw_launch is not None:
            assert trap.queries == [], "the browser asked the resolver of the machine, with every context proxied: %s" % trap.describe()
