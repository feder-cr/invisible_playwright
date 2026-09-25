"""`goto` on a page that replaces itself from script while it loads.

The known-bad input of [B228], measured on 2026-09-25: YouTube
(`?themeRefresh=1`) and Reddit (a JavaScript challenge) start a new navigation
after ours has committed. Our navigation never reaches `load`, because its
document is gone, and `goto` used to wait for it until the timeout: 45 s on a
page that was ready, while stock Playwright answered in 2 to 4 s on the same
Firefox 151. Every `browser_navigate` of the MCP server to such a site was a
45-second failure.

This is the same shape without depending on anybody's site: a local page whose
`<head>` calls `location.replace` before `load`. The unit half, the ordering
of navigations, is in `test_juggler_lifecycle.py`.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

START = (b"<!doctype html><html><head><title>start</title>"
         b"<script>location.replace('/landed')</script></head>"
         b"<body>start</body></html>")
LANDED = b"<!doctype html><html><head><title>landed</title></head><body>landed</body></html>"


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = LANDED if self.path.startswith("/landed") else START
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    # Threading for the reason test_navigation_response.py gives: Firefox's
    # speculative connections starve a single-threaded server.
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port


@pytest.mark.e2e
def test_goto_returns_on_the_page_that_replaced_itself_from_script(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            started = time.monotonic()
            response = page.goto(url, wait_until="load", timeout=20_000)
            took = time.monotonic() - started
            assert took < 10, (
                "goto took %.1fs on a page that redirects itself before load: "
                "it waited for the navigation the script replaced ([B228])" % took)
            assert page.url == url + "landed", page.url
            assert page.title() == "landed", page.title()
            assert response is not None and response.status == 200, response

            # and an ordinary goto after it still waits for ITS OWN document
            page.goto(url + "landed?again", wait_until="load", timeout=20_000)
            assert page.url.endswith("landed?again"), page.url
    finally:
        srv.shutdown()
