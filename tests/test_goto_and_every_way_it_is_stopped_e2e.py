"""A goto that something stops or replaces on its way raises at once, or lands
on its page; it never answers OK on another page and never waits out its
timeout ([B302], the class).

Until firefox-46 the engine dropped every abort of the main frame's
navigation, a workaround for the spurious one a Fission process swap fires,
and a navigation the page being left stopped stayed pending: the
document.write() after a document.open() was taken for its commit, and
`goto` answered OK on the OLD page (7 times in 40, firefox-45), or waited out
its timeout (45 s in a Dot's MCP server). No test covered it, because no test
stopped a navigation that was on its way. This one asks every way a page or
the caller can, and checks each answer against upstream Playwright on its own
Firefox (1.63, Firefox 155, `C:/tmp/b230/interrupt_class_probe.py upstream`
and `second_goto_probe.py upstream`, measured 2026-10-14):

- document.open, window.stop: NS_BINDING_ABORTED in 0.3 s, the old page stays;
- location.replace, location.assign, a form submit, an iframe navigating the
  top, history.back: NS_BINDING_ABORTED in 0.3 s, the page goes where the
  page sent it;
- history.pushState, a meta refresh, nothing: the goto lands on its page;
- a second goto from the caller: the first is "interrupted by another
  navigation", the second lands.

Each kind runs with the goto's page on the same site (one process) and on
another (`localhost` against `127.0.0.1`, a Fission process swap, the case the
dropped abort was written for), twice, back to back. The page being left acts
300 ms after it loads, and the server holds the goto's page for 1.5 s, so the
goto is always on its way when the page acts.
"""
from __future__ import annotations

import asyncio
import http.server
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

HELD_S = 1.5
ACT = {
    "docopen": "document.open(); document.write('<html><body><h1>rewritten</h1></body></html>'); document.close();",
    "stop": "window.stop();",
    "replace": "location.replace('/other?from=replace');",
    "assign": "location.assign('/other?from=assign');",
    "pushstate": "history.pushState({}, '', '/pushed');",
    "back": "history.back();",
    "submit": "document.getElementById('f').submit();",
    "meta": None,
    "iframe_top": None,
    "none": "",
}
#: Upstream Playwright's answer to each kind, and where the page then is.
EXPECT = {
    "docopen": ("stopped", "/old"),
    "stop": ("stopped", "/old"),
    "replace": ("stopped", "/other?from=replace"),
    "assign": ("stopped", "/other?from=assign"),
    "back": ("stopped", "/start"),
    "submit": ("stopped", "/other?from=submit"),
    "iframe_top": ("stopped", "/other?from=iframe"),
    "pushstate": ("landed", None),
    "meta": ("landed", None),
    "none": ("landed", None),
}


def _old_page(kind):
    if kind == "meta":
        return ("<html><head><meta http-equiv=refresh content='0.3;url=/other?from=meta'>"
                "</head><body><p>old</p></body></html>")
    if kind == "iframe_top":
        return ("<html><body><p>old</p><iframe srcdoc=\"<script>setTimeout(() => "
                "{top.location = '/other?from=iframe'}, 300)</script>\"></iframe></body></html>")
    return ("<html><body><p>old</p><form id=f action='/other' method=get>"
            "<input name=from value=submit></form><script>setTimeout(() => {%s}, 300)"
            "</script></body></html>" % ACT[kind])


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/new", "/held"):
                time.sleep(HELD_S)
                body = "<html><body><p>new</p></body></html>"
            elif path == "/old":
                body = _old_page(self.path.split("k=")[1].split("&")[0])
            else:
                body = "<html><body><p>%s</p></body></html>" % path
            body = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                pass  # the browser let go of a response it stopped

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.mark.parametrize("kind", list(ACT))
def test_a_goto_stopped_on_its_way_raises_or_lands_and_never_lies(firefox_binary, server, kind):
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.sync_api import Error

    same = "http://127.0.0.1:%d" % server
    cross = "http://localhost:%d" % server
    answer, end = EXPECT[kind]
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        for site, target_base in (("same", same), ("cross", cross)):
            for i in range(2):
                page.goto(same + "/start?k=%s&i=%d" % (kind, i))
                page.goto(same + "/old?k=%s&i=%d" % (kind, i), wait_until="load")
                target = target_base + "/new?k=%s&i=%d" % (kind, i)
                began = time.monotonic()
                error = None
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=10_000)
                except Error as e:
                    error = e.message
                took = time.monotonic() - began
                where = (kind, site, i, error, took)
                if answer == "stopped":
                    assert error is not None and "NS_BINDING_ABORTED" in error, where
                    assert took < HELD_S, where
                    expected = same + end
                    if end in ("/old", "/start"):
                        expected = same + "%s?k=%s&i=%d" % (end, kind, i)
                    page.wait_for_url(expected, timeout=5_000)
                else:
                    assert error is None, where
                    # It waited for its page, which the server held.
                    assert took >= HELD_S - 0.1, where
                    assert page.url == target, (where, page.url)


def test_a_second_goto_interrupts_the_first_and_lands(firefox_binary, server):
    from invisible_playwright.async_api import InvisiblePlaywright

    base = "http://127.0.0.1:%d" % server

    async def run():
        async with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                       headless=True) as browser:
            page = await browser.new_page()
            for i in range(3):
                await page.goto(base + "/start?i=%d" % i)
                began = time.monotonic()

                async def go(url, delay):
                    await asyncio.sleep(delay)
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                        return None
                    except Exception as e:
                        return str(e)
                first, second = await asyncio.gather(
                    go(base + "/held?first=%d" % i, 0), go(base + "/second?i=%d" % i, 0.3))
                took = time.monotonic() - began
                assert first is not None and "is interrupted by another navigation" in first, first
                assert second is None, second
                assert took < HELD_S, took
                assert page.url == base + "/second?i=%d" % i

    asyncio.run(run())
