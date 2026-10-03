"""`networkidle` through the public API: the waits that run in the CLIENT.

`page.goto(..., wait_until="networkidle")` is answered by the server, but
`page.wait_for_load_state("networkidle")`, `wait_for_url(...,
wait_until="networkidle")` and `expect_navigation(wait_until="networkidle")`
are not: the vendored client waits for a `loadstate {"add": "networkidle"}`
event on the frame, and the server never sent one. All three timed out on a
loaded, silent page. Measured on main 2026-10-04: `wait_for_load_state("load")`
in 0.0 s, `wait_for_load_state("networkidle")` TimeoutError at 5 s.

The unit halves are in `test_juggler_lifecycle.py` (the state is born once,
without a waiter) and `test_frame_dispatch.py` (it goes up as a load state).
"""
from __future__ import annotations

import http.server
import threading

import pytest

PAGE = (b"<!doctype html><html><head><title>%s</title></head><body>"
        b"<a id=next href='/second'>next</a></body></html>")


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE % self.path.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


@pytest.mark.e2e
def test_the_client_side_networkidle_waits_resolve(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, base = _serve()
    try:
        with InvisiblePlaywright(seed=4245, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto(base + "/first", wait_until="load", timeout=20_000)
            page.wait_for_load_state("networkidle", timeout=10_000)

            page.goto(base + "/plain", wait_until="load", timeout=20_000)
            page.wait_for_url(base + "/plain", wait_until="networkidle",
                              timeout=10_000)

            page.goto(base + "/first", wait_until="networkidle", timeout=20_000)
            with page.expect_navigation(wait_until="networkidle",
                                        timeout=10_000):
                page.click("#next")
            assert page.url == base + "/second", page.url
    finally:
        srv.shutdown()
