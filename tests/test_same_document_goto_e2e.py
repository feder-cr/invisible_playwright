"""`page.url` right after a `goto` to a fragment of the same page.

Upstream Playwright answers such a `goto` only after the frame's same-document
navigation, so the client already holds the new URL. This server answered on
`Page.navigate`'s reply, before `Page.sameDocumentNavigation` arrived, and
the client read the old URL until its next call delivered the event. Measured
on main 2026-10-04: `goto(url + "#sec")` returned None with `page.url == url`,
and after `goto(url + "#other")` the URL read `url + "#sec"`.

The sync API reads `page.url` without running the event loop, which is what
makes the ordering visible: whatever arrived after the reply is not there yet.
The unit half is in `test_juggler_lifecycle.py`.
"""
from __future__ import annotations

import http.server
import threading

import pytest

PAGE = (b"<!doctype html><html><head><title>t</title></head><body>"
        b"<h1 id=sec>sec</h1><div style='height:3000px'></div>"
        b"<h1 id=other>other</h1></body></html>")


@pytest.mark.e2e
def test_goto_to_a_fragment_answers_with_the_new_url(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    try:
        with InvisiblePlaywright(seed=4246, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            first = page.goto(url, timeout=20_000)
            assert first is not None and first.status == 200
            for fragment in ("#sec", "#other", "#other"):
                response = page.goto(url + fragment, timeout=10_000)
                assert response is None, response
                assert page.url == url + fragment, (fragment, page.url)
    finally:
        srv.shutdown()
