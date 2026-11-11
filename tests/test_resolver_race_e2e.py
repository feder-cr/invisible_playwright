"""E2E: the first navigation of a fresh context never asks the system resolver, whatever the situation of the context.

The rule (owner decision B): each browser context is a whole situation. A context WITH a proxy (its own, or the launch
proxy that every context without a proxy of its own inherits) resolves its names ONLY by DoH, through that proxy,
fail-closed; a context WITHOUT a proxy resolves natively and never uses the DoH. The resolver hook of the engine chooses
per lookup by the user context of the lookup, from the contexts that Juggler publishes as proxied in a pref.

The race this file is about: the proxy of a context is declared by the wrapper AFTER `Browser.createBrowserContext` and the
first lookup of its first page can be made at once. If the engine does not know yet that the context is proxied, the first
lookup takes the native road: the system resolver is asked for a name of a proxied context (a leak of the name, and on a
real network the page does not even load). Every case here therefore creates the context and navigates IMMEDIATELY: no
wait, no warm-up, no earlier page in the same context. The names are known only to the FakeDoh (`race-<n>.test` are on no
zone of the machine and the gateway never resolves a name), so a native lookup ends in NS_ERROR_UNKNOWN_HOST and a page
that loads proves that the DoH, through the gateway of its context, resolved the name.

Three situations (the parameter):

* `context_proxy`: no launch proxy; 8 contexts created back-to-back, each with a gateway of its own and a distinct name,
  each navigated at once and closed. Every page loads, every name is asked only by the gateway of its context, none is
  ever asked `direct`, nothing reaches the DoH by a direct connection, and no DoH query or failed handshake belongs to the
  default context (which has no proxy and resolves natively);
* `browser_proxy`: a launch proxy and contexts WITHOUT a proxy of their own, navigated at once: they use the launch proxy
  (the DoH through it), as does the default context, so here nothing at all may go direct or ask the system resolver;
* `context_without_proxy_beside_a_proxied_one`: no launch proxy, a proxied context A and a context B without a proxy.
  A name is not hermetic for B (the names the system resolver knows are the host's), so B navigates by ADDRESS
  (`http://127.0.0.1:<port>/`, which needs no name) before and after A has used the DoH, and the test shows that B never
  borrowed the proxy of A (its server is not in A's gateway log) and never shows up at the FakeDoh (the only queries are
  A's names, by A's gateway).

Where the resolver trap runs (Linux, root) it adds the direct proof: it is never asked for a name of the proxied contexts.
On Windows the native question cannot be observed without a resolver trap, so there the proof is the one above: the page of
a name known only to the DoH loads, and the DoH log and the gateways account for every name.

Fixtures, FakeDoh, gateway, watcher, trap and the accounting of the end of a test are those of
`test_doh_through_the_proxy_e2e.py` (imported, not copied).
"""
from __future__ import annotations

import pytest

from test_doh_through_the_proxy_e2e import (  # noqa: F401  (fixtures are used by name)
    DOH_ADDRESSES, Pages, _asked_outside, _assert_every_connection_went_through_the_gateway,
    _assert_stayed_home, _direct_to_the_doh, _hermetic_session, _is_address, _launch, _page_in, _text, _why, doh, gateway,
    pages, trap, watcher)

from _doh_servers import DOH_HOST, QTYPE_A

pytestmark = pytest.mark.e2e

#: Contexts created back-to-back in one session.
ROUNDS = 8
#: A navigation that cannot resolve its name must fail fast (NS_ERROR_UNKNOWN_HOST), not wait the default 30 s per round.
GOTO_MS = 20000


def _race_names(prefix):
    return ["%s-%d.test" % (prefix, i) for i in range(ROUNDS)]


def _navigate_at_once(browser, pages, name, proxy):
    """Create a context (with `proxy` when given), open its first page and navigate at once, with nothing in between.
    Returns (text of the page or None, error text or None). The context is closed either way."""
    context, page = _page_in(browser, proxy=proxy)
    try:
        page.goto(pages.url(name), wait_until="load", timeout=GOTO_MS)
        return _text(page), None
    except Exception as exc:  # noqa: BLE001 - the failure is the result: it is reported with the other rounds
        return None, "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0] if str(exc) else "")
    finally:
        context.close()


def _names_asked_to_the_system_resolver(trap, names):
    """The trap's questions about one of `names` or a name under one (None when there is no trap)."""
    if trap is None:
        return None
    return [q for q in trap.queries if any(q.name == n or q.name.endswith("." + n) for n in names)]


@pytest.mark.parametrize("mode", ["context_proxy", "browser_proxy", "context_without_proxy_beside_a_proxied_one"])
def test_the_first_navigation_of_a_fresh_context_never_asks_the_system_resolver(
        mode, firefox_binary, doh, pages, gateway, trap, watcher):
    if mode == "context_without_proxy_beside_a_proxied_one":
        _beside_a_proxied_context(firefox_binary, doh, pages, gateway, trap, watcher)
    else:
        _back_to_back_contexts(mode, firefox_binary, doh, pages, gateway, trap, watcher)


def _back_to_back_contexts(mode, firefox_binary, doh, pages, gateway, trap, watcher):
    names = _race_names("race-" + mode.split("_")[0])
    doh.names.update({n: "127.0.0.1" for n in names})
    if mode == "context_proxy":
        # no launch proxy; one gateway per context, so "asked only by the gateway of its own context" is checkable
        launch_gateway, roads = None, {n: gateway("gw-%d" % i) for i, n in enumerate(names)}
    else:
        # a launch proxy; the contexts have none of their own and inherit it
        launch_gateway = gateway("launch")
        roads = {n: launch_gateway for n in names}
    gateways = list(dict.fromkeys(roads.values()))
    outcomes = {}

    with _launch(firefox_binary, launch_gateway.proxy if launch_gateway else None) as browser:
        for name in names:
            own_proxy = roads[name].proxy if launch_gateway is None else None
            outcomes[name] = _navigate_at_once(browser, pages, name, own_proxy)
    watcher.stop()

    why = _why(doh, *gateways)
    failed = {n: err for n, (text, err) in outcomes.items() if text is None}
    assert failed == {}, (
        "%d of %d first navigations failed: the name, known only to the DoH, was not resolved by it through the proxy of "
        "its context (a native lookup would end in NS_ERROR_UNKNOWN_HOST): %s" % (len(failed), ROUNDS, failed) + why)
    wrong = {n: text for n, (text, _e) in outcomes.items() if "HOST=%s:%d" % (n, pages.port) not in text}
    assert wrong == {}, "a page loaded but is not the page of its name: %s" % wrong + why

    for name in names:
        road = roads[name]
        assert doh.vias_of(name, QTYPE_A) == {road.name}, (
            "%s (A) was not asked by %s (the proxy of its context) only: %s" % (name, road.name, doh.vias_of(name)) + why)
        assert not [h for h, _p in road.connects if h == name], (
            "the browser sent the NAME %s to the gateway instead of resolving it with the DoH" % name + why)
        assert ("127.0.0.1", pages.port) in road.connects, "%s was not reached by address through %s" % (name, road.name) + why
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy of its context" + why
    for gw in gateways:
        assert gw.doh_connects(), "%s carried no DoH CONNECT" % gw.name + why
        assert [h for h, _p in gw.connects if not _is_address(h) and h != DOH_HOST] == [], (
            "a CONNECT of %s carried a name: the browser must resolve with the DoH and connect by address" % gw.name + why)
    assert pages.hits and {host.split(":")[0] for host, _p in pages.hits} == set(names), (
        "the page server saw hosts other than the names of the rounds: %s" % pages.hits)
    _assert_every_connection_went_through_the_gateway(doh, *gateways)

    assert watcher.watched_pids, ("the watcher never saw a browser process (it saw: %s): a watcher that cannot see proves "
                                  "nothing" % watcher.process_names)
    assert _direct_to_the_doh(watcher) == [], (
        "the browser reached a DoH (%s) or a DNS server without the proxy of its context: %s"
        % (", ".join(DOH_ADDRESSES), watcher.describe()) + why)
    asked_natively = _names_asked_to_the_system_resolver(trap, names)
    if asked_natively is not None:
        assert asked_natively == [], (
            "the system resolver was asked for a name of a proxied context: %s" % trap.describe() + why)

    if launch_gateway is None:
        # No launch proxy: the default context is the machine's own network. It resolves natively and never uses the DoH:
        # no name but the rounds' in the DoH log, and no failed handshake (only the default context lacks the certificate
        # exception, so a failed handshake is a lookup of the default context that reached the DoH).
        assert _asked_outside(doh, *names) == [], (
            "the DoH was asked for a name that is not one of the proxied contexts' (the default context has no proxy and "
            "must resolve natively): %s" % _asked_outside(doh, *names) + why)
        assert doh.tls_failures == [], "a context without the FakeDoh certificate exception used the DoH: %s" % doh.tls_failures + why
    else:
        # The launch proxy is the proxy of the default context too: its background names go to the DoH through the launch
        # proxy (their handshakes fail BY DESIGN and are counted by the accounting above), so here nothing may go direct or
        # ask the system resolver at all.
        _assert_stayed_home(watcher, trap, doh, launch_gateway)


def _beside_a_proxied_context(firefox_binary, doh, pages, gateway, trap, watcher):
    name_a = "race-beside-a.test"
    doh.names[name_a] = "127.0.0.1"
    gw_a = gateway("gw-a")
    home_b = Pages()  # B's own server: whose gateway log it is in says which road B took
    try:
        with _launch(firefox_binary, None) as browser:
            context_b, page_b = _page_in(browser)                         # no proxy of its own, no launch proxy
            context_a, page_a = _page_in(browser, proxy=gw_a.proxy)       # created while B lives
            page_b.goto(home_b.url("127.0.0.1"), wait_until="load", timeout=GOTO_MS)
            first_b = _text(page_b)
            page_a.goto(pages.url(name_a), wait_until="load", timeout=GOTO_MS)
            text_a = _text(page_a)
            page_b.goto(home_b.url("127.0.0.1") + "again", wait_until="load", timeout=GOTO_MS)  # after A has used the DoH
            second_b = _text(page_b)
            context_a.close()
            context_b.close()
        watcher.stop()

        why = _why(doh, gw_a)
        assert "HOST=%s:%d" % (name_a, pages.port) in text_a, (
            "A's page did not load: the name, known only to the DoH, was not resolved by it through A's proxy: %r" % text_a + why)
        assert "HOST=127.0.0.1:%d" % home_b.port in first_b and "HOST=127.0.0.1:%d" % home_b.port in second_b, (
            "B's pages did not load by address: %r %r" % (first_b, second_b) + why)
        assert [path for _h, path in home_b.hits if path != "/favicon.ico"] == ["/", "/again"], "B's server saw: %s" % home_b.hits
        assert doh.vias_of(name_a, QTYPE_A) == {"gw-a"}, "%s (A) was not asked by A's gateway only" % name_a + why
        assert ("127.0.0.1", pages.port) in gw_a.connects, "A's page was not reached by address through A's gateway" + why
        assert ("127.0.0.1", home_b.port) not in gw_a.connects, (
            "B's page went through A's gateway: B, which has no proxy, borrowed the proxy of A" + why)
        # B's lookups are the machine's own and never show up at the FakeDoh: only A's name was asked, by A's gateway.
        assert _asked_outside(doh, name_a) == [], (
            "the FakeDoh was asked for a name that is not A's (B and the default context have no proxy and must not use the "
            "DoH): %s" % _asked_outside(doh, name_a) + why)
        assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without A's proxy" + why
        assert doh.tls_failures == [], "a context without the FakeDoh certificate exception used the DoH: %s" % doh.tls_failures + why
        _assert_every_connection_went_through_the_gateway(doh, gw_a)

        assert watcher.watched_pids, ("the watcher never saw a browser process (it saw: %s): a watcher that cannot see "
                                      "proves nothing" % watcher.process_names)
        assert _direct_to_the_doh(watcher) == [], (
            "the browser reached a DoH or a DNS server without the proxy: %s" % watcher.describe() + why)
        asked_natively = _names_asked_to_the_system_resolver(trap, [name_a])
        if asked_natively is not None:
            assert asked_natively == [], "the system resolver was asked for A's name: %s" % trap.describe() + why
    finally:
        home_b.close()
