"""E2E: behind a proxy every name is resolved by Firefox's DoH THROUGH the proxy, and nothing leaves the host.

Hermetic on a real binary. The tools they stand on are proven by `test_the_fake_doh_answers.py`,
`test_the_http_proxy_helper_asks_for_credentials.py` and `test_the_leak_watcher_sees_a_direct_connection.py`, and the
session declaration they start from by `test_doh_session_prefs_unit.py`. Run against an engine that keeps the contract,
they are the proof; run against one that does not, each of them fails on the thing it names.

The fake DoH is NOT `mozilla.cloudflare-dns.com`. That host is HSTS-preloaded/pinned, and Firefox refuses any
certificate override for such a host ("HSTS or pinned host - no overrides allowed", MOZILLA_PKIX_ERROR_SELF_SIGNED_CERT),
so a self-signed fake for it is rejected whatever `ignore_https_errors` says. The test declares its own provider through
the core's `extra_prefs` overlay (applied last, documented for this): `network.trr.uri` = `https://doh.gateway.test/
dns-query` (a `.test` name is on no preload list) and `network.trr.bootstrapAddr` = `192.0.2.53` (TEST-NET-1: the
address of the FakeDoh as a gateway knows it, never a real one). The gateways map a CONNECT to that address (or name) to
the FakeDoh. The core's own default provider is checked by `test_doh_session_prefs_unit.py`, not here.

The contract (core-19 and motore-18): the core declares the DoH PROVIDER for every session (`network.trr.uri` the DoH of
Mozilla, `network.trr.bootstrapAddr` an address so the DoH host is found with no DNS) and, with a proxy,
`network.proxy.allow_bypass` false; the ENGINE owns the state, so the core never writes `network.trr.mode`,
`exclude-etc-hosts` or `excluded-domains`. Each browser context is a whole situation, decided per lookup by the user
context of the lookup (the DoH service itself stays in mode 0; Juggler publishes the contexts that have a proxy):

* a context WITH a proxy - its own, or the launch proxy, which is the proxy of every context that has none of its own,
  the default one included - resolves its names ONLY by DoH, THROUGH that proxy, fail-closed: a DoH request is a channel
  like any other, so it leaves by a SOCKS5 CONNECT or an HTTP CONNECT to the DoH address, by the proxy of the context of
  the lookup, and the browser then connects to the resolved ADDRESS (SOCKS5 by address, never by name);
* a context WITHOUT a proxy resolves natively (the system resolver) and NEVER reaches the DoH.

Page hosts, ICE server names and HTTP/3 hosts of a proxied context all follow the first rule. The session proxy is the
first command on the pipe, and every proxied channel waits for it: nothing of a proxied context goes DIRECT. No UDP/53
and no direct TCP to a DoH leave the browser, and no helper process (crash reporter, default browser agent,
maintenance service, updater, pingsender) is started. The tests of a session WITH a launch proxy may therefore assert
that nothing at all goes direct and nothing asks the system resolver (the default context is proxied too); the tests of a
session WITHOUT one assert only about the DoH road, because the default context is then the machine's own network.
`test_context_proxy_only_resolves_by_doh_e2e.py` and `test_resolver_race_e2e.py` test the rule where it is sharpest.

How each test sees it, with nothing but loopback:

* `FakeDoh` answers the names (`page-a.test`...) and records every query with the gateway it arrived by (`via`);
* `DohGateway` / `HttpProxy` are the proxies; a CONNECT to the DoH address is handed to the FakeDoh, every other one to
  a loopback address is relayed, and every CONNECT is recorded (a CONNECT that carries a NAME is a defect to find);
* `Watcher` samples the connections of every browser process from before the launch: one to a non-loopback address is
  a direct connection;
* `ResolverTrap` (Linux, root) points /etc/resolv.conf at a counting resolver: one question is a leak to the resolver
  of the machine. Where it cannot run, its assertions are skipped, not the test - unless the gate sets
  INVPW_REQUIRE_TRAP=1: then a trap that cannot run, or whose port 53 cannot be bound, is a failure.

Pages are served by a local HTTP server on 127.0.0.1 and reached by NAMES only the FakeDoh can resolve. The browser must
accept the FakeDoh certificate: the pages are opened in contexts made with `ignore_https_errors=True`, which the engine
implements per user context (`Browser.setIgnoreHTTPSErrors` -> `nsICertOverrideService`, keyed by the origin attributes
of the TLS socket), and a DoH request of a lookup carries the user context of that lookup
(`StealthApplyTRRContextToChannel`). So the lookups of THESE contexts accept the certificate; a lookup of the default
context (user context 0: the browser's own background resolutions, telemetry and the like) cannot be made to. In a session
WITH a launch proxy the default context is proxied too, so its background lookups go to the FakeDoh through the launch
proxy and fail the handshake BY DESIGN: no assertion there counts the FakeDoh's TLS failures or treats a name of the
default context as a result (the accounting helper below counts them against the CONNECTs of the gateway). In a session
WITHOUT a launch proxy the default context resolves natively and never reaches the FakeDoh: there a TLS failure at the
FakeDoh is itself the sign of the defect. Every assertion is about the names and the contexts the test itself creates.
This is why a test says "a context with no proxy of its own" where the brief says "the default context".

Assumptions that could not be verified without the engine, each one the first place to look if a test fails for no
visible reason: (1) the TRR channel of a lookup in a context honours `ignore_https_errors` of that context; (2) the
ICE resolver's lookup carries the origin attributes of its document; (3) Firefox's TRR accepts HTTP/1.1 as well as h2
(the FakeDoh offers both; `doh.alpn_of(...)` says which one was used); (4) the DoH and page connections reach a SOCKS
gateway as ADDRESSES; (5) behind an HTTP proxy an `http` page's name goes to the proxy inside the request (it is the
proxy that resolves it), so the DoH is triggered there by an ICE server name, which the browser always resolves itself.
"""
from __future__ import annotations

import base64
import http.server
import ipaddress
import threading
import time
from pathlib import Path

import pytest

from invisible_core import SessionGeo, decide_session_locale
from invisible_playwright import InvisiblePlaywright

import _doh_servers as ds
import _trap_policy
from _doh_servers import DOH_BOOTSTRAP, DOH_HOST, DOH_PORT, DOH_PROVIDER_PREFS, QTYPE_A, DohGateway, FakeDoh
from _http_proxy import HttpProxy, basic
from _leaks import ResolverTrap, Watcher
from _doh_support import ZONE, Pages, a_display_when_the_host_has_none  # noqa: F401  (Pages and ZONE are imported by name by the sibling tests)
from _servers import LocalServer  # noqa: F401

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

#: Loopback targets go through the proxy too (as in test_proxy_socks_auth_e2e.py), so the road of a page is observable.
#: The two `network.trr.*` entries replace the core's default provider (a preloaded host whose certificate cannot be
#: overridden) with the FakeDoh's: https://doh.gateway.test/dns-query, found at 192.0.2.53 with no DNS.
PREFS = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": "", **DOH_PROVIDER_PREFS}
#: The helper processes of Firefox that a session behind a proxy must never start (match on the executable's stem).
HELPERS = ("crashreporter", "default-browser-agent", "maintenanceservice", "updater", "pingsender")
GEO = SessionGeo("America/New_York", "203.0.113.7", latitude=40.7128, longitude=-74.0060,
                 locale=decide_session_locale("en-US"))


# ------------------------------------------------------------------------------------------------------- fixtures
# `doh`, `gateway`, `trap`, `watcher` and `pages` are in conftest.py: the sibling files use them without importing them.
@pytest.fixture(autouse=True)
def _hermetic_session(monkeypatch):
    """No egress lookup on the internet, and no egress drift check through the proxy.

    A launch asks "who am I" through the proxy (api.ipify.org by NAME, resolved by the TEST process: a question to the
    resolver trap and a connection past the loopback), and re-asks at every new context. This test is about the
    BROWSER's roads, so the answer is declared: the same facts (timezone, locale, position, an exit address) the
    lookup would have returned.
    """
    monkeypatch.setattr("invisible_playwright.launcher.prepare_session_geo", lambda tz, proxy, locale, **_kw: GEO)
    monkeypatch.setattr(InvisiblePlaywright, "_assert_uscita_invariata", lambda self: None)
    # The core also asks the exit of a SOCKS5 proxy (its UDP measurement, invisible_core._udp_exit): api.ipify.org, icanhazip.com and
    # checkip.amazonaws.com BY NAME through the gateway of the context, which the tests below read as "a CONNECT carried a name"
    # (run 43164943499, Windows). The answer is declared here too.
    monkeypatch.setattr("invisible_core._geo.discover_egress_ip", lambda *_a, **_k: GEO.egress_ip)


# --------------------------------------------------------------------------------------------------------- helpers
def _launch(binary, proxy, **kwargs):
    return InvisiblePlaywright(seed=42, binary_path=binary, proxy=proxy, extra_prefs=dict(PREFS),
                               **{**a_display_when_the_host_has_none(), **kwargs})


def _page_in(browser, proxy=None):
    """A page in a new context, with its own proxy when given and the FakeDoh's certificate accepted."""
    options = {"ignore_https_errors": True}
    if proxy is not None:
        options["proxy"] = proxy
    context = browser.new_context(**options)
    return context, context.new_page()


def _text(page):
    return page.evaluate("() => document.body.innerText")


def _why(doh, *proxies):
    """What every side saw, for the message of a failed assertion."""
    lines = ["FakeDoh queries: %s" % [tuple(q) for q in doh.queries],
             "FakeDoh connections (via, protocol): %s" % doh.connections,
             "FakeDoh TLS failures (the default context's background lookups fail by design): %s" % doh.tls_failures]
    for p in proxies:
        lines.append("%s CONNECTs: %s" % (p.name, p.connects))
        if getattr(p, "refused", None):
            lines.append("%s refused: %s" % (p.name, p.refused))
    return "\n  " + "\n  ".join(lines)


def _is_address(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


#: The addresses a DoH request could reach by a road other than the proxy of its context: the bootstrap address of the
#: test's provider and the one of the core's default provider.
DOH_ADDRESSES = (DOH_BOOTSTRAP, "162.159.61.4")


def _direct_to_the_doh(watcher):
    """The direct connections the watcher saw to a DoH address or to a DNS port (53)."""
    return [c for c in watcher.direct
            if c.remote.rsplit(":", 1)[0] in DOH_ADDRESSES or c.remote.endswith(":53")]


def _asked_outside(doh, *zone_names):
    """The FakeDoh queries whose name is none of `zone_names` nor a name under one of them (the question of the HTTPS
    record of a name is `_<port>._https.<name>`, which ends with it). Empty when only the names of the test's own
    contexts were asked, i.e. when no background name of another context reached the DoH."""
    names = tuple(n.lower() for n in zone_names)
    return [q for q in doh.queries if not any(q.name == n or q.name.endswith("." + n) for n in names)]


def _assert_every_connection_went_through_the_gateway(doh, *proxies):
    """Every connection the browser made, the test's own and (in a session with a launch proxy) the background traffic
    of the default context, is accounted for at a gateway, and none reached the FakeDoh by another road.

    The background lookups of a proxied default context (telemetry and the like) have no `ignore_https_errors`, so their
    TLS handshake with the FakeDoh fails BY DESIGN. That is an expected outcome, counted here, not ignored:

    * the FakeDoh served no connection (successful or failed) by `direct`: each one arrived by a named gateway;
    * per gateway, the connections the FakeDoh served (completed + failed handshakes) are all tunnels of a DoH CONNECT
      that the gateway itself recorded (it records the CONNECT before it hands the socket over), so none is
      unaccounted for: served <= recorded DoH CONNECTs;
    * no CONNECT of any gateway carries the name of the test's own zone (the browser resolves those with the DoH and
      connects by address); a CONNECT to some other non-loopback target is listed by the gateway in `refused`, i.e. it
      was attempted THROUGH the gateway and went no further, which is the point.
    """
    why = _why(doh, *proxies)
    names = {p.name for p in proxies}
    served = list(doh.connections) + [(via, "tls-failed") for via, _error in doh.tls_failures]
    by_road = {via for via, _what in served}
    assert by_road <= names, "the FakeDoh served a connection by a road that is no gateway of the test: %s" % (
        sorted(by_road - names)) + why
    for proxy in proxies:
        mine = [what for via, what in served if via == proxy.name]
        recorded = len(proxy.doh_connects())
        assert len(mine) <= recorded, (
            "%s: the FakeDoh served %d connection(s) (%d failed handshakes) but the gateway recorded only %d DoH "
            "CONNECT(s)" % (proxy.name, len(mine), mine.count("tls-failed"), recorded) + why)
        zone_names = [h for h, _p in proxy.connects if h.lower() in ZONE]
        assert zone_names == [], "%s was sent the NAME %s instead of an address" % (proxy.name, zone_names) + why


def _assert_stayed_home(watcher, trap=None, doh=None, *proxies):
    """Nothing went past the gateway (no direct connection), nothing asked the machine's resolver, and (when the
    FakeDoh and the proxies are given) every connection is accounted for at a gateway."""
    if doh is not None:
        _assert_every_connection_went_through_the_gateway(doh, *proxies)
    assert watcher.watched_pids, ("the watcher never saw a browser process (it saw: %s): a watcher that cannot see "
                                  "proves nothing" % watcher.process_names)
    assert watcher.direct == [], "the browser connected past the gateway: %s" % watcher.describe()
    if trap is not None:
        assert trap.queries == [], "the browser asked the resolver of the machine: %s" % trap.describe()


def _create_a_peer_connection_for(page, name):
    """An RTCPeerConnection whose ICE server is `stun:<name>:3478`, gathering started: the browser resolves the name."""
    page.evaluate("""(name) => {
        const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:' + name + ':3478'}]});
        window.__pc = pc;
        pc.createDataChannel('probe');
        pc.createOffer().then(offer => pc.setLocalDescription(offer));
    }""", name)


# ============================================================================================================ tests
def test_names_resolve_through_doh_by_the_proxy(firefox_binary, doh, pages, gateway, trap, watcher):
    """A page name is asked to the DoH, by the proxy, and the page is then reached by ADDRESS."""
    gw1 = gateway("gw1")
    with _launch(firefox_binary, gw1.proxy) as browser:
        context, page = _page_in(browser)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        text = _text(page)
        context.close()
    watcher.stop()

    assert "HOST=page-a.test:%d" % pages.port in text, "the page did not load through the proxy: %r" % text
    why = _why(doh, gw1)
    assert doh.vias_of("page-a.test", QTYPE_A) == {"gw1"}, "page-a.test (A) was not asked by gw1 only" + why
    assert (DOH_BOOTSTRAP, DOH_PORT) in gw1.connects, "no DoH CONNECT to the bootstrap address" + why
    assert not [h for h, _p in gw1.connects if h == "page-a.test"], (
        "the browser sent the NAME page-a.test to the proxy instead of resolving it" + why)
    assert [h for h, _p in gw1.connects if not _is_address(h) and h != DOH_HOST] == [], (
        "a CONNECT carried a name: the browser must resolve with the DoH and connect by address" + why)
    assert ("127.0.0.1", pages.port) in gw1.connects, "the page was not reached by address through gw1" + why
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy" + why
    _assert_stayed_home(watcher, trap, doh, gw1)


def test_an_ice_server_name_resolves_the_same_way(firefox_binary, doh, pages, gateway, trap, watcher):
    """A name chosen by the PAGE (a STUN server in iceServers) is resolved by the DoH through the proxy too."""
    gw1 = gateway("gw1")
    with _launch(firefox_binary, gw1.proxy) as browser:
        context, page = _page_in(browser)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        _create_a_peer_connection_for(page, "stun-a.test")
        asked = ds.wait_until(lambda: doh.queries_for("stun-a.test"), 30)
        page.evaluate("() => window.__pc.close()")
        context.close()
    watcher.stop()

    why = _why(doh, gw1)
    assert asked, "the browser never asked the DoH for the ICE server name" + why
    assert doh.vias_of("stun-a.test") == {"gw1"}, "stun-a.test was not asked by gw1 only" + why
    _assert_stayed_home(watcher, trap, doh, gw1)


def test_two_contexts_two_doh_exits(firefox_binary, doh, pages, gateway, trap, watcher):
    """Each context resolves by ITS proxy: the browser's for a context with none, its own for context B, each with
    the credentials of that gateway."""
    gw1 = gateway("gw1", user="gw1-user", password="gw1-pass")
    gw2 = gateway("gw2", user="gw2-user", password="gw2-pass")
    with _launch(firefox_binary, gw1.proxy) as browser:
        context_a, page_a = _page_in(browser)                    # no proxy of its own: the browser's, gw1
        context_b, page_b = _page_in(browser, proxy=gw2.proxy)   # its own: gw2
        page_a.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        page_b.goto(pages.url("page-b.test"), wait_until="load", timeout=30000)
        text_a, text_b = _text(page_a), _text(page_b)
        context_a.close()
        context_b.close()
    watcher.stop()

    why = _why(doh, gw1, gw2)
    assert "HOST=page-a.test" in text_a and "HOST=page-b.test" in text_b, (text_a, text_b)
    assert doh.vias_of("page-a.test", QTYPE_A) == {"gw1"}, "page-a.test must be resolved by gw1 only" + why
    assert doh.vias_of("page-b.test", QTYPE_A) == {"gw2"}, "page-b.test must be resolved by gw2 only" + why
    assert gw1.doh_connects() and gw2.doh_connects(), "each gateway must have carried a DoH CONNECT" + why
    assert ("127.0.0.1", pages.port) in gw1.connects and ("127.0.0.1", pages.port) in gw2.connects, (
        "each page must be reached by address through the gateway of its context" + why)
    assert gw1.credentials_seen and set(gw1.credentials_seen) == {("gw1-user", "gw1-pass")}, (
        "gw1 saw: %s" % gw1.credentials_seen)
    assert gw2.credentials_seen and set(gw2.credentials_seen) == {("gw2-user", "gw2-pass")}, (
        "gw2 saw: %s" % gw2.credentials_seen)
    _assert_stayed_home(watcher, trap, doh, gw1, gw2)


def test_an_authenticated_http_proxy_resolves(firefox_binary, doh, pages, trap, watcher):
    """An http proxy with credentials: the DoH CONNECT carries the PREEMPTIVE `Proxy-Authorization: Basic`, so it
    never needs a 407 round trip, and the page loads.

    Behind an HTTP proxy an `http` page's NAME goes to the proxy (which resolves it), so the browser's own DoH is
    triggered here by an ICE server name, which the browser always resolves itself.
    """
    user, password = "ferd_http_user", "pw-é42"
    proxy = HttpProxy("http1", doh, user=user, password=password)
    try:
        with _launch(firefox_binary, proxy.proxy) as browser:
            context, page = _page_in(browser)
            page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
            text = _text(page)
            _create_a_peer_connection_for(page, "stun-a.test")
            asked = ds.wait_until(lambda: doh.queries_for("stun-a.test"), 30)
            page.evaluate("() => window.__pc.close()")
            context.close()
        watcher.stop()

        expected = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
        assert expected == basic(user, password)
        why = _why(doh, proxy)
        assert "HOST=page-a.test:%d" % pages.port in text, "the page did not load through the proxy: %r" % text
        assert asked and doh.vias_of("stun-a.test") == {"http1"}, "the DoH was not reached through the proxy" + why
        authorizations = proxy.doh_authorizations()
        assert authorizations, "no DoH CONNECT reached the proxy" + why
        assert set(authorizations) == {expected}, (
            "every DoH CONNECT must carry the preemptive Basic header %r, saw %r" % (expected, authorizations) + why)
        assert [s for s in proxy.refused_for_credentials if (s.host, s.port) in ds.DOH_TARGETS] == [], (
            "the DoH CONNECT needed a 407 round trip: the header was not preemptive" + why)
        _assert_stayed_home(watcher, trap, doh, proxy)
    finally:
        proxy.close()


def test_nothing_leaves_the_host_between_launch_and_the_first_command(firefox_binary, doh, pages, gateway, trap,
                                                                      watcher):
    """The watcher runs from before the launch to the first navigation, against a gateway that is slow to say hello
    (its first accepted connection is answered a second late): the session proxy is the first command on the pipe and
    every proxied channel is held until then, so nothing may leave meanwhile."""
    gw1 = gateway("gw1", first_connection_delay=1.0)
    with _launch(firefox_binary, gw1.proxy) as browser:
        context, page = _page_in(browser)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=45000)
        text = _text(page)
        context.close()
    watcher.stop()

    why = _why(doh, gw1)
    assert "HOST=page-a.test" in text
    assert gw1.accepted_at, "the browser never connected to the gateway" + why
    _assert_stayed_home(watcher, trap, doh, gw1)


def test_no_helper_process_starts(firefox_binary, doh, pages, gateway, trap, watcher):
    """No crash reporter client, default browser agent, maintenance service, updater or pingsender is started,
    through the first five seconds of the session and its teardown (pingsender is the one that starts at exit)."""
    gw1 = gateway("gw1")
    with _launch(firefox_binary, gw1.proxy) as browser:
        context, page = _page_in(browser)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        time.sleep(5)
        context.close()
    time.sleep(1)  # the processes of the exit
    watcher.stop()

    assert watcher.watched_pids, "the watcher never saw a browser process: %s" % watcher.process_names
    started = watcher.helpers_seen(HELPERS)
    assert started == [], "helper processes were started: %s (all processes seen: %s)" % (
        started, watcher.process_names)


def test_a_dead_proxy_does_not_fall_back(firefox_binary, doh, pages, gateway, trap, watcher):
    """When the proxy dies, a navigation fails: it does not go direct and does not reach the resolver of the machine.

    `page-c.test` is not cached anywhere and the FakeDoh would answer it if it were reached.
    """
    gw1 = gateway("gw1")
    with _launch(firefox_binary, gw1.proxy) as browser:
        context, page = _page_in(browser)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        gw1.kill()
        with pytest.raises(Exception) as failed:
            page.goto(pages.url("page-c.test"), wait_until="load", timeout=20000)
        context.close()
    watcher.stop()

    why = _why(doh, gw1)
    assert pages.hits and all(host.startswith("page-a.test") for host, _p in pages.hits), (
        "the dead proxy still served a page: %s" % pages.hits + why)
    assert doh.queries_for("page-c.test") == [], "page-c.test was resolved after the proxy died" + why
    assert gw1.killed and failed.value is not None
    _assert_stayed_home(watcher, trap, doh, gw1)


def test_a_session_without_a_proxy_resolves_with_the_system_resolver(firefox_binary, pages, watcher):
    """The negative control: with no proxy anywhere the exit is the machine's own network and retail resolves with the
    system resolver. The core declares the DoH provider (uri + bootstrapAddr) in every session, but no engine-owned
    state (`network.trr.mode` and the other two), and no context has a proxy, so no lookup is a DoH lookup: the
    resolver trap sees the question and no connection goes to a DoH address."""
    trapped = ResolverTrap.available
    why = _trap_policy.failure(None, trapped, ResolverTrap.reason)
    if why:
        pytest.fail(why)
    name = "page-d.test" if trapped else "localhost"
    session = InvisiblePlaywright(seed=42, binary_path=firefox_binary, proxy=None)
    session._timezone, session._locale = GEO.timezone, GEO.locale
    prefs = session._build_prefs()
    assert prefs.get("network.trr.mode") != 3, "a session without a proxy turned TRR on: %s" % prefs.get("network.trr.mode")
    engine_owned = [k for k in ("network.trr.mode", "network.trr.exclude-etc-hosts", "network.trr.excluded-domains")
                    if k in prefs]
    assert engine_owned == [], "a session without a proxy declared engine-owned TRR prefs: %s" % engine_owned

    trap = None
    if trapped:
        trap = ResolverTrap({"page-d.test": "127.0.0.1"})
        trap.__enter__()  # port 53 taken by another worker is a failure of the setup, not a reason to skip
    try:
        with session as browser:
            context = browser.new_context()
            page = context.new_page()
            page.goto(pages.url(name), wait_until="load", timeout=30000)
            text = _text(page)
            context.close()
    finally:
        if trap is not None:
            trap.__exit__(None, None, None)
        watcher.stop()

    assert "HOST=%s:%d" % (name, pages.port) in text, "the page did not load through the system resolver: %r" % text
    if trap is not None:
        assert trap.asked(name), "the system resolver was never asked for %s: %s" % (name, trap.describe())
    assert watcher.watched_pids, "the watcher never saw a browser process: %s" % watcher.process_names
    assert _direct_to_the_doh(watcher) == [], (
        "a session with no proxy reached a DoH (or a DNS server) instead of the system resolver: %s" % watcher.describe())
