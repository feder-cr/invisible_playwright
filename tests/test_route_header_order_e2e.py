"""A route does not change what goes on the wire: same headers, same order, same
spelling, as the same request with no route.

Known-bad, measured on firefox-35 (B254): 8 requests out of 8 left with another
header order under `route.continue_()` - Connection and Cookie after the
Sec-Fetch-* group, Referer after Content-Length on a POST - because resuming an
intercepted request rebuilds the channel twice and Gecko re-adds part of the
headers at the end. A server sees that without running any JavaScript.

The server below records the raw request lines, so the test reads exactly what a
server reads. Loopback only.
"""
from __future__ import annotations

import socket
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

pytestmark = pytest.mark.e2e

PAGE = (b"<!doctype html><title>h</title><script>"
        b"window.done = fetch('/api', {method: 'POST', body: 'x=1', headers: {"
        b"'Content-Type': 'application/x-www-form-urlencoded', 'X-Empty': '', "
        b"'X-Custom': 'v'}}).then(r => r.text());"
        b"</script>")


class _RawServer:
    """HTTP/1.1 on loopback that keeps every request's header block as sent."""

    def __init__(self):
        self.seen = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        with conn:
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                head, _, buf = buf.partition(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                headers = [tuple(line.split(":", 1)) for line in lines[1:]]
                length = next((int(v) for n, v in headers if n.lower() == "content-length"), 0)
                while len(buf) < length:
                    buf += conn.recv(65536)
                buf = buf[length:]
                path = lines[0].split(" ")[1]
                self.seen.append((path, [(n, v.strip()) for n, v in headers]))
                body = PAGE if path == "/" else b"ok"
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                             b"Content-Length: %d\r\n\r\n%s" % (len(body), body))

    def close(self):
        self.sock.close()


def _load(firefox_binary, route):
    """The header blocks the server received for `/` and `/api`, in one fresh
    browser, with `route` installed on the context first (or none)."""
    server = _RawServer()
    try:
        with InvisiblePlaywright(seed=4247, binary_path=firefox_binary, headless=True) as browser:
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "c", "value": "1", "domain": "127.0.0.1", "path": "/"}])
            if route:
                ctx.route("**/*", route)
            page = ctx.new_page()
            page.goto("http://127.0.0.1:%d/" % server.port)
            page.evaluate("window.done")
        return {path: headers for path, headers in server.seen if path in ("/", "/api")}
    finally:
        server.close()


@pytest.fixture(scope="module")
def without_route(firefox_binary):
    return _load(firefox_binary, None)


ROUTES = {
    "continue": lambda route: route.continue_(),
    "continue with its own headers": lambda route: route.continue_(headers=route.request.headers),
}


@pytest.mark.parametrize("name", list(ROUTES))
def test_a_route_sends_the_headers_a_request_without_one_sends(firefox_binary, without_route, name):
    with_route = _load(firefox_binary, ROUTES[name])
    assert set(with_route) == {"/", "/api"}, with_route
    for path in ("/", "/api"):
        plain = [n for n, _ in without_route[path]]
        routed = [n for n, _ in with_route[path]]
        assert routed == plain, "%s %s: order or spelling changed under the route\n  without: %s\n  with:    %s" % (
            name, path, plain, routed)


def test_an_empty_header_survives_a_route(firefox_binary, without_route):
    """Known-bad: the order repair removed each header and set it again with
    setRequestHeader, which drops an empty value, so `X-Empty:` went out without
    a route and vanished with one."""
    assert ("X-Empty", "") in without_route["/api"], without_route["/api"]
    with_route = _load(firefox_binary, ROUTES["continue"])
    assert ("X-Empty", "") in with_route["/api"], with_route["/api"]
