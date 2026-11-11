"""`goto(wait_until="networkidle")` arrives on a served browser whose context routes every request, as a stock Firefox does.

The shape of a production client: `launch_server()`, a stock Playwright client that connects to it over the WebSocket, a context
route on `**/*` that aborts images, fonts and media and continues the rest, and a page that sends a beacon (`sendBeacon`,
a POST the page does not wait for) and asks for the same font twice. Measured on real news sites through such a client: the
navigation committed, `load` came, and `networkidle` never did (timeout at 30 s on about one fetch in seven, firefox-61 and
firefox-62 alike), while a stock Firefox and Chromium reached it on the same addresses.

Red until the server, the Juggler or the core's lifecycle ends every request a routed page opens.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

PAGE = b"""<!doctype html><html><head><meta charset=utf-8><title>routed</title>
<link rel=stylesheet href=/style.css>
<link rel=preload as=font type=font/woff2 href=/font.woff2 crossorigin>
<style>@font-face{font-family:F2;src:url(/font.woff2) format('woff2')}</style>
<script src=/app.js></script>
</head><body style="font-family:F,F2"><p>text in the font</p>
<img src=/pixel.png><img src=/photo.jpg>
<script>
addEventListener('load', () => {
  navigator.sendBeacon('/beacon', JSON.stringify({ev: 'load'}));
  fetch('/api/data').then(r => r.text());
});
</script></body></html>"""
CSS = b"@font-face{font-family:F;src:url(/font.woff2) format('woff2')} p{color:#333}"
JS = b"window.__app = 1;"
# The other shapes a news page has: a frame of another site (another process with Fission) with its own routed
# resources, a worker that fetches, a script reached through a redirect.
EXTRA = {
    "beacon": b"",
    "oop_iframe": b"<iframe src='http://localhost:%(port)d/frame'></iframe>",
    "worker": b"<script>new Worker('/worker.js')</script>",
    "redirect": b"<script src=/moved.js></script><img src=/moved.png>",
    # What a slow remote client meets: the page cancels requests while the route is still deciding.
    "cancel_fetch": b"<script>for(let i=0;i<30;i++){const c=new AbortController();fetch('/api/data?i='+i,{signal:c.signal}).catch(()=>{});setTimeout(()=>c.abort(),i%7)}</script>",
    "cancel_img": b"<script>for(let i=0;i<30;i++){const m=new Image();m.src='/pixel.png?a='+i;m.src='/pixel.png?b='+i}</script><script src=/slow.js></script>",
    "cancel_iframe": b"<script>for(let i=0;i<6;i++){const f=document.createElement('iframe');f.src='/frame?i='+i;document.body.appendChild(f);setTimeout(()=>f.remove(),5*i)}</script>",
    "cancel_navigation": b"<script>history.pushState({},'','/spa');fetch('/api/data');setTimeout(()=>{history.pushState({},'','/spa2');fetch('/api/data?x=1')},10)</script><script src=/slow.js></script>",
    "many": b"<script>for(let i=0;i<50;i++){fetch('/api/data?i='+i).catch(()=>{});(new Image).src='/pixel.png?i='+i}</script>",
}
# kinds whose route handler is slow (ms). The sync client runs the handlers one after the other, so the page's 100 requests
# of `many` take 100 x (delay + one round trip); an async client (below) answers them all at once, like a Go client does.
SLOW_MS = {"cancel_fetch": 120, "cancel_img": 120, "cancel_iframe": 120, "cancel_navigation": 120, "many": 20}
FRAME = b"""<!doctype html><title>frame</title><link rel=stylesheet href=/style.css><img src=/ad.png>
<script src=/app.js></script><script>addEventListener('load',()=>{navigator.sendBeacon('/beacon','f');fetch('/api/data')})</script>"""
WORKER = b"fetch('/api/data').then(r => r.text()); setTimeout(() => fetch('/api/data'), 200);"


class _Handler(BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def _send(self, body, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        _Handler.hits.append(("GET", path))
        if path == "/":
            kind = (self.path.split("kind=")[1].split("&")[0]) if "kind=" in self.path else "beacon"
            extra = EXTRA[kind] % {b"port": self.server.server_address[1]} if b"%(" in EXTRA[kind] else EXTRA[kind]
            self._send(PAGE.replace(b"</body>", extra + b"</body>"), "text/html; charset=utf-8")
        elif path == "/frame":
            self._send(FRAME, "text/html; charset=utf-8")
        elif path == "/slow.js":
            time.sleep(0.15)
            self._send(JS, "text/javascript")
        elif path == "/worker.js":
            self._send(WORKER, "text/javascript")
        elif path in ("/moved.js", "/moved.png"):
            self.send_response(302)
            self.send_header("Location", "/app.js" if path.endswith(".js") else "/pixel.png")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/style.css":
            self._send(CSS, "text/css")
        elif path == "/app.js":
            self._send(JS, "text/javascript")
        elif path == "/api/data":
            self._send(b'{"ok":1}', "application/json")
        else:  # the font and the images: the route aborts them, they must not get here
            self._send(b"x", "application/octet-stream")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        _Handler.hits.append(("POST", self.path))
        self._send(b"", "text/plain", 204)


@pytest.fixture
def site():
    _Handler.hits = []
    srv = LocalServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d/" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


BLOCKED = {"image", "font", "media"}
ANSWER_ERRORS: list = []


def _router(delay_s):
    def route(route):
        if delay_s:
            time.sleep(delay_s)      # a client far away: the page can cancel the request before the answer arrives
        try:
            if route.request.resource_type in BLOCKED:
                route.abort("blockedbyclient")
            else:
                route.continue_()
        except Exception as e:
            if "Cannot find request" not in str(e) and "Target" not in str(e):
                raise
            ANSWER_ERRORS.append(str(e)[:120])
    return route


@pytest.mark.parametrize("kind", sorted(EXTRA))
def test_networkidle_arrives_on_a_routed_page_through_a_served_browser(firefox_binary, site, kind):
    from invisible_playwright._juggler.remote import launch_server
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright   # a stock client, as production uses

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            ctx = browser.new_context()
            ANSWER_ERRORS.clear()
            ctx.route("**/*", _router(SLOW_MS.get(kind, 0) / 1000.0))
            page = ctx.new_page()
            for attempt in range(3):     # fresh pages, the way a client fetches one address after another
                t0 = time.monotonic()
                page.goto(site + "?kind=%s&n=%d" % (kind, attempt), wait_until="networkidle", timeout=20000)
                took = time.monotonic() - t0
                assert took < 15, "networkidle after %.1f s on attempt %d (answers to a closed request: %s)" % (
                    took, attempt, ANSWER_ERRORS[:3])
                assert page.evaluate("window.__app") == 1
            assert ("POST", "/beacon") in _Handler.hits, _Handler.hits
            assert not any(path in ("/font.woff2", "/pixel.png", "/photo.jpg", "/ad.png") for _, path in _Handler.hits), _Handler.hits
            ctx.close()
            browser.close()
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["many", "cancel_fetch", "cancel_img", "cancel_iframe", "cancel_navigation"])
def test_networkidle_arrives_when_the_client_answers_all_requests_concurrently_and_slowly(firefox_binary, site, kind):
    """An async client (the way a Go client answers: every route handler in its own task) that takes 300 ms to decide, so the
    page cancels, replaces or removes a request while its answer is still on the way."""
    import asyncio

    from invisible_playwright._juggler.remote import launch_server

    async_playwright = pytest.importorskip("playwright.async_api").async_playwright
    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    errors = []

    async def run():
        async with async_playwright() as p:
            browser = await p.firefox.connect(server.ws_endpoint)
            ctx = await browser.new_context()

            async def route(r):
                await asyncio.sleep(0.3)
                try:
                    if r.request.resource_type in BLOCKED:
                        await r.abort("blockedbyclient")
                    else:
                        await r.continue_()
                except Exception as e:
                    errors.append(str(e)[:140])

            await ctx.route("**/*", route)
            page = await ctx.new_page()
            for attempt in range(3):
                t0 = time.monotonic()
                await page.goto(site + "?kind=%s&n=%d" % (kind, attempt), wait_until="networkidle", timeout=25000)
                assert time.monotonic() - t0 < 20, "networkidle after %.1f s on attempt %d" % (time.monotonic() - t0, attempt)
            await ctx.close()
            await browser.close()

    try:
        asyncio.run(run())
    finally:
        server.close()
    # an answer to a request the browser had already closed is allowed to fail, but it must not leave the page waiting
    print("answers to a closed request:", errors[:3])


def test_one_page_reused_for_many_addresses_reaches_networkidle_every_time(firefox_binary, site):
    """tf-fetch's shape: ONE page for dozens of fetches. A request whose end never reaches the client (a beacon the
    page closes with its document, a request the browser cancelled while the route was deciding) must not hold the
    addresses that follow: the lifecycle's memory of requests in flight is per document, keyed by request id."""
    from invisible_playwright._juggler.remote import launch_server

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            ctx = browser.new_context()
            ctx.route("**/*", _router(0.05))
            page = ctx.new_page()
            kinds = ["cancel_fetch", "cancel_img", "cancel_iframe", "beacon", "cancel_navigation", "oop_iframe"] * 2
            for n, kind in enumerate(kinds):
                t0 = time.monotonic()
                page.goto(site + "?kind=%s&n=%d" % (kind, n), wait_until="networkidle", timeout=20000)
                assert time.monotonic() - t0 < 15, "address %d (%s) reached networkidle after %.1f s" % (n, kind, time.monotonic() - t0)
            ctx.close()
            browser.close()
    finally:
        server.close()
