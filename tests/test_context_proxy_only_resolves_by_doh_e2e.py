"""E2E: the rule "a context WITH a proxy resolves by DoH through it, a context WITHOUT one resolves natively and never
uses the DoH", on the case where the two meet: a session with NO launch proxy and a context with a proxy of its own.

The session is launched without a proxy (the DEFAULT context is the browser's own network: it resolves natively), then a
context is made with its own SOCKS5 gateway (`new_context(proxy=..., ignore_https_errors=True)`) and loads
`http://page-a.test:<port>/`. The name `page-a.test` is one that only the FakeDoh can resolve: the machine's resolver
cannot (a `.test` name is on no zone), and the gateway never resolves a name (it refuses a CONNECT that carries one). So
the page loads only if the lookup of that context went to the DoH, through the proxy it was handed, even though no proxy
exists at launch: the choice follows the proxy of the CONTEXT of each lookup, not the proxy of the launch.

The same test is the other half of the rule. While the proxied context loads its page, the default context is busy with
its own background lookups (telemetry, update and extension endpoints...), and those are the machine's own network:

* they must NOT go to the DoH: nothing connects to the bootstrap address or to port 53 by a direct road (the watcher), the
  FakeDoh has no query for a name that is not one of the zone names this test asked (a background name of the default
  context in `doh.queries` is a lookup of the wrong context sent to the DoH), and the FakeDoh has no failed handshake (a
  lookup of a context without `ignore_https_errors` can only fail it, so a failure IS a default-context lookup sent to the
  DoH);
* they go to the system resolver. Where the resolver trap runs (Linux, root) the trap sees at least one question that is
  no zone name: the background question of the default context. On Windows the native question cannot be observed
  without a resolver trap, so there the assertion is the negative one above (nothing of that traffic reaches the DoH).

Red on an engine that picks the road of a lookup by the session instead of by the user context: the background lookups of
the default context go to the DoH by a DIRECT connection (the bootstrap address 192.0.2.53:443 is reached with no proxy;
the giro-2 binary), or, if the proxy resolves remotely, arrive at the gateway as a NAME (refused, listed in `refused`) and
the page does not load. Green when the resolver hook of the engine chooses per lookup by the user context.

Hermetic like the DoH e2e it borrows from (`test_doh_through_the_proxy_e2e.py`: the fixtures, the FakeDoh, the gateway, the
watcher, the trap and the accounting of the end of a test are imported, not copied), with one difference in what is asserted:
with no launch proxy the default context is the machine's own network, so its background traffic is allowed to be direct
and to ask the machine's resolver; every assertion here is about the DoH road, the zone's names and this context.
"""
from __future__ import annotations

import pytest

# The fixtures and the helpers of the DoH e2e (imported, not copied): the hermetic session (autouse), the resolver trap, the
# watcher, the page server, the gateway factory, and the accounting of the connections to the FakeDoh.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (the hermetic session is autouse and imported by name; the
    # other fixtures of the DoH e2e are in conftest.py)
    DOH_ADDRESSES, ZONE, _asked_outside, _assert_every_connection_went_through_the_gateway, _direct_to_the_doh,
    _hermetic_session, _is_address, _launch, _page_in, _text, _why)

import _doh_servers as ds
from _doh_servers import DOH_BOOTSTRAP, DOH_HOST, DOH_PORT, QTYPE_A

pytestmark = pytest.mark.e2e

#: How long to wait, where the resolver trap runs, for the background question of the default context to reach it.
BACKGROUND_QUESTION_SECONDS = 20


def test_a_context_that_is_the_only_proxy_resolves_its_names_by_doh_through_it(firefox_binary, doh, pages, gateway, trap,
                                                                              watcher):
    """No proxy at launch; the context has gw. `page-a.test` is asked to the FakeDoh BY gw, the page is reached by ADDRESS
    through gw, nothing reaches the DoH directly, the machine's resolver is never asked for the name, and the background
    names of the default context neither reach the DoH nor are lost: they go to the system resolver."""
    gw = gateway("gw")
    background_question = None
    with _launch(firefox_binary, None) as browser:             # no launch proxy: the session itself is on the host's network
        context, page = _page_in(browser, proxy=gw.proxy)      # the only proxy there is: the context's own
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        text = _text(page)
        if trap is not None:
            # The default context asks the system resolver for its own background names while the session lives; the trap
            # answers nothing but REFUSED, so the question is all that is left of them. Wait for it here, before teardown.
            background_question = ds.wait_until(
                lambda: [q for q in trap.queries if q.name not in ZONE], BACKGROUND_QUESTION_SECONDS)
        context.close()
    watcher.stop()

    why = _why(doh, gw)
    assert "HOST=page-a.test:%d" % pages.port in text, (
        "the page did not load: the name page-a.test, which only the FakeDoh can resolve, was not resolved by DoH through "
        "the proxy of the context (the lookup of a context with a proxy did not go to the DoH?): %r" % text + why)
    assert doh.vias_of("page-a.test", QTYPE_A) == {"gw"}, "page-a.test (A) was not asked by gw (the proxy of the context) only" + why
    assert (DOH_BOOTSTRAP, DOH_PORT) in gw.connects, "no DoH CONNECT to the bootstrap address reached the gateway of the context" + why
    assert not [h for h, _p in gw.connects if h == "page-a.test"], (
        "the browser sent the NAME page-a.test to the gateway instead of resolving it with the DoH" + why)
    assert [h for h, _p in gw.connects if not _is_address(h) and h != DOH_HOST] == [], (
        "a CONNECT carried a name: the browser must resolve with the DoH and connect by address" + why)
    assert ("127.0.0.1", pages.port) in gw.connects, "the page was not reached by address through the gateway of the context" + why
    assert pages.hits and all(host.startswith("page-a.test") for host, _p in pages.hits), pages.hits
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy of the context" + why
    _assert_every_connection_went_through_the_gateway(doh, gw)

    # The names the DoH was asked for are the names of THIS context and no other: a background name of the default context
    # (a mozilla.org or github.com name...) in the log is a lookup of a context without a proxy sent to the DoH.
    assert _asked_outside(doh, "page-a.test") == [], (
        "the DoH was asked for a name that is not the proxied context's: the default context, which has no proxy, must "
        "resolve natively and never use the DoH: %s" % _asked_outside(doh, "page-a.test") + why)
    # A lookup of a context without `ignore_https_errors` (the default one) can only fail the handshake with the FakeDoh:
    # a failed handshake is a lookup of the wrong context that reached the DoH through this gateway.
    assert doh.tls_failures == [], (
        "a DoH handshake failed: a context that does not accept the FakeDoh certificate (the default context, which has no "
        "proxy) used the DoH: %s" % doh.tls_failures + why)

    assert watcher.watched_pids, ("the watcher never saw a browser process (it saw: %s): a watcher that cannot see proves "
                                  "nothing" % watcher.process_names)
    assert _direct_to_the_doh(watcher) == [], (
        "the browser reached a DoH (%s) or a DNS server (port 53) without the proxy: the background lookups of the default "
        "context, which has no proxy, went to the DoH instead of the system resolver: %s"
        % (", ".join(DOH_ADDRESSES), watcher.describe()))
    if trap is not None:
        assert [name for name in ZONE if trap.asked(name)] == [], (
            "the browser asked the resolver of the machine for a name of the zone: %s" % trap.describe() + why)
        assert background_question, (
            "the system resolver was asked nothing but zone names (nothing at all, here): the background names of the "
            "default context did not go to it. They went to the DoH, or the default context made no lookup in %d s: %s"
            % (BACKGROUND_QUESTION_SECONDS, trap.describe()) + why)
    # Where the trap cannot run (Windows, or Linux without root) the native question of the default context cannot be
    # observed: the assertion for it is the negative one above (no direct DoH connection, no query outside the zone, no
    # failed handshake).
