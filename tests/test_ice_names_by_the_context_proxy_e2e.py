"""E2E: the name of an ICE server is resolved in the USER CONTEXT of the page, so it goes by the proxy of that context.

The defect these tests hold: the ICE resolver of a PeerConnection (NrIceResolver) resolved the NAME in `iceServers`
with default OriginAttributes, so the DoH request of that lookup left in the default user context (user context 0) and
not in the context of the page. In the log of the engine the page's own lookups read `Resolving host
[page-a.test]<^userContextId=6...>` and the ICE one reads `Resolving host [stun-a.test]<>`. Two consequences a page
(or the other side of the wire) can see:

* the DoH request of the ICE name went by the proxy of the SESSION and not by the one of the context of the page, and
  with `ignore_https_errors` a property of the context, the lookup of the default context cannot accept the DoH
  certificate: it fails at once (TRR error, no DNS answer) and the STUN server is marked FAILED a millisecond later;
* so the gather of a page behind a gateway that does not relay UDP ended at once, without the ~11 s of nICEr's own
  timer that a retail Firefox on a network that drops UDP shows (the 701 at 11.1-11.3 s, see
  `test_a_proxy_that_does_not_relay_udp_gives_no_srflx_at_all` in test_webrtc_realness.py).

How each test sees it, with nothing but loopback (the tools are the ones of test_doh_through_the_proxy_e2e.py,
`_doh_servers.py`, `_http_proxy.py` and `_socks_servers.py`):

* the FakeDoh maps the ICE names to 198.51.100.5 (`STUN_NUMBER`, TEST-NET-2), NOT to loopback: the ICE code skips a
  loopback STUN server, so a loopback answer would end the gather at once for a reason that has nothing to do with
  the context;
* the gateway is a SOCKS5 one that is TCP only: `DohGateway` is a `Gateway` built with `udp="refuse"` (the option of
  `_socks_servers.py`; `tcp_only()` is the same thing for the plain gateway), so UDP ASSOCIATE is answered with reply
  code 7 and the page gets no srflx; or an HTTP proxy with credentials, which has no UDP at all;
* the FakeDoh cannot read the user context of a DoH request (the request does not carry it), so a test attributes a
  question to a context by the GATEWAY it arrived by: the default context goes through the session proxy, each
  context under test through a proxy of its own. A question for an ICE name that arrives by the session proxy would
  be a question of the default context.

Every test is RED on an engine that resolves the ICE name with default OriginAttributes (the FakeDoh is never asked
the name: the lookup of the default context fails its TLS handshake with the FakeDoh BY DESIGN) and GREEN on one that
carries the attributes of the PeerConnection into the resolver. Two tests of test_doh_through_the_proxy_e2e.py turn
green with the same fix without change: `test_an_ice_server_name_resolves_the_same_way` and
`test_an_authenticated_http_proxy_resolves`.
"""
from __future__ import annotations

import pytest

# The fixtures and the helpers of the DoH e2e (imported, not copied): the hermetic session (autouse), the resolver
# trap, the watcher, the page server, the gateway factory, and the checks of the end-of-test accounting.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (fixtures are used by name)
    PREFS, ZONE, _assert_stayed_home, _create_a_peer_connection_for, _hermetic_session,
    _page_in, _why, gateway, pages, trap, watcher)
# The page-side probe of the realness test: one STUN server, the candidates, the ICE errors, the timeline in ms
# (`at_ms`: host/srflx/error/complete from the offer) and the end of the gather. Imported, not edited.
from test_webrtc_realness import _RELAY_PROBE_JS, candidates

import _doh_servers as ds
from _doh_servers import QTYPE_A, FakeDoh
from _http_proxy import HttpProxy, basic
from _socks_servers import STUN_NUMBER

pytestmark = pytest.mark.e2e

#: The ICE names of the tests. Both resolve to a routable documentation address that only a relay could reach.
ICE_ZONE = {**ZONE, "stun-a.test": STUN_NUMBER, "stun-b.test": STUN_NUMBER}
STUN_A = "stun:stun-a.test:3478"


@pytest.fixture
def doh():
    """The FakeDoh with the ICE names pointing at STUN_NUMBER (overrides the one of the DoH e2e)."""
    pytest.importorskip("h2", reason="Firefox's TRR speaks h2: the FakeDoh serves it with the h2 package")
    server = FakeDoh(ICE_ZONE)
    yield server
    server.close()


@pytest.fixture
def tcp_only_proxy(doh, gateway, request):
    """A proxy without UDP, by kind: a SOCKS5 gateway whose UDP ASSOCIATE is refused, or an HTTP proxy with credentials."""
    if request.param == "socks5":
        # DohGateway is built with udp="refuse": the TCP-only option of _socks_servers.Gateway (reply code 7).
        gw = gateway("gw1")
        assert gw.udp == "refuse"
        yield gw
        return
    proxy = HttpProxy("http1", doh, user="ferd_http_user", password="pw-é42")
    try:
        yield proxy
    finally:
        proxy.close()


def _launch(binary, proxy):
    """Like the DoH e2e's `_launch`, with the baseline's mDNS host candidates declared as the realness test does."""
    from invisible_playwright import InvisiblePlaywright

    return InvisiblePlaywright(seed=42, binary_path=binary, proxy=proxy,
                               extra_prefs={**PREFS, "media.peerconnection.ice.obfuscate_host_addresses": True})


# ============================================================================================================ tests
@pytest.mark.parametrize("tcp_only_proxy", ["socks5", "http_with_credentials"], indirect=True,
                         ids=["socks5_tcp_only", "http_with_credentials"])
def test_a_gateway_without_udp_resolves_the_ice_name_then_ends_in_701(firefox_binary, doh, pages, tcp_only_proxy,
                                                                      trap, watcher):
    """A page in a context with no proxy of its own (the proxy of the session is its proxy) gives `iceServers` a NAME.
    The name is asked to the DoH THROUGH that proxy, in the context of the page (whose certificate override accepts the
    FakeDoh), the answer is 198.51.100.5, the STUN request has no road (the proxy has no UDP) and the gather ends the
    way a retail Firefox on a network that drops UDP ends: host UDP and TCP candidates, no srflx, ONE ICE error 701 at
    nICEr's own time (10-13 s) together with the end of the gather. On an engine that resolves the name in the default
    context the FakeDoh is never asked and the gather ends at once (the name is unresolved)."""
    proxy = tcp_only_proxy
    with _launch(firefox_binary, proxy.proxy) as browser:
        context, page = _page_in(browser)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        res = page.evaluate(_RELAY_PROBE_JS, STUN_A)
        context.close()
    watcher.stop()

    why = _why(doh, proxy) + "\n  page: candidates %s, ICE errors %s, timeline (ms from the offer) %s" % (
        res["candidates"], res["errors"], res["at_ms"])
    # 1. The name was asked, by the proxy of the context and by no other road.
    ds.wait_until(lambda: doh.queries_for("stun-a.test", QTYPE_A), 3)
    assert doh.queries_for("stun-a.test", QTYPE_A), (
        "the DoH was never asked stun-a.test (A): the ICE resolver looked the name up in the default user context, whose "
        "DoH request cannot accept the FakeDoh certificate" + why)
    assert doh.vias_of("stun-a.test", QTYPE_A) == {proxy.name}, (
        "stun-a.test (A) was not asked by the proxy of the context only" + why)
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy" + why
    assert proxy.doh_connects(), "the proxy carried no DoH CONNECT" + why
    if isinstance(proxy, HttpProxy):
        expected = basic("ferd_http_user", "pw-é42")
        assert set(proxy.doh_authorizations()) == {expected}, (
            "every DoH CONNECT must carry the preemptive Basic header" + why)
        assert [s for s in proxy.refused_for_credentials if (s.host, s.port) in ds.DOH_TARGETS] == [], (
            "the DoH CONNECT needed a 407 round trip: the header was not preemptive" + why)
    # 2. The end of the gather, as a retail Firefox with UDP blocked.
    cands = candidates(res["candidates"])
    typ = [c["typ"] for c in cands]
    assert "srflx" not in typ and "relay" not in typ, "a candidate that nothing allocated" + why
    assert [e["code"] for e in res["errors"]] == [701], "the STUN request must end in ONE ICE error 701" + why
    protocols = sorted({c["proto"].upper() for c in cands if c["typ"] == "host"})
    assert protocols == ["TCP", "UDP"], "host candidates of a retail with UDP blocked: UDP and TCP" + why
    assert 10_000 <= res["at_ms"]["error"] <= 13_000, (
        "the 701 came at %s ms; nICEr's time on a network that drops UDP is 11.1-11.3 s (an immediate error means the "
        "name was never resolved)" % res["at_ms"]["error"] + why)
    assert "complete" in res["at_ms"], "the gather did not end by itself" + why
    _assert_stayed_home(watcher, trap, doh, proxy)


def test_the_ice_name_of_a_context_goes_by_the_proxy_of_that_context(firefox_binary, doh, pages, gateway, trap,
                                                                    watcher):
    """The session proxy is gw0; the context 1 has gw1, the context 2 has gw2. The page of each context asks for the
    name of its own (stun-a.test, stun-b.test) and each name is asked at the gateway of ITS context only. No question
    for an ICE name leaves in the default context: the FakeDoh cannot read the user context of a request, so the
    default context is told by its road, the session proxy gw0, which must have carried no question for either name."""
    gw0, gw1, gw2 = gateway("gw0"), gateway("gw1"), gateway("gw2")
    with _launch(firefox_binary, gw0.proxy) as browser:
        context_1, page_1 = _page_in(browser, proxy=gw1.proxy)
        context_2, page_2 = _page_in(browser, proxy=gw2.proxy)
        page_1.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        page_2.goto(pages.url("page-b.test"), wait_until="load", timeout=30000)
        _create_a_peer_connection_for(page_1, "stun-a.test")
        _create_a_peer_connection_for(page_2, "stun-b.test")
        asked_a = ds.wait_until(lambda: doh.queries_for("stun-a.test", QTYPE_A), 30)
        asked_b = ds.wait_until(lambda: doh.queries_for("stun-b.test", QTYPE_A), 30)
        for page in (page_1, page_2):
            page.evaluate("() => window.__pc.close()")
        context_1.close()
        context_2.close()
    watcher.stop()

    why = _why(doh, gw0, gw1, gw2)
    assert asked_a, "the DoH was never asked stun-a.test (the lookup left in the default context)" + why
    assert asked_b, "the DoH was never asked stun-b.test (the lookup left in the default context)" + why
    assert doh.vias_of("stun-a.test", QTYPE_A) == {"gw1"}, "stun-a.test must be asked by gw1 (context 1) only" + why
    assert doh.vias_of("stun-b.test", QTYPE_A) == {"gw2"}, "stun-b.test must be asked by gw2 (context 2) only" + why
    on_the_session_proxy = [q for q in doh.queries if q.via == "gw0" and q.name in ("stun-a.test", "stun-b.test")]
    assert on_the_session_proxy == [], (
        "a question for an ICE name left in the default context (by the session proxy gw0): %s" % on_the_session_proxy
        + why)
    assert gw1.doh_connects() and gw2.doh_connects(), "each context's proxy must have carried a DoH CONNECT" + why
    _assert_stayed_home(watcher, trap, doh, gw0, gw1, gw2)
