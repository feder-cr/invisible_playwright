"""A tab opened by a link (`target=_blank`, with or without `noopener`) or by `window.open` reaches the client within 2 s, in-process and
through a served browser driven by a stock Playwright client, with and without a context route holding the requests.

⛔ THE KNOWN-BAD INPUT. The page is handed to the client when the engine says `Page.ready`; the engine says it at the commit of the
popup's first document. Waiting for it INSIDE the construction of the page (7db28e4..6a60dce) broke two ways, each found by a run:
  * the first request of a popup is held by the context's route until the page answers it, and a page that is built only once it is
    ready never answers: `adoption of target ... failed: the page was not ready in 20s` on the server, `Timeout 15000ms exceeded while
    waiting for event "page"` on the client (a stock client, a served browser, a route that continues every request);
  * in-process, the commit and the `load` that come with the ready were said to a page that did not exist: `popup.wait_for_url(url)`
    timed out on `load` (5 tests of test_popup_pages.py).
The page is now BUILT at once and ANNOUNCED when ready.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

POPUP = b"<!doctype html><html><head><title>popup</title></head><body><h1>popup</h1></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/popup":
            body = POPUP
        else:
            body = (b"<!doctype html><html><head><title>opener</title></head><body>"
                    b"<a id=link href=/popup target=_blank>link</a>"
                    b"<a id=noopener href=/popup target=_blank rel=noopener>noopener</a>"
                    b"<button id=open onclick=\"window.open('/popup','_blank')\">open</button>"
                    b"<button id=open-noopener onclick=\"window.open('/popup','_blank','noopener')\">open</button>"
                    b"</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site():
    srv = LocalServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d/" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _stock_client(module):
    """The stock Playwright client, a pinned dev dependency: its absence is a broken environment, not a reason to skip."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:       # pragma: no cover - only in a broken environment
        pytest.fail("the stock Playwright client is missing (%s): install the wrapper with its dev extra" % exc, pytrace=False)


OPENERS = ["#link", "#noopener", "#open", "#open-noopener"]


def _router(delay_s):
    def route(route):
        time.sleep(delay_s)      # a client that decides: the popup's first request waits for it
        route.continue_()
    return route


def _check(page, popup_of, site, opener):
    page.goto(site, wait_until="load", timeout=30000)
    t0 = time.monotonic()
    with page.expect_popup(timeout=2000) as info:
        page.click(opener)
    popup = info.value
    assert time.monotonic() - t0 < 2.0
    popup.wait_for_load_state("load", timeout=5000)
    assert popup.url == site + "popup"
    assert popup.title() == "popup"
    assert popup in page.context.pages
    popup.close()


@pytest.mark.parametrize("routed", [False, True], ids=["plain", "routed"])
@pytest.mark.parametrize("opener", OPENERS)
def test_a_popup_reaches_an_in_process_client(firefox_binary, site, opener, routed):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True, humanize=False) as b:
        ctx = b.new_context()
        if routed:
            ctx.route("**/*", _router(0.05))
        page = ctx.new_page()
        _check(page, None, site, opener)
        ctx.close()


@pytest.mark.parametrize("routed", [False, True], ids=["plain", "routed"])
@pytest.mark.parametrize("opener", OPENERS)
def test_a_popup_reaches_a_stock_client_of_a_served_browser(firefox_binary, site, opener, routed):
    from invisible_playwright._juggler.remote import launch_server
    sync_playwright = _stock_client("playwright.sync_api").sync_playwright   # a stock client, as production uses

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            ctx = browser.new_context()
            if routed:
                ctx.route("**/*", _router(0.05))
            page = ctx.new_page()
            _check(page, None, site, opener)
            ctx.close()
            browser.close()
    finally:
        server.close()
