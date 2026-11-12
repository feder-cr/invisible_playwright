"""A `fetch(..., {keepalive: true})` is a request of the page: the client hears `request` and its end.

Stock Playwright's Firefox reports the keepalive fetch of a page like any other request: `page.on("request")` fires, and
`requestfinished` follows. Here the server saw the request and the client heard nothing, so a client that follows the page's
network by those events (tf-fetch's network recorder does) never knew the request existed.

The page asks for the same address four ways. `plain` is the control (a fetch without keepalive), `keepalive` and
`beacon` are the two that the engine runs in the parent process, and each must give exactly one `request` and one end.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

PAGE = b"""<!doctype html><title>keepalive</title><script>
addEventListener('load', () => {
  fetch('/seen/plain', {method: 'POST', body: 'x'});
  fetch('/seen/keepalive', {method: 'POST', body: 'x', keepalive: true});
  fetch('/seen/keepalive-nocors', {keepalive: true, mode: 'no-cors'});
  navigator.sendBeacon('/seen/beacon', 'x');
});
</script>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _answer(self, body, ctype="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._answer(PAGE, "text/html; charset=utf-8")
        else:
            self._answer(b"ok")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._answer(b"ok")


@pytest.fixture
def site():
    srv = LocalServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("route", [False, True])
def test_every_request_of_the_page_reaches_the_client_with_its_end(firefox_binary, site, route):
    from invisible_playwright import InvisiblePlaywright

    started, ended = [], []
    with InvisiblePlaywright(seed=4261, binary_path=firefox_binary, headless=True) as browser:
        context = browser.new_context()
        if route:
            context.route("**/*", lambda r: r.continue_())
        page = context.new_page()
        page.on("request", lambda r: started.append(r.url.replace(site, "")))
        page.on("requestfinished", lambda r: ended.append(r.url.replace(site, "")))
        page.on("requestfailed", lambda r: ended.append(r.url.replace(site, "")))
        page.goto(site + "/", wait_until="load")
        page.wait_for_timeout(2000)         # the client is a sync one: events are delivered while it calls the browser
    seen = sorted(u for u in started if u.startswith("/seen/"))
    assert seen == ["/seen/beacon", "/seen/keepalive", "/seen/keepalive-nocors", "/seen/plain"], \
        "the client heard these requests: %r" % started
    assert sorted(u for u in ended if u.startswith("/seen/")) == seen, \
        "requests without an end for the client: %r" % sorted(set(seen) - set(ended))
