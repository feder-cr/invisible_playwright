"""E2E: nothing a PAGE makes the browser do leaves except through the proxy of its context.

Why it is a file of its own. The DoH e2e (``test_doh_through_the_proxy_e2e.py``, ``test_context_proxy_only_resolves_by_doh_e2e.py``) prove the
road of the names a page NAVIGATES to and of one STUN name. A page does much more than that, and each kind of work is its own road inside the
browser, written by its own code (the preconnect and prefetch hints, the image loader, the WebSocket and EventSource channels, the ICE
resolver and its sockets, the geolocation provider, the service worker and the push service, a request still in flight when its context
dies). A real-sites run found the main process asking the machine's resolver for names of pages loaded behind a SOCKS proxy and opening a
few TLS connections straight to sites: nothing in the navigations of the DoH e2e could have shown it.

Each kind of page work is tried in a context of its own, on the scheme that shows a leak (``test_context_proxy_only_resolves_by_doh_e2e.py``):

* ``context_only``: NO proxy at launch, a context with a SOCKS5 gateway of its own. The default context is the machine's own network, so a
  lookup that takes the wrong user context takes the system resolver and the page's name never reaches the DoH;
* ``launch_and_context``: a launch gateway AND a different gateway for the context. The default context is proxied too, so nothing at all
  may go direct or ask the resolver, and a name of the page must reach the DoH by the gateway of the CONTEXT, never by the one of the launch
  (a lookup with the default user context would leave by the wrong exit).

What each case proves (the page work is in ``_page_work.py``; every name points at a documentation address, never at loopback):

* no question to the resolver of the machine for a ``.test`` name (the resolver trap, where it runs: Linux, root; with a launch gateway no
  question at all);
* no direct connection: the watcher sees none to a DoH address, to port 53 or to a documentation address (with a launch gateway, none at all);
* every name the page work needs is asked of the DoH, and only by the gateway of the context;
* every CONNECT a gateway got carries an ADDRESS, never a name;
* the work was done (the page opened, and the names the case must have asked were asked): a leak test that measures nothing is not green.

The TURN server given by NAME over TCP is not here: it is the reproducer of an engine defect and has its own file
(``test_a_turn_name_over_tcp_goes_through_the_proxy_of_the_context_e2e.py``).

Hermetic like the DoH e2e it borrows from: its fixtures, FakeDoh, gateway, watcher and trap are imported, not copied.
"""
from __future__ import annotations

import time

import pytest

# The fixtures and the helpers of the DoH e2e (imported, not copied): the hermetic session (autouse), the launch, the page in a context,
# and the accounting of the end of a test. `doh`, `gateway`, `trap` and `watcher` are in conftest.py.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (the hermetic session is autouse and imported by name)
    DOH_ADDRESSES, _assert_every_connection_went_through_the_gateway, _direct_to_the_doh, _hermetic_session, _is_address, _launch,
    _page_in, _why)

import _doh_servers as ds
from _doh_servers import DOH_HOST, QTYPE_A, FakeDoh
from _page_work import ASKED, CASES, SETTLE_SECONDS, ZONE, CaseSite, is_documentation_address, site_name

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

SCHEMES = ["context_only", "launch_and_context"]
#: How long to wait for a name the case must have asked to reach the DoH, in seconds.
ASKED_SECONDS = 25
#: How long the page of the `push` case may take to register its service worker and read its subscription, in seconds.
PUSH_SECONDS = 40
#: After the context of the `closing` case is closed, the browser lives on this long: the requests of the page's last moments are made now.
AFTER_CLOSE_SECONDS = 4


@pytest.fixture
def doh():
    """The FakeDoh with the names of the page work (overrides the one of conftest.py)."""
    import h2  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip

    server = FakeDoh(ZONE)
    yield server
    server.close()


@pytest.fixture
def site():
    server = CaseSite()
    yield server
    server.close()


def _direct_to_a_documentation_address(watcher):
    """The direct connections the watcher saw to the addresses the page work's names answer (loopback is never seen as direct)."""
    return [c for c in watcher.direct if is_documentation_address(c.remote.rsplit(":", 1)[0])]


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("case", list(CASES))
def test_a_kind_of_page_work_leaves_only_through_the_proxy_of_its_context(case, scheme, firefox_binary, doh, site, gateway, trap,
                                                                       watcher):
    gw_context = gateway("gw-context")
    gw_launch = gateway("gw-launch") if scheme == "launch_and_context" else None
    gateways = [g for g in (gw_context, gw_launch) if g is not None]
    asked_names = ASKED[case] + ([site_name(case)] if site_name(case) else [])
    opened = None
    waited = {}
    with _launch(firefox_binary, gw_launch.proxy if gw_launch else None) as browser:
        context, page = _page_in(browser, proxy=gw_context.proxy)      # a proxy of its own: the only one in `context_only`
        try:
            page.goto(site.url(case), wait_until="load", timeout=30000)
            opened = True
        except Exception as exc:  # noqa: BLE001 - said below with everything the sides saw
            opened = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        for name in asked_names:
            waited[name] = ds.wait_until(lambda n=name: doh.queries_for(n, QTYPE_A), ASKED_SECONDS)
        page.wait_for_timeout(int(SETTLE_SECONDS[case] * 1000))
        if case == "push":
            # A service worker starts a process: wait for the page to leave `pending` (a fixed settle is a guess), and say the step it reached.
            try:
                page.wait_for_function("() => window.__push !== 'pending'", timeout=PUSH_SECONDS * 1000)
            except Exception:  # noqa: BLE001 - the state is read and said below
                pass
            pushed = page.evaluate("() => window.__push")
            push_stage = page.evaluate("() => window.__pushStage + ' after ' + window.__pushTries + ' ask(s)'")
        context.close()                                           # for `closing`: the page is still working
        if case == "closing":
            time.sleep(AFTER_CLOSE_SECONDS)
    watcher.stop()

    why = _why(doh, *gateways) + "\n  the page: opened=%r" % (opened,)
    assert opened is True, "the page of the case %r did not open through the gateway of its context: %s" % (case, opened) + why
    if case == "push":
        assert pushed.startswith("sub:"), (
            "the page could not register its service worker and read its subscription: %r (last step reached: %s)" % (pushed, push_stage) + why)

    # The work was done: the names it needs reached the DoH, by the gateway of the context and by no other road.
    for name in asked_names:
        assert waited[name], "the DoH was never asked for %s (the work of the case %r was not done, or its name took another road)" % (
            name, case) + why
        assert doh.vias_of(name, QTYPE_A) == {gw_context.name}, (
            "%s (A) was not asked by the gateway of the context only: %s" % (name, sorted(doh.vias_of(name, QTYPE_A))) + why)
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without a proxy" + why
    if gw_launch is not None:
        asked_by_launch = [q.name for q in doh.queries if q.via == gw_launch.name and q.name in ZONE]
        assert asked_by_launch == [], (
            "a name of the page work left by the gateway of the LAUNCH, not by the one of the context: %s" % (asked_by_launch,) + why)

    # Every CONNECT is an address: the browser resolves with the DoH and connects to what it answered.
    for gw in gateways:
        named = [h for h, _p in gw.connects if not _is_address(h) and h != DOH_HOST]
        assert named == [], "%s was sent a NAME in a CONNECT: %s (the browser must resolve with the DoH)" % (gw.name, named) + why
    _assert_every_connection_went_through_the_gateway(doh, *gateways)

    # Nothing direct.
    assert watcher.watched_pids, "the watcher never saw a browser process (it saw: %s): a watcher that cannot see proves nothing" % (
        watcher.process_names)
    assert _direct_to_the_doh(watcher) == [], (
        "the browser reached a DoH (%s) or a DNS server (port 53) without the proxy: %s" % (", ".join(DOH_ADDRESSES), watcher.describe()))
    assert _direct_to_a_documentation_address(watcher) == [], (
        "the browser connected straight to an address a name of the page work answers: %s" % watcher.describe())
    if gw_launch is not None:
        assert watcher.direct == [], "the browser connected past the gateways, with every context proxied: %s" % watcher.describe()

    # Nothing asked of the resolver of the machine.
    if trap is not None:
        if gw_launch is not None:
            assert trap.queries == [], "the browser asked the resolver of the machine, with every context proxied: %s" % trap.describe()
        else:
            asked = [name for name in trap.names() if name.endswith(".test")]
            assert asked == [], (
                "the browser asked the resolver of the machine for a name of the page work (the lookup took the user context of no "
                "proxy): %s" % asked + "; " + trap.describe())
