"""A routed request carries its body, on the route and on the request event.

The known-bad input, measured on 2026-10-03: `request.post_data` was None for
every POST, including a page `fetch()` with a JSON body, both inside a context
route handler and on the `request` event fired for the same request. The
engine's `NetworkObserver.js` sent `postData: undefined` for every request
(feder-cr/invisible_core#90), so a route guard could check a POST's URL and
never its body. The engine now reads the body while interception is on for
the page or its context.
"""
from __future__ import annotations

import http.server
import json
import socket
import threading

import pytest

BODY = {"amount": "12.34", "note": "café"}
PAGE = (b"<!doctype html><html><body><script>"
        b"fetch('/api', {method: 'POST', headers: {'Content-Type': "
        b"'application/json'}, body: JSON.stringify("
        + json.dumps(BODY).encode() +
        b")}).then(r => document.title = 'post:' + r.status)"
        b".catch(() => document.title = 'post:failed');"
        b"</script></body></html>")


def _serve():
    received = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            received.append(
                self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port, received


@pytest.mark.e2e
@pytest.mark.parametrize("where", ["context", "page"])
def test_a_routed_post_carries_its_body(firefox_binary, where):
    from invisible_playwright import InvisiblePlaywright

    srv, url, received = _serve()
    on_route, on_event = [], []

    def guard(route):
        request = route.request
        if request.method == "POST":
            on_route.append((request.post_data, request.post_data_json,
                             request.post_data_buffer))
        route.continue_()

    def on_request(request):
        if request.method == "POST":
            on_event.append(request.post_data)

    try:
        with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                                 headless=True) as browser:
            context = browser.new_context()
            page = context.new_page()
            page.on("request", on_request)
            (context if where == "context" else page).route("**/*", guard)
            page.goto(url, wait_until="load", timeout=20_000)
            page.wait_for_function("document.title.startsWith('post:')",
                                   timeout=10_000)
            assert page.title() == "post:204", page.title()
    finally:
        srv.shutdown()

    # what the page's JSON.stringify sends: compact, and UTF-8 on the wire
    sent = json.dumps(BODY, separators=(",", ":"), ensure_ascii=False)
    assert on_route == [(sent, BODY, sent.encode())], on_route
    assert on_event == [sent], on_event
    # reading the body did not consume it: the server got every byte
    assert received == [sent.encode()], received


@pytest.mark.e2e
def test_without_a_route_the_engine_does_not_copy_the_body(firefox_binary):
    """The other half of the contract: the engine reads a POST body only
    while interception is on, so a session with no route keeps the saving the
    2026-08-24 engine change was made for, and the body still reaches the
    server whole."""
    from invisible_playwright import InvisiblePlaywright

    srv, url, received = _serve()
    on_event = []
    try:
        with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            page.on("request", lambda r: on_event.append(r.post_data)
                    if r.method == "POST" else None)
            page.goto(url, wait_until="load", timeout=20_000)
            page.wait_for_function("document.title.startsWith('post:')",
                                   timeout=10_000)
            assert page.title() == "post:204", page.title()
    finally:
        srv.shutdown()
    sent = json.dumps(BODY, separators=(",", ":"), ensure_ascii=False)
    assert on_event == [None], on_event
    assert received == [sent.encode()], received
