"""E2E, characterization: what a proxy receives, and what `page.goto` sees when the proxy does not carry the DoH.

Behind a proxy the engine firefox-63 resolves every name ONLY by its own DoH, through the proxy (`network.trr.mode` 3, no
fallback; `test_doh_through_the_proxy_e2e.py` is the proof of the road). Two consequences for whoever runs a proxy:

* a SOCKS5 proxy is sent ADDRESSES, never names (the browser resolved them); an HTTP proxy is sent the name of an `http`
  page inside the request (the proxy resolves it), as any browser does;
* a proxy that does not let HTTPS to the DoH through cannot have any name resolved by the browser.

These tests put both on the record, for a SOCKS5 and an HTTP proxy, and for three DoH roads: carried (`works`), refused
(SOCKS5 reply 2 "not allowed", HTTP 403 to the CONNECT), and accepted then mute (the tunnel opens and nothing ever comes
back). Each case runs this browser and Playwright's own Firefox (the `playwright` package at the pinned version, its own
browser) with the same proxy, one after the other, and writes what it saw - the targets the proxy received, the outcome of
`page.goto` (loaded, or the first line of the error) and the seconds it took - to the file named by
`INVPW_PROXY_NODOH_REPORT` (one JSON object per line). The proxies here resolve the names of the test's zone when they are
GIVEN a name (as a real proxy does), so stock Firefox is measured against a proxy that works for it.

What is asserted is only what the contract says, so a change of behaviour shows up in the report and the assertions keep
the fail-closed line: this browser never goes past the proxy and never asks the machine's resolver (Linux, the resolver
trap), and behind a SOCKS5 proxy it never sends the page's name. The outcome and the time of `page.goto` are recorded,
not asserted (they are what this file is here to measure).

Not covered: an `https` page (the page server is plain HTTP), a proxy with credentials, a context proxy, Windows.
"""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time

import pytest

from invisible_core import SessionGeo, decide_session_locale
from invisible_playwright import InvisiblePlaywright

from _doh_servers import DOH_PROVIDER_PREFS, DOH_TARGETS, DohGateway
from _http_proxy import HttpProxy

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

#: As in test_doh_through_the_proxy_e2e.py: loopback through the proxy, and the FakeDoh as the session's provider.
PREFS = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": "", **DOH_PROVIDER_PREFS}
GEO = SessionGeo("America/New_York", "203.0.113.7", latitude=40.7128, longitude=-74.0060,
                 locale=decide_session_locale("en-US"))
#: Long enough for the browser to give up on its own: the error and the time are the measurement.
GOTO_TIMEOUT_MS = 60000
ROADS = ("works", "refused", "mute")


@pytest.fixture(autouse=True)
def _hermetic_session(monkeypatch):
    """No egress lookup on the internet (the same declaration as test_doh_through_the_proxy_e2e.py)."""
    monkeypatch.setattr("invisible_playwright.launcher.prepare_session_geo", lambda tz, proxy, locale, **_kw: GEO)
    monkeypatch.setattr(InvisiblePlaywright, "_assert_uscita_invariata", lambda self: None)
    monkeypatch.setattr("invisible_core._geo.discover_egress_ip", lambda *_a, **_k: GEO.egress_ip)


def _is_address(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class _Socks(DohGateway):
    """The DoH gateway, with a DoH road that works, is refused or is mute, and that resolves the names of the zone it is
    given (stock Firefox sends names to a SOCKS5 proxy)."""

    def __init__(self, name, doh, road):
        self.road = road
        self.names_received = []
        self._stop = threading.Event()
        super().__init__(name, doh)

    def _connect(self, c, host, port):
        if (host, port) in DOH_TARGETS and self.road != "works":
            self.connects.append((host, port))
            if self.road == "refused":
                self.refused.append((host, port))
                self._send(c, self._reply(2))
                return
            self._send(c, self._reply(0))
            self._stop.wait()          # mute: the tunnel is open and nothing ever comes back
            return
        if not _is_address(host) and host.lower() in self.doh.names:
            self.names_received.append((host, port))
            super()._connect(c, self.doh.names[host.lower()], port)
            return
        super()._connect(c, host, port)

    def received(self):
        return [list(t) for t in self.connects]

    def close(self):
        self._stop.set()
        super().close()


class _Http(HttpProxy):
    """The HTTP proxy of the DoH tests, with a DoH road that works, is refused (403) or is mute, and that resolves the names of
    the zone given in a CONNECT (the GET of an `http` page is resolved by the proxy already)."""

    def __init__(self, name, doh, road):
        self.road = road
        self.names_received = []
        self._stop = threading.Event()
        super().__init__(name, doh)

    def _connect(self, conn, host, port, authorization):
        if (host, port) in DOH_TARGETS and self.road != "works":
            if self.road == "refused":
                self._record("CONNECT", host, port, authorization, 403)
                self._answer(conn, 403)
                return
            self._record("CONNECT", host, port, authorization, 200)
            self._answer(conn, 200)
            self._stop.wait()
            return
        if not _is_address(host) and host.lower() in self.doh.names:
            self.names_received.append((host, port))
            super()._connect(conn, self.doh.names[host.lower()], port, authorization)
            return
        super()._connect(conn, host, port, authorization)

    def received(self):
        with self._lock:
            return [[s.method, s.host, s.port, s.status] for s in self.seen]

    def close(self):
        self._stop.set()
        super().close()


def _proxy(kind, doh, road):
    return _Socks("socks1", doh, road) if kind == "socks5" else _Http("http1", doh, road)


def _goto(page, url):
    started = time.monotonic()
    try:
        page.goto(url, wait_until="load", timeout=GOTO_TIMEOUT_MS)
        outcome = "loaded: " + page.evaluate("() => document.body.innerText")[:80]
    except Exception as exc:  # the error IS the measurement
        outcome = "error: " + str(exc).strip().splitlines()[0][:200]
    return outcome, round(time.monotonic() - started, 2)


def _report(row):
    path = os.environ.get("INVPW_PROXY_NODOH_REPORT")
    print("PROXY-NODOH " + json.dumps(row, sort_keys=True))
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


@pytest.mark.parametrize("road", ROADS)
@pytest.mark.parametrize("kind", ["socks5", "http"])
def test_this_browser_behind_a_proxy_that_does_or_does_not_carry_the_doh(firefox_binary, doh, pages, trap, watcher, kind,
                                                                          road):
    proxy = _proxy(kind, doh, road)
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary, proxy=proxy.proxy, extra_prefs=dict(PREFS),
                                 headless=True) as browser:
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            outcome, seconds = _goto(page, pages.url("page-a.test"))
            context.close()
        watcher.stop()
        _report({"browser": "invisible", "proxy": kind, "doh": road, "outcome": outcome, "seconds": seconds,
                 "received": proxy.received(), "names_received": [list(t) for t in proxy.names_received],
                 "doh_queries": len(doh.queries_for("page-a.test"))})
    finally:
        proxy.close()

    why = "\n  proxy received: %s\n  outcome: %s after %s s" % (proxy.received(), outcome, seconds)
    assert watcher.watched_pids, "the watcher never saw a browser process: %s" % watcher.process_names
    assert watcher.direct == [], "the browser connected past the proxy: %s" % watcher.describe() + why
    if trap is not None:
        assert trap.queries == [], "the browser asked the resolver of the machine: %s" % trap.describe() + why
    if kind == "socks5":
        assert proxy.names_received == [], "the SOCKS5 proxy was sent the NAME of the page" + why
    if road == "works":
        assert outcome.startswith("loaded"), "the page did not load through a proxy that carries the DoH" + why


@pytest.mark.parametrize("road", ROADS)
@pytest.mark.parametrize("kind", ["socks5", "http"])
def test_stock_firefox_behind_the_same_proxy(doh, pages, kind, road):
    """The reference: Playwright's own Firefox with the same proxy (no assertion on its outcome: it is the measurement)."""
    from playwright.sync_api import sync_playwright

    proxy = _proxy(kind, doh, road)
    try:
        with sync_playwright() as p:
            if not os.path.exists(p.firefox.executable_path):
                pytest.fail("Playwright's own Firefox is not installed (python -m playwright install firefox): %s"
                            % p.firefox.executable_path)
            browser = p.firefox.launch(headless=True, proxy=proxy.proxy)
            try:
                page = browser.new_page()
                outcome, seconds = _goto(page, pages.url("page-a.test"))
            finally:
                browser.close()
        _report({"browser": "stock", "proxy": kind, "doh": road, "outcome": outcome, "seconds": seconds,
                 "received": proxy.received(), "names_received": [list(t) for t in proxy.names_received],
                 "doh_queries": len(doh.queries_for("page-a.test"))})
    finally:
        proxy.close()
    assert outcome, "nothing was recorded"
