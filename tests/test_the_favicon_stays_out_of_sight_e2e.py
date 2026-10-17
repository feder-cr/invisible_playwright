"""The browser's favicon request reaches the site, and nothing of it reaches the
client (B321).

Measured 2026-10-16 on 0.36.0: a `page.route("**/*")` handler saw
`GET /favicon.ico` in 3 runs out of 6, which is what made Playwright's own
`test_should_amend_post_data` red now and then. Playwright never shows a client
the favicon: no `request`, `response`, `requestfinished` or `requestfailed`, no
route handler, and it is not one of the requests `networkidle` waits on. It
also aborts it when a route is on; a real Firefox always asks the site for its
favicon, so here it is let through instead (`invisible_core.juggler.favicon`).

The site HOLDS the favicon's answer until the test has its verdict, so the
proof does not lean on timing: were the favicon a request in flight,
`networkidle` could not arrive while it is held.
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

PAGE = b"<!doctype html><html><head><title>f</title></head><body>f</body></html>"


def _site():
    seen, arrived, release = [], threading.Event(), threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path == "/favicon.ico":
                arrived.set()
                release.wait(60)
                body, ctype = b"\x00\x00\x01\x00", "image/x-icon"
            else:
                body, ctype = PAGE, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen, arrived, release


@pytest.mark.parametrize("routed", [False, True], ids=["no-route", "route-on-everything"])
def test_the_favicon_goes_to_the_site_and_nothing_of_it_to_the_client(firefox_binary, routed):
    from invisible_playwright import InvisiblePlaywright

    srv, seen, arrived, release = _site()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    told, routes = [], []
    try:
        with InvisiblePlaywright(binary_path=firefox_binary, headless=True) as browser:
            page = browser.new_page()
            for event in ("request", "response", "requestfinished", "requestfailed"):
                page.on(event, lambda r, event=event: told.append((event, r.url)))
            if routed:
                page.route("**/*", lambda route: (routes.append(route.request.url),
                                                  route.continue_()))
            page.goto(url)
            # The favicon is asked for after the document; it is the precondition.
            assert arrived.wait(30), ("the browser never asked for the favicon", seen)
            # Held by the site, it is still not a request in flight.
            page.wait_for_load_state("networkidle", timeout=30000)
            # ⛔ And the session still answers. Letting the held favicon through
            # with a `send` from the reader thread blocked that thread for good,
            # and nothing above noticed: networkidle reaches the client from
            # the lifecycle's timer, not from the reader. A round trip to the
            # engine does need the reader.
            page.wait_for_function("() => document.title === 'f'", timeout=10000)
            release.set()
            page.wait_for_timeout(500)
    finally:
        release.set()
        srv.shutdown()
    assert "/favicon.ico" in seen, seen
    assert not [t for t in told if t[1].endswith("/favicon.ico")], told
    assert not [u for u in routes if u.endswith("/favicon.ico")], routes
    # And the page's own document is still reported, and routed when asked.
    assert ("request", url) in told, told
    if routed:
        assert url in routes, routes
