"""A goto to the address the page is at ends, whatever the page is made of (firefox-62, sites isolated: strategy 1).

With sites isolated an iframe of another site is born in the process of its page and goes to the process of its site.
A goto to the address the page is already at replaces the document in the same process, and the children of the
document it replaces are detached when the new one is inserted. The page world of such an iframe is its
WindowProxy, which is a remote one by then: the Debugger refused to remove it ("argument is not a global object"),
the detach threw, and the new document was never told to the parent. The goto then ended with "load not reached in 45s
(our navigation nav-N started and has neither committed nor been aborted)", on pages of every size and header
(MDN, css-tricks, cnn), while a goto to another address, a reload and a fragment were fine.

Each goto here must end, with the response, the address and the frames it has: 20 in a row, on the kinds of page below
(a plain one, a big one, one revalidated with a 304, one fresh in the cache, one that is never stored, a page with a
frame of its own site, one with a frame of another site, one with that frame revalidated, and a -> b -> a). Hosts
`a.test`, `b.test` and `c.test` are three sites on one server (`network.dns.localDomains`).
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

FISSION_ON = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
    "network.dns.localDomains": "a.test,b.test,c.test",
}

GOTOS = 20

BIG = "<p>" + ("lorem ipsum dolor sit amet " * 40) + "</p>\n"


def _body(kind, port):
    """(html, how many frames the page has, the title)."""
    frame = '<iframe src="http://%s:%d/%s"></iframe>'
    if kind == "plain":
        return "<p>plain</p>", 1
    if kind == "big":
        return BIG * 1200, 1                                   # about 1.3 MB
    if kind in ("etag", "fresh", "nostore"):
        return "<p>%s</p>" % kind, 1
    if kind == "same-site-frame":
        return "<p>top</p>" + frame % ("a.test", port, "plain"), 2
    if kind == "other-site-frame":
        return "<p>top</p>" + frame % ("b.test", port, "plain"), 2
    if kind == "other-site-frame-etag":
        return "<p>top</p>" + frame % ("b.test", port, "etag"), 2
    if kind == "a-b-a":
        return "<p>top</p>" + frame % ("b.test", port, "mid"), 3
    if kind == "mid":
        return "<p>mid</p>" + frame % ("a.test", port, "plain"), 2
    raise AssertionError(kind)


KINDS = ["plain", "big", "etag", "fresh", "nostore", "same-site-frame", "other-site-frame",
         "other-site-frame-etag", "a-b-a"]


@pytest.fixture
def site():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 10

        def log_message(self, *args):
            pass

        def do_GET(self):
            kind = self.path.split("?")[0].strip("/")
            port = self.server.server_address[1]
            if kind == "favicon.ico":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            html, _ = _body(kind, port)
            body = ("<!doctype html><title>%s</title>%s" % (kind, html)).encode()
            headers = {"Content-Type": "text/html"}
            if kind.endswith("etag"):
                tag = '"%s"' % hashlib.md5(body).hexdigest()
                headers["ETag"] = tag
                headers["Cache-Control"] = "max-age=0, must-revalidate"
                if self.headers.get("If-None-Match") == tag:
                    self.send_response(304)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            elif kind == "fresh":
                headers["Cache-Control"] = "max-age=3600"
            elif kind == "nostore":
                headers["Cache-Control"] = "no-store"
            try:
                self.send_response(200)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join()


def _run(firefox_binary, scenario):
    from invisible_playwright.async_api import InvisiblePlaywright

    async def go():
        async with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True,
                                       extra_prefs=FISSION_ON) as browser:
            page = await browser.new_page()
            return await scenario(page)

    return asyncio.run(go())


@pytest.mark.parametrize("kind", KINDS)
def test_a_goto_to_the_address_the_page_is_at_ends_every_time(firefox_binary, site, kind):
    port = site
    url = "http://a.test:%d/%s" % (port, kind)
    _, frames = _body(kind, port)

    async def scenario(page):
        await page.goto(url, timeout=15000)
        seen = []
        for i in range(GOTOS):
            began = time.monotonic()
            response = await page.goto(url, timeout=15000)
            took = time.monotonic() - began
            title = await page.title()
            seen.append((i, response.status if response else None, page.url, title, len(page.frames), round(took, 2)))
        return seen

    seen = _run(firefox_binary, scenario)
    assert len(seen) == GOTOS
    answers = (200, 304) if kind.endswith("etag") else (200,)   # a revalidated page is answered 304, and says so
    for i, status, address, title, count, took in seen:
        assert status in answers, seen[i]
        assert address == url, seen[i]
        assert title == kind, seen[i]
        assert count == frames, seen[i]
        assert took < 10, seen[i]


def test_a_goto_to_the_same_address_with_a_query_ends_the_second_time_too(firefox_binary, site):
    """The query is another address once, and the same one after: MDN's `?x=1` twice."""
    port = site
    url = "http://a.test:%d/other-site-frame?x=1" % port

    async def scenario(page):
        await page.goto("http://a.test:%d/other-site-frame" % port, timeout=15000)
        out = []
        for _ in range(3):
            response = await page.goto(url, timeout=15000)
            out.append((response.status if response else None, page.url, len(page.frames)))
        return out

    out = _run(firefox_binary, scenario)
    assert out == [(200, url, 2)] * 3, out
