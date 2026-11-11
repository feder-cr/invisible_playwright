"""E2E: a session with NO launch proxy and a context with a proxy of its own resolves that context's names by DoH, through
that proxy.

The session is launched without a proxy (the browser's own network: TRR mode 0 by the contract of the engine), then a context
is made with its own SOCKS5 gateway (`new_context(proxy=..., ignore_https_errors=True)`) and loads `http://page-a.test:<port>/`.
The name `page-a.test` is one that only the FakeDoh can resolve: the machine's resolver cannot (a `.test` name is on no zone), and
the gateway never resolves a name (it refuses a CONNECT that carries one). So the page loads only if the engine turned TRR on for
the proxy it was handed, even though no proxy exists at launch: the state follows the proxies PRESENT, not the proxy of the
launch (`TrrPrefs.js`, engine commit 9bff9cf8de).

Red on an engine whose TRR stays in mode 0 when the only proxies are those of contexts: the name goes to the machine's
resolver (it fails; the resolver trap, where it can run, sees the question) or, if the proxy resolves remotely, arrives at the
gateway as a NAME (refused, listed in `refused`), and the page does not load. Green with 9bff9cf8de.

Hermetic like the DoH e2e it borrows from (`test_doh_through_the_proxy_e2e.py`: the fixtures, the FakeDoh, the gateway, the
watcher, the trap and the accounting of the end of a test are imported, not copied), with one difference in what is asserted:
with no launch proxy the DEFAULT context is the machine's own network, so its background traffic is allowed to be direct and
to ask the machine's resolver; every assertion here is therefore about the DoH road, the zone's names and this context.
"""
from __future__ import annotations

import pytest

# The fixtures and the helpers of the DoH e2e (imported, not copied): the hermetic session (autouse), the resolver trap, the
# watcher, the page server, the gateway factory, and the accounting of the connections to the FakeDoh.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (fixtures are used by name)
    ZONE, _assert_every_connection_went_through_the_gateway, _hermetic_session, _is_address, _launch, _page_in, _text,
    _why, doh, gateway, pages, trap, watcher)

from _doh_servers import DOH_BOOTSTRAP, DOH_HOST, DOH_PORT, QTYPE_A

pytestmark = pytest.mark.e2e

#: The addresses a DoH request could reach by a road other than the context's proxy: the bootstrap address of the test's
#: provider and the one of the core's default provider.
DOH_ADDRESSES = (DOH_BOOTSTRAP, "162.159.61.4")


def test_a_context_that_is_the_only_proxy_resolves_its_names_by_doh_through_it(firefox_binary, doh, pages, gateway, trap,
                                                                              watcher):
    """No proxy at launch; the context has gw. `page-a.test` is asked to the FakeDoh BY gw, the page is reached by ADDRESS
    through gw, nothing reaches the DoH directly, and the machine's resolver is never asked for the name."""
    gw = gateway("gw")
    with _launch(firefox_binary, None) as browser:             # no launch proxy: the session itself is on the host's network
        context, page = _page_in(browser, proxy=gw.proxy)      # the only proxy there is: the context's own
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        text = _text(page)
        context.close()
    watcher.stop()

    why = _why(doh, gw)
    assert "HOST=page-a.test:%d" % pages.port in text, (
        "the page did not load: the name page-a.test, which only the FakeDoh can resolve, was not resolved by DoH through "
        "the proxy of the context (TRR stays off when the only proxy is a context's?): %r" % text + why)
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

    assert watcher.watched_pids, ("the watcher never saw a browser process (it saw: %s): a watcher that cannot see proves "
                                  "nothing" % watcher.process_names)
    direct_to_doh = [c for c in watcher.direct
                     if c.remote.rsplit(":", 1)[0] in DOH_ADDRESSES or c.remote.endswith(":53")]
    assert direct_to_doh == [], "the browser reached a DoH or a DNS server without the proxy: %s" % watcher.describe()
    if trap is not None:
        assert [name for name in ZONE if trap.asked(name)] == [], (
            "the browser asked the resolver of the machine for a name of the zone: %s" % trap.describe() + why)
