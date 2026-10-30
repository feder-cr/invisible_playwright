"""An offline context loads nothing and says so, and set_offline(False) brings it back.

**Why this file exists.** set_offline() was refused by this server, and
new_context(offline=True) sent Juggler's Browser.setOnlineOverride to an engine
that ignored it: the engine's HTTP channel asked a stub that always answered
"online" instead of the browsing context's ForceOffline, so a page saw
navigator.onLine === false and still loaded every request. Found by the 157
inventory (2026-10-27); the engine now runs Mozilla's own code there.

Known-bad inputs, each run against this file before it was trusted:

* an engine with the stub (firefox_antidetect_patch before 3087f02c56) -> the
  offline goto loads the page;
* op_set_offline that still raises -> every test here goes red.
"""
from __future__ import annotations

import http.server
import threading

import pytest


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>up</title>")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


@pytest.mark.e2e
def test_set_offline_stops_loads_and_set_online_resumes_them(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, timeout=30_000)
            context.set_offline(True)
            assert page.evaluate("navigator.onLine") is False
            # A failed goto is followed by Firefox's error page, a navigation of
            # its own: waited for here, as Playwright's own offline test does
            # on Firefox (test_offline_should_work_with_initial_option).
            # Without the wait the next goto races it and reads as interrupted
            # by a navigation to the failed URL.
            with page.expect_event("framenavigated", timeout=15_000):
                with pytest.raises(Exception) as failure:
                    page.goto(url + "again", timeout=15_000)
            assert "OFFLINE" in str(failure.value).upper(), str(failure.value)
            context.set_offline(False)
            assert page.goto(url + "back", timeout=30_000).status == 200
            assert page.evaluate("navigator.onLine") is True
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_a_context_created_offline_loads_nothing(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context(offline=True).new_page()
            with pytest.raises(Exception):
                page.goto(url, timeout=15_000)
    finally:
        srv.shutdown()
