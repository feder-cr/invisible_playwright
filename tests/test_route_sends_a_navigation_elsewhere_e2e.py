"""A route that sends a navigation to another URL lands there (B326).

Measured 2026-10-17 with Playwright's own `test_should_override_request_url`,
10 runs out of 10, page and context: `route.fallback(url=...)` on a document
request made `goto` raise "reported committed on .../target, which is not
where it went (.../asked)", and the response kept the URL the request was sent
with. The engine applies a route's URL as an internal redirect and sends no
event of it; the route tells the request (`RequestDispatcher.continued_at`),
which tells the lifecycle where the navigation went.
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e


def _site():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            body = (b"<!doctype html><title>target</title><script>window.landed = 'target'</script>"
                    if self.path == "/target" else b"<!doctype html><title>asked</title>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


@pytest.mark.parametrize("how", ["fallback", "continue"])
def test_a_navigation_a_route_sends_elsewhere_lands_there(firefox_binary, how):
    from invisible_playwright import InvisiblePlaywright

    srv, seen = _site()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        with InvisiblePlaywright(binary_path=firefox_binary, headless=True) as browser:
            page = browser.new_page()
            if how == "fallback":
                page.route("**/asked", lambda route: route.fallback(url=base + "/target"))
            else:
                page.route("**/asked", lambda route: route.continue_(url=base + "/target"))
            response = page.goto(base + "/asked")
            assert response is not None
            assert response.url == base + "/target", response.url
            assert page.evaluate("() => window.landed") == "target"
    finally:
        srv.shutdown()
    assert "/target" in seen and "/asked" not in seen, seen
