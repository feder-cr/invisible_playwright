"""E2E: the DoH of a proxied context when it is slow, and when it refuses once.

The engine resolves the names of a context with a proxy by the DoH service, which stays in global mode 0, and asks it
with a per-request TRR_ONLY (fail-closed: no native fallback). Mozilla does not exercise that combination: its TRR-only
is the GLOBAL mode 3, and several timeouts and counters of `TRRService` depend on the global mode. These two tests put
the combination under the two stresses a real DoH gives, on the layout of `test_context_proxy_only_resolves_by_doh_e2e.py`
(no launch proxy, one context with its own SOCKS5 gateway; the FakeDoh can be reached only through it, and only it
knows the names used here, so a page loads only if the lookup went to the DoH).

* `test_a_slow_doh_still_resolves_the_name_of_a_proxied_context`: the FakeDoh holds EVERY answer for `slow-a.test` for 2 s
  (`delay_names`; each response is held after its query arrived, the A and the AAAA of the lookup together, so the
  connection to the DoH is up at once and only the answer is late: this is what the browser's DoH request timeout
  measures). In global mode 0 `TRRService::GetRequestTimeout` returns `network.trr.request_timeout_ms` (1.5 s); in global
  mode 3 it returns `network.trr.request_timeout_mode_trronly_ms` (30 s). A request made TRR_ONLY per request under a global
  mode 0 would keep the 1.5 s one and fail a lookup whose answer is only 2 s away, with nothing to fall back to. The page
  must load, within 20 s, and the FakeDoh log must show the queries answered after the delay.
* `test_a_name_the_doh_refused_once_resolves_on_the_next_request`: the FakeDoh refuses the FIRST query of each type for
  `flaky-a.test` by ending the exchange with no answer (a reset h2 stream: a failure of the CHANNEL, the kind
  `ConfirmationContext::RecordTRRStatus` counts; `_doh_servers.FAILURE_KINDS` has the other two) and serves it afterwards.
  The first navigation may fail (NS_ERROR_UNKNOWN_HOST) or load; then a retry loop (up to 5 navigations 2 s apart, each in a
  fresh page of the same context, nothing cleared) must load within 15 s AND the FakeDoh must have been asked a second time:
  the browser asks again instead of serving the failure from a cache or from a temporary block that a global TRR-only
  mode would not have (`IsTemporarilyBlocked` returns false under global mode 3, and its blocklist is fed by failures
  counted under the global modes).

Red on an engine with the 1.5 s DoH timeout (the giro-2 and giro-3 binaries before the timeout fix) for the first test:
the lookup of `slow-a.test` fails after 1.5 s, the navigation ends in NS_ERROR_UNKNOWN_HOST and the page never loads. The
second test is red if the failure of the first lookup sticks (negative cache or temporary block) longer than the retry loop.
"""
from __future__ import annotations

import time

import pytest

# The fixtures and the helpers of the DoH e2e (imported, not copied): the hermetic session (autouse), the resolver trap,
# the watcher, the page server, the gateway factory, and the accounting of the connections to the FakeDoh.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (the hermetic session is autouse and imported by name; the
    # other fixtures of the DoH e2e are in conftest.py)
    DOH_ADDRESSES, _asked_outside, _assert_every_connection_went_through_the_gateway, _direct_to_the_doh,
    _hermetic_session, _is_address, _launch, _page_in, _text, _why)

import _doh_servers as ds
from _doh_servers import DOH_BOOTSTRAP, DOH_HOST, DOH_PORT, QTYPE_A, QTYPE_AAAA

pytestmark = pytest.mark.e2e

#: The latency of the slow DoH, and what a page behind it may take at most.
DOH_LATENCY_S = 2.0
SLOW_LOAD_LIMIT_S = 20.0
#: The retries after a refusal: how many, how far apart, and the time they all have.
RETRIES = 5
RETRY_GAP_S = 2.0
RETRY_WINDOW_S = 15.0


#: Firefox keeps the failure of ANY lookup (a refused DoH query included) in the DNS cache for network.dnsNegativeCacheExpiration
#: = 60 s, plus 60 s of grace (network.dnsNegativeCacheExpirationGracePeriod): retail behaviour, under a global TRR-only mode
#: too, measured by reading nsHostResolver.cpp:1333-1349 (not by a run). To isolate what this test is about (no TEMPORARY BLOCK
#: of the DoH and no stickiness beyond that cache) the session shortens the cache to 1 s and drops the grace period: the
#: refusal is then forgotten within the retry window. This only touches a cache; the code path of the lookup is the same.
SHORT_NEGATIVE_CACHE = {"network.dnsNegativeCacheExpiration": 1, "network.dnsNegativeCacheExpirationGracePeriod": 0}


def _launch_with_a_short_negative_cache(binary):
    """A session without a launch proxy (the proxy is the context's) whose DNS negative cache is 1 s."""
    from _doh_support import a_display_when_the_host_has_none
    from invisible_playwright import InvisiblePlaywright
    from test_doh_through_the_proxy_e2e import PREFS
    return InvisiblePlaywright(seed=42, binary_path=binary, proxy=None, extra_prefs={**PREFS, **SHORT_NEGATIVE_CACHE},
                               **a_display_when_the_host_has_none())


def _navigate(context, url, timeout_ms):
    """(text or None, error or None, seconds) of one navigation in a fresh page of `context`, which is closed after."""
    page = context.new_page()
    started = time.monotonic()
    try:
        page.goto(url, wait_until="load", timeout=timeout_ms)
        return _text(page), None, time.monotonic() - started
    except Exception as exc:  # noqa: BLE001 - the failure is the result
        return None, "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0] if str(exc) else ""), \
            time.monotonic() - started
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001 - a page that cannot be closed is not the subject
            pass


def _only_the_doh_road_was_used(doh, gw, watcher):
    """The layout assertions of the context-only e2e: the DoH was reached by the gateway of the context, by address,
    and nothing went past it."""
    why = _why(doh, gw)
    assert (DOH_BOOTSTRAP, DOH_PORT) in gw.connects, "no DoH CONNECT to the bootstrap address reached the gateway" + why
    assert [h for h, _p in gw.connects if not _is_address(h) and h != DOH_HOST] == [], (
        "a CONNECT carried a name: the browser must resolve with the DoH and connect by address" + why)
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy of the context" + why
    _assert_every_connection_went_through_the_gateway(doh, gw)
    # Context-only layout: the default context resolves natively and never uses the DoH, so a failed handshake (the lookup
    # of a context that does not accept the FakeDoh certificate) is a lookup of the wrong context sent to the DoH.
    assert doh.tls_failures == [], "a DoH handshake failed: the default context used the DoH: %s" % doh.tls_failures + why
    assert watcher.watched_pids, ("the watcher never saw a browser process (it saw: %s): a watcher that cannot see proves "
                                  "nothing" % watcher.process_names)
    assert _direct_to_the_doh(watcher) == [], (
        "the browser reached a DoH (%s) or a DNS server (port 53) without the proxy: %s"
        % (", ".join(DOH_ADDRESSES), watcher.describe()))


def test_a_slow_doh_still_resolves_the_name_of_a_proxied_context(firefox_binary, doh, pages, gateway, watcher,
                                                                 record_property):
    """`slow-a.test` is known only to the FakeDoh, which answers it 2 s after the query. The page of a context with a
    proxy of its own must load within 20 s, reached by address through that proxy."""
    doh.names["slow-a.test"] = "127.0.0.1"
    doh.delay_names["slow-a.test"] = DOH_LATENCY_S
    gw = gateway("gw")
    text = error = None
    seconds = 0.0
    with _launch(firefox_binary, None) as browser:             # no launch proxy: only the context has one
        context, page = _page_in(browser, proxy=gw.proxy)
        started = time.monotonic()
        try:
            page.goto(pages.url("slow-a.test"), wait_until="load", timeout=int(SLOW_LOAD_LIMIT_S * 1000))
            text = _text(page)
        except Exception as exc:  # noqa: BLE001 - reported below with what the FakeDoh saw
            error = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0] if str(exc) else "")
        seconds = time.monotonic() - started
        context.close()
    watcher.stop()
    record_property("goto_seconds", "%.2f" % seconds)

    answers = doh.answers_for("slow-a.test")
    why = _why(doh, gw) + "\n  FakeDoh answers (name, qtype, via, outcome, seconds held): %s\n  goto took %.2f s" % (
        [tuple(a) for a in doh.answers], seconds)
    assert error is None and text is not None, (
        "the page behind a DoH that answers in %.0f s did not load (%s): the DoH request of a lookup made TRR_ONLY per "
        "request gave up before the answer (the 1.5 s timeout of global mode 0?)" % (DOH_LATENCY_S, error) + why)
    assert "HOST=slow-a.test:%d" % pages.port in text, "the page is not the page of the name: %r" % text + why
    assert seconds < SLOW_LOAD_LIMIT_S, "the page took %.1f s to load, the limit is %.0f s" % (seconds, SLOW_LOAD_LIMIT_S) + why
    assert seconds >= DOH_LATENCY_S - 0.2, (
        "the page loaded in %.2f s, before the DoH could have answered (%.0f s): the name was not resolved by it"
        % (seconds, DOH_LATENCY_S) + why)
    assert [a for a in answers if a.qtype == QTYPE_A], "the DoH was never asked for the A record of the name" + why
    assert answers and all(a.outcome == "answered" for a in answers), "the DoH refused something here" + why
    assert min(a.delay for a in answers) >= 1.8, (
        "a query was answered after only %.2f s: the FakeDoh did not hold it (A and AAAA: %s)"
        % (min(a.delay for a in answers), [(a.qtype, round(a.delay, 2)) for a in answers]) + why)
    assert doh.vias_of("slow-a.test", QTYPE_A) == {"gw"}, "slow-a.test (A) was not asked by gw (the proxy of the context)" + why
    assert not [h for h, _p in gw.connects if h == "slow-a.test"], "the browser sent the NAME to the gateway" + why
    assert ("127.0.0.1", pages.port) in gw.connects, "the page was not reached by address through the gateway" + why
    assert _asked_outside(doh, "slow-a.test") == [], (
        "the DoH was asked for a name that is not the proxied context's: %s" % _asked_outside(doh, "slow-a.test") + why)
    _only_the_doh_road_was_used(doh, gw, watcher)


def test_a_name_the_doh_refused_once_resolves_on_the_next_request(firefox_binary, doh, pages, gateway, watcher,
                                                                  record_property):
    """The FakeDoh ends the exchange of the first A (and the first AAAA) query for `flaky-a.test` with no answer, then
    serves the name. The first navigation may fail with NS_ERROR_UNKNOWN_HOST or load; a retry (a fresh page of the same
    context, 2 s apart, up to 5 times, within 15 s) must load, and the browser must have asked the DoH again. The
    DNS negative cache (60 s + 60 s of grace in a release, the same under a global TRR-only mode) is shortened to 1 s
    for this test (SHORT_NEGATIVE_CACHE): without it the retries would be answered from the cache, as in a retail
    Firefox, and the test would say nothing about a temporary block of the DoH.

    WHAT IT MEASURES: that a lookup the DoH refused once is not blocked by anything of the DoH itself (no temporary
    blocklist, no failure counter: under a per-request TRR_ONLY with the service in global mode 0 neither applies, by
    reading TRRService.cpp:1025-1066, :1126, :1250-1298 and TRR.cpp:177-217) and that the browser asks the DoH again
    (a second A query at the FakeDoh) and resolves the name when it is served. WHAT IT DOES NOT MEASURE: how long Firefox
    keeps the failure of a lookup in its DNS cache (60 s + 60 s of grace in a release, retail behaviour, the same in a
    global TRR-only mode); that duration is shortened here on purpose and is not tested."""
    doh.names["flaky-a.test"] = "127.0.0.1"
    doh.fail_first["flaky-a.test"] = "close"
    gw = gateway("gw")
    url = pages.url("flaky-a.test")
    attempts = []                       # (text or None, error or None, seconds)
    with _launch_with_a_short_negative_cache(firefox_binary) as browser:
        context, page = _page_in(browser, proxy=gw.proxy)
        page.close()
        attempts.append(_navigate(context, url, 20000))
        window_starts = time.monotonic()
        while attempts[-1][0] is None and len(attempts) <= RETRIES:
            remaining = RETRY_WINDOW_S - (time.monotonic() - window_starts)
            if remaining <= 0:
                break
            time.sleep(min(RETRY_GAP_S, remaining))
            remaining = RETRY_WINDOW_S - (time.monotonic() - window_starts)
            if remaining <= 0:
                break
            attempts.append(_navigate(context, url, int(min(8.0, remaining) * 1000)))
        context.close()
    watcher.stop()
    record_property("attempts", str([(ok is not None, err, round(s, 2)) for ok, err, s in attempts]))

    queries = doh.queries_for("flaky-a.test", QTYPE_A)
    why = _why(doh, gw) + "\n  FakeDoh answers (name, qtype, via, outcome, seconds held): %s\n  navigations (text, error, s): %s" % (
        [tuple(a) for a in doh.answers], attempts)
    first_text, first_error, _s = attempts[0]
    assert first_text is not None or "NS_ERROR_UNKNOWN_HOST" in (first_error or ""), (
        "the first navigation failed in a way that is not the refusal of the name: %s" % first_error + why)
    refused = [a for a in doh.answers_for("flaky-a.test") if a.outcome == "close"]
    assert {a.qtype for a in refused} <= {QTYPE_A, QTYPE_AAAA} and [a for a in refused if a.qtype == QTYPE_A], (
        "the FakeDoh never refused the first A query of the name: the test did not exercise a refusal" + why)
    loaded = [a for a in attempts if a[0] is not None]
    assert loaded, (
        "no navigation to a name the DoH refused once loaded in %d attempts / %.0f s: the failure of the first lookup "
        "sticks (a cached failure or a temporary block)" % (len(attempts), RETRY_WINDOW_S) + why)
    assert "HOST=flaky-a.test:%d" % pages.port in loaded[0][0], "the page is not the page of the name: %r" % loaded[0][0] + why
    assert len(queries) >= 2, (
        "the DoH was asked for the A record of the name %d time(s): the browser must ask again after a refusal, not serve "
        "a cached failure beyond the DoH's own negative TTL" % len(queries) + why)
    assert [a for a in doh.answers_for("flaky-a.test", QTYPE_A) if a.outcome == "answered"], (
        "the DoH never answered the name after the refusal" + why)
    assert doh.vias_of("flaky-a.test", QTYPE_A) == {"gw"}, "flaky-a.test (A) was not asked by gw only" + why
    assert not [h for h, _p in gw.connects if h == "flaky-a.test"], "the browser sent the NAME to the gateway" + why
    assert _asked_outside(doh, "flaky-a.test") == [], (
        "the DoH was asked for a name that is not the proxied context's: %s" % _asked_outside(doh, "flaky-a.test") + why)
    _only_the_doh_road_was_used(doh, gw, watcher)
