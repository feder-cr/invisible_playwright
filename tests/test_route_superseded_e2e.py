"""A route whose request the page has already abandoned is answered quietly.

The known-bad input, measured on 2026-10-14 with
`page.route("**/*", lambda r: r.continue_())` and sixty sequential
`page.goto(url, wait_until="load")` calls on a loopback page: 4-12 of them
failed. Every failing `continue_` was the previous document's `/favicon.ico`,
whose channel the next navigation had aborted while the route sat with the
handler. The engine answered `Network.resumeInterceptedRequest` with
NS_BINDING_ABORTED, the server raised it, and it came out of the caller's NEXT
call (`Page.goto: Route.continue_: ...`, `Page.evaluate: Route.continue_: ...`)
or left that goto waiting for a load that never came.

Playwright's own Firefox server sends all three interception answers with
`sendMayFail`: a request that is gone has nothing left to answer.
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

PAGE = b"<!doctype html><html><body><form><textarea></textarea></form></body></html>"
HELD = b"<!doctype html><html><body><img src='/held.png'></body></html>"


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = HELD if self.path.startswith("/held") and \
                not self.path.endswith(".png") else PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % port


@pytest.mark.e2e
@pytest.mark.parametrize("answer", ["continue_", "fulfill", "abort"])
def test_answering_a_route_the_next_navigation_aborted_does_not_raise(
        firefox_binary, answer):
    """The image of a page the caller has navigated away from is still held
    by the handler; answering it afterwards neither raises nor poisons the
    caller's next call."""
    from invisible_playwright import InvisiblePlaywright

    srv, base = _serve()
    held = []

    def handler(route):
        if route.request.url.endswith("/held.png"):
            held.append(route)
        else:
            route.continue_()

    try:
        with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                                 headless=True) as b:
            page = b.new_context().new_page()
            page.route("**/*", handler)
            page.goto(base + "/held", wait_until="commit", timeout=15000)
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(50)
            assert held, "the page's image never reached the handler"

            page.goto(base + "/next", wait_until="load", timeout=15000)

            route = held[0]
            if answer == "fulfill":
                route.fulfill(status=200, body="")
            else:
                getattr(route, answer)()
            assert page.evaluate("1 + 1") == 2
            assert page.url == base + "/next"
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_sequential_routed_gotos_neither_hang_nor_raise(firefox_binary):
    """The measured case: every routed goto's favicon is still being
    continued when the next goto aborts it."""
    from invisible_playwright import InvisiblePlaywright

    srv, base = _serve()
    failures = []
    try:
        with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                                 headless=True) as b:
            page = b.new_context().new_page()
            page.route("**/*", lambda r: r.continue_())
            for i in range(40):
                try:
                    page.goto(f"{base}/p?{i % 2}", wait_until="load",
                              timeout=8000)
                    page.evaluate("1")
                except Exception as e:  # noqa: BLE001 - the failure is the finding
                    failures.append((i, type(e).__name__, str(e)[:200]))
    finally:
        srv.shutdown()
    assert not failures, failures
