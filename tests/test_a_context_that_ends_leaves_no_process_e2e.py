"""A context that ends leaves no content process behind, on a real engine.

⛔ THE KNOWN-BAD INPUT, measured on the engine before the fix: a page that registers a service worker whose
script is `for (;;) {}` (an endless install handler) keeps its content process alive after its context is
closed. The worker never answers the shutdown, no shutdown starts for the process, and nothing else ends it:
four such contexts left four more processes (8 web content processes at the end instead of 4). The engine's
BrowserContext now waits a few seconds for the processes of its container once its pages are gone, and kills the
ones that stay.

The test closes five such contexts in a row. While each page is open it counts the processes that host a site
(at least one: the page's, or the count would prove nothing), and once `close` has answered it waits a few
seconds for them to be gone: none may be left (on the old engine every context leaves its own, one more each
time). Only the processes that host a site are counted (tests/_processes.py): the engine refills its pool of
preallocated content processes behind a closed context, in about half the runs, and counting every content
process read that refill as a leak ([2, 2, 2, 5, 5] on the e2e of the 0.50.0 commit, three "Web Content" and no
site left). Off Linux the test skips: no other system names the process.

The engine that does it is not published when this file is written: `engine_at_least` makes the test wait for
it, and on an older engine it is skipped, never passed.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

pytest.importorskip("websockets")
from websockets.asyncio.client import connect  # noqa: E402

from _processes import site_processes_below  # noqa: E402
from _servers import LocalServer  # noqa: E402

pytestmark = pytest.mark.e2e

ENGINE_WITH_THE_FIX = "firefox-61"

PAGE = (b"<!doctype html><title>sw</title><script>"
        b"navigator.serviceWorker.register('/sw.js').catch(e => { document.title = 'ERR ' + e; });</script>")
WORKER = b"self.addEventListener('install', () => { for (;;) {} }); self.addEventListener('fetch', () => {});"


def _serve():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, kind = (WORKER, "text/javascript") if self.path.startswith("/sw.js") else (PAGE, "text/html")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


class _Client:
    """The few calls of Playwright's protocol this test needs, over the served WebSocket."""

    def __init__(self, ws):
        self.ws, self.objects, self.counter = ws, {}, 0

    async def call(self, guid, method, params=None):
        self.counter += 1
        await self.ws.send(json.dumps({"id": self.counter, "guid": guid, "method": method,
                                       "params": params or {}, "metadata": {}}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("method") == "__create__":
                self.objects[message["params"]["guid"]] = message["params"]
            if message.get("id") == self.counter:
                assert "error" not in message, message
                return message.get("result")

    async def start(self):
        await self.call("", "initialize", {"sdkLanguage": "python"})
        self.browser = [g for g, o in self.objects.items() if o["type"] == "Browser"][0]

    async def new_page(self):
        context = (await self.call(self.browser, "newContext", {}))["context"]["guid"]
        page = (await self.call(context, "newPage", {}))["page"]["guid"]
        frame = self.objects[page]["initializer"]["mainFrame"]["guid"]
        return context, frame


def test_closed_contexts_with_a_never_stopping_service_worker_leave_no_process(firefox_binary, engine_at_least):
    engine_at_least(ENGINE_WITH_THE_FIX)
    from invisible_playwright import launch_server

    server, base = _serve()
    launched = launch_server(host="127.0.0.1", port=0, headless=True, virtual_display=False, humanize=False,
                             seed=4266, binary_path=firefox_binary, launch_timeout_ms=180_000)

    def sites():
        return site_processes_below(launched.process.pid)

    async def drive():
        opened, left = [], []
        async with connect(launched.ws_endpoint, max_size=None) as ws:
            client = _Client(ws)
            await client.start()
            for i in range(5):
                context, frame = await client.new_page()
                await client.call(frame, "goto", {"url": "%s/?i=%d" % (base, i), "waitUntil": "load", "timeout": 30000})
                await asyncio.sleep(1.0)               # the worker is registered, its install loop is running
                opened.append(len(sites()))
                await client.call(context, "close")    # the engine waits for the context's processes, then kills
                deadline = time.time() + 5
                while sites() and time.time() < deadline:
                    await asyncio.sleep(0.25)
                left.append(len(sites()))
        return opened, left

    try:
        opened, left = asyncio.run(drive())
    finally:
        launched.close()
        server.shutdown()
    assert all(opened), "a page with no process for its site: the count proves nothing (%r)" % (opened,)
    assert left == [0] * 5, "processes of a site left after each closed context: %r" % (left,)
