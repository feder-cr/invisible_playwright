"""A popup is reported on Page.ready, as Playwright's own is: with no limit, and with the destination's address already.

Measured against stock Playwright 1.62 and its Firefox (in-process, 2 runs of each, never in disagreement):
  * a destination that answers 204, or never answers: NO `page` event in 30 s (nothing commits, the engine never says Page.ready);
  * a destination that answers after 4 s: the event at 4.08 s, with `url` already the destination and `load` in the same millisecond.
Here: the same two, in-process and through a served browser driven by a stock client, and a destination that answers after 25 s (the
wrapper dropped a popup that was not ready in 20 s, with a warning); the address read at the `page` event is the destination.

Red on b09d620: the popup of the slow destination reads `about:blank` at the event (the commit comes in the same turn as the ready and
was applied after the page was announced), and the one that answers after 25 s was dropped at 20 s.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/nocontent":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/slow":
            ms = int(query.split("ms=")[1].split("&")[0]) if "ms=" in query else 0
            time.sleep(ms / 1000.0)
            body = b"<!doctype html><html><head><title>slow</title></head><body>slow</body></html>"
        else:
            body = (b"<!doctype html><html><head><title>opener</title></head><body>"
                    b"<button id=open204 onclick=\"window.open('/nocontent','_blank')\">open</button>"
                    b"<a id=link204 href=/nocontent target=_blank>link</a>"
                    b"<button id=open1500 onclick=\"window.open('/slow?ms=1500','_blank')\">open</button>"
                    b"<a id=link1500 href='/slow?ms=1500' target=_blank>link</a>"
                    b"<button id=open25000 onclick=\"window.open('/slow?ms=25000','_blank')\">open</button>"
                    b"</body></html>")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OSError:       # the browser left before a slow answer
            pass


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
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:       # pragma: no cover - only in a broken environment
        pytest.fail("the stock Playwright client is missing (%s): install the wrapper with its dev extra" % exc, pytrace=False)


def _drive(mode, firefox_binary, body):
    """Run body(context, page) in-process or through a served browser driven by a stock client."""
    if mode == "in-process":
        from invisible_playwright import InvisiblePlaywright

        with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True, humanize=False) as b:
            ctx = b.new_context()
            body(ctx, ctx.new_page())
            ctx.close()
        return
    from invisible_playwright._juggler.remote import launch_server
    sync_playwright = _stock_client("playwright.sync_api").sync_playwright

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            ctx = browser.new_context()
            body(ctx, ctx.new_page())
            ctx.close()
            browser.close()
    finally:
        server.close()


MODES = ["in-process", "served"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("opener", ["#open204", "#link204"])
def test_a_destination_that_commits_nothing_gives_no_popup_event(firefox_binary, site, mode, opener):
    def body(ctx, page):
        page.goto(site, wait_until="load", timeout=30000)
        seen = []
        ctx.on("page", lambda p: seen.append(p))
        page.click(opener)
        time.sleep(6)
        assert not seen, "a popup whose destination commits nothing was reported (Playwright's is not)"
        assert len(ctx.pages) == 1

    _drive(mode, firefox_binary, body)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("opener", ["#open1500", "#link1500"])
def test_a_slow_popup_is_reported_with_the_address_of_its_destination(firefox_binary, site, mode, opener):
    def body(ctx, page):
        page.goto(site, wait_until="load", timeout=30000)
        with page.expect_popup(timeout=20000) as info:
            page.click(opener)
        assert info.value.url == site + "slow?ms=1500", "the popup was reported with %r" % info.value.url
        info.value.close()

    _drive(mode, firefox_binary, body)


@pytest.mark.parametrize("mode", MODES)
def test_a_popup_whose_destination_answers_after_25_seconds_is_reported_then(firefox_binary, site, mode):
    def body(ctx, page):
        page.goto(site, wait_until="load", timeout=30000)
        t0 = time.monotonic()
        with page.expect_popup(timeout=45000) as info:
            page.click("#open25000")
        took = time.monotonic() - t0
        assert 24.0 < took < 40.0, "reported after %.1f s" % took
        assert info.value.url == site + "slow?ms=25000"
        info.value.close()

    _drive(mode, firefox_binary, body)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("opener", ["#open1500", "#link1500"])
def test_a_listener_registered_in_the_page_handler_hears_the_load_of_the_popup(firefox_binary, site, mode, opener):
    """Stock Playwright: `context.on("page", lambda p: p.on("load", ...))` fires for a popup that loads after the event (4 s and 25 s
    destinations); the popup is reported when its ready comes, which is when its document commits, so the load may be said before the
    client has the page. `wait_for_load_state` in the same handler returns at once."""
    def body(ctx, page):
        page.goto(site, wait_until="load", timeout=30000)
        heard, waited = [], []

        def on_page(popup):
            popup.on("load", lambda p: heard.append(time.monotonic()))
            t = time.monotonic()
            popup.wait_for_load_state("load", timeout=10000)
            waited.append(time.monotonic() - t)

        ctx.on("page", on_page)
        with page.expect_popup(timeout=20000) as info:
            page.click(opener)
        deadline = time.monotonic() + 5
        while not heard and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert heard, "the listener registered in the handler of the page event never heard the load of the popup"
        assert waited and waited[0] < 2.0, "wait_for_load_state in the handler took %r" % (waited,)
        info.value.close()

    _drive(mode, firefox_binary, body)
