"""`Heap.minimizeMemory` and INVPW_GC_MODE=minimize on a real engine.

The engine that has the command is not published when this file is written: `engine_at_least` makes the
tests wait for it, and on an older engine they are skipped, never passed. (On an older engine the option
falls back to `Heap.collectGarbage` with one warning: tests/test_gc_every_navigation.py.)

What is checked, with a browser served by `launch_server` and a client connected to it:

1. The option sends the command and the engine takes it: after a few navigations `nav_gc` counts the
   rounds it started, none failed and none fell back to `gc`.
2. It does what a real Firefox short of memory does to the bfcache, and this file DOCUMENTS it: a page the tab
   left is restored from the bfcache (`pageshow.persisted == true`) on `go_back()`, and after
   `Heap.minimizeMemory` it is not (`persisted == false`: the document is loaded again). The control is the
   same sequence with nothing sent. No page can read the command; it can read this, and only by going back
   in the history of the tab, which a fetcher that loads a page and moves on never does.
"""
from __future__ import annotations

import threading

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

ENGINE_WITH_THE_COMMAND = "firefox-61"

PAGE = (b"<html><head><title>%s</title></head><body>%s"
        b"<script>window.shows = [];"
        b"addEventListener('pageshow', e => window.shows.push(e.persisted));</script></body></html>")


def _serve():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.strip("/").encode() or b"index"
            body = PAGE % (name, b"page " + name)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _launch(firefox_binary):
    from invisible_playwright import launch_server

    return launch_server(host="127.0.0.1", port=0, headless=True, virtual_display=False, humanize=False,
                         seed=4261, binary_path=firefox_binary, launch_timeout_ms=180_000)


def test_the_option_sends_minimize_memory_and_the_engine_takes_it(firefox_binary, engine_at_least, monkeypatch):
    engine_at_least(ENGINE_WITH_THE_COMMAND)
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("INVPW_GC_EVERY_NAV", "2")
    monkeypatch.setenv("INVPW_GC_MODE", "minimize")
    server, base = _serve()
    launched = _launch(firefox_binary)
    try:
        nav_gc = launched._endpoint.nav_gc
        assert nav_gc is not None and nav_gc.mode == "minimize"
        with sync_playwright() as p:
            browser = p.firefox.connect(launched.ws_endpoint)
            page = browser.new_context().new_page()
            for i in range(6):
                page.goto("%s/p%d" % (base, i), wait_until="load", timeout=30_000)
                nav_gc.join(60)
            browser.close()
        assert nav_gc.commits >= 6 and nav_gc.started >= 3
        assert nav_gc.failed == 0, "the engine refused a round"
        assert (nav_gc.effective_mode, nav_gc.fell_back) == ("minimize", 0), "the engine has no such command"
    finally:
        launched.close()
        server.shutdown()


def _restored_from_bfcache(firefox_binary, minimize, monkeypatch):
    """Load A, leave for B, optionally minimize, go back: the `persisted` of the last pageshow of A."""
    from playwright.sync_api import sync_playwright

    monkeypatch.delenv("INVPW_GC_EVERY_NAV", raising=False)
    server, base = _serve()
    launched = _launch(firefox_binary)
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(launched.ws_endpoint)
            page = browser.new_context().new_page()
            page.goto(base + "/a", wait_until="load", timeout=30_000)
            page.goto(base + "/b", wait_until="load", timeout=30_000)
            page.wait_for_timeout(500)
            if minimize:
                dispatcher = launched._endpoint.browser.contexts[-1].pages[-1]
                dispatcher.send("Heap.minimizeMemory", {}, timeout=120)
                page.wait_for_timeout(500)
            page.go_back(wait_until="load", timeout=30_000)
            page.wait_for_timeout(500)
            assert page.title() == "a"
            persisted = page.evaluate("window.shows[window.shows.length - 1]")
            browser.close()
        return persisted
    finally:
        launched.close()
        server.shutdown()


def test_a_page_the_tab_left_is_restored_from_the_bfcache_when_nothing_is_sent(firefox_binary, engine_at_least,
                                                                              monkeypatch):
    engine_at_least(ENGINE_WITH_THE_COMMAND)
    assert _restored_from_bfcache(firefox_binary, False, monkeypatch) is True


def test_minimize_memory_evicts_the_bfcache_as_a_real_firefox_under_memory_pressure_does(firefox_binary,
                                                                                         engine_at_least,
                                                                                         monkeypatch):
    engine_at_least(ENGINE_WITH_THE_COMMAND)
    assert _restored_from_bfcache(firefox_binary, True, monkeypatch) is False
