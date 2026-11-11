"""A CORS preflight is not a request the client hears of, as on stock Firefox.

A page at one origin fetches another origin with a header that needs a preflight: the browser sends an OPTIONS request first. Playwright's
own server drops an OPTIONS request that no route holds before the client can see it (ffNetworkManager `_onRequestWillBeSent`);
ours reported it, so a page with preflighted fetches showed more requests than on stock (measured on cnbc.com and scmp.com: 10 and
18 OPTIONS requests a page, none on stock). The test counts what the client hears: the document and the two fetches, no OPTIONS, with
and without a context route (a route that holds the request is another story and keeps it).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e


class _Api(BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "x-test")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def do_OPTIONS(self):
        _Api.hits.append(("OPTIONS", self.path))
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        _Api.hits.append(("GET", self.path))
        body = b'{"ok":1}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


def _page(api_origin):
    return ("<!doctype html><title>preflight</title><script>"
            "fetch('%s/one', {headers: {'x-test': '1'}}); fetch('%s/two', {headers: {'x-test': '2'}});"
            "</script>" % (api_origin, api_origin)).encode()


@pytest.fixture
def two_origins():
    _Api.hits = []

    class Page(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = _page(self.server.api_origin)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    api = LocalServer(("127.0.0.1", 0), _Api)
    page = LocalServer(("127.0.0.1", 0), Page)
    page.api_origin = "http://localhost:%d" % api.server_address[1]
    for srv in (api, page):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d/" % page.server_address[1]
    finally:
        for srv in (api, page):
            srv.shutdown()
            srv.server_close()


@pytest.mark.parametrize("route", [False, True])
def test_the_client_hears_the_document_and_the_fetches_and_no_preflight(firefox_binary, two_origins, route):
    from invisible_playwright import InvisiblePlaywright

    heard = []
    with InvisiblePlaywright(seed=4261, binary_path=firefox_binary, headless=True) as browser:
        context = browser.new_context()
        if route:
            context.route("**/*", lambda r: r.continue_())
        page = context.new_page()
        page.on("request", lambda r: heard.append((r.method, r.url.rsplit("/", 1)[-1] or "page")))
        page.goto(two_origins, wait_until="load")
        page.wait_for_timeout(2000)         # the events of a sync client are delivered while it calls the browser
    assert ("OPTIONS", "/one") in _Api.hits or ("OPTIONS", "/two") in _Api.hits, "the browser made no preflight: %r" % _Api.hits
    methods = sorted(m for m, _ in heard)
    assert methods == ["GET", "GET", "GET"], "the client heard %r (the server saw %r)" % (heard, _Api.hits)
