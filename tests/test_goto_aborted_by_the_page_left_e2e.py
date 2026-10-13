"""A goto that the page being left aborts raises at once, as in Playwright
([B302]).

document.open() stops the ongoing navigation of its browsing context (HTML
spec). Until firefox-46 the engine dropped every abort of the main frame's
navigation, since a Fission process swap fires a spurious one, so the goto
stayed pending; the document.write() that follows a document.open() inserts a
root element into the OLD document, and the engine took that for the goto's
commit. Measured on firefox-45 with goto and an immediate read, back to back,
on a page that rewrites itself 100 ms after it loads: 7 gotos of 40 answered
OK in 0.03 s with the page still at the previous address. A Dot's MCP server
also met the other ending, a goto waiting out its 45 s.

Here the race is made certain rather than waited for: the page being left
rewrites itself 300 ms after it loads, and the server holds the next page's
response for 1.5 s, so the goto is always pending when the rewrite comes.
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

OLD = (b"<html><body><p>first</p><script>setTimeout(() => {document.open();"
       b"document.write('<html><body><h1>rewritten</h1></body></html>');"
       b"document.close()}, 300)</script></body></html>")
NEW = b"<html><body><p>new</p></body></html>"
HELD_S = 1.5


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/new"):
                time.sleep(HELD_S)
                body = NEW
            else:
                body = OLD
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                pass  # the browser let go of the response it stopped

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()


def test_a_goto_the_page_left_aborts_raises_at_once(firefox_binary, site):
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.sync_api import Error

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True) as browser:
        page = browser.new_page()
        for i in range(3):
            page.goto(site + "old?i=%d" % i, wait_until="load")
            began = time.monotonic()
            with pytest.raises(Error) as e:
                page.goto(site + "new?i=%d" % i, wait_until="domcontentloaded",
                          timeout=10_000)
            took = time.monotonic() - began
            assert "NS_BINDING_ABORTED" in e.value.message, e.value.message
            # The rewrite comes 300 ms after the load: the answer follows it,
            # not the held response and never the timeout.
            assert took < HELD_S, took
            # The page is the one that rewrote itself, as the browser has it.
            page.wait_for_function("() => document.querySelector('h1')")
            assert page.url == site + "old?i=%d" % i
