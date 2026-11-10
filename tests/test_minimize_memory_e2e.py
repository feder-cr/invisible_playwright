"""`Heap.minimizeMemory` and INVPW_GC_MODE=minimize on a real engine.

The engine that has the command is not published when this file is written: `engine_at_least` makes the
tests wait for it, and on an older engine they are skipped, never passed. (On an older engine the option
falls back to `Heap.collectGarbage` with one warning: tests/test_gc_every_navigation.py.)

What is checked, with a browser served by `launch_server` and a client connected to it:

1. The option sends the command and the engine takes it: after a few navigations `nav_gc` counts the
   rounds it started, none failed and none fell back to `gc`.
2. It does what a real Firefox short of memory does to the bfcache, and this file DOCUMENTS it: a page the tab
   left is restored from the bfcache (`pageshow.persisted == true`) on `go_back()`, and after
   `Heap.minimizeMemory` it is not (`persisted == false`: the document is loaded again). The control is the
   same sequence with nothing sent. No page can read the command; it can read this, and only by going back
   in the history of the tab, which a fetcher that loads a page and moves on never does.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("websockets")
from websockets.asyncio.client import connect  # noqa: E402

from _servers import LocalServer  # noqa: E402

pytestmark = pytest.mark.e2e

ENGINE_WITH_THE_COMMAND = "firefox-61"

PAGE = (b"<html><head><title>%s</title></head><body>%s"
        b"<script>window.shows = [];"
        b"addEventListener('pageshow', e => window.shows.push(e.persisted));</script></body></html>")


def _serve():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.strip("/").encode() or b"index"
            body = PAGE % (name, b"page " + name)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _launch(firefox_binary):
    from invisible_playwright import launch_server

    return launch_server(host="127.0.0.1", port=0, headless=True, virtual_display=False, humanize=False,
                         seed=4261, binary_path=firefox_binary, launch_timeout_ms=180_000)


class _Client:
    """The few calls of Playwright's protocol these tests need, over the served WebSocket (the vendored client has
    no `connect`, and the installed Playwright is not imported here)."""

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
        browser = [g for g, o in self.objects.items() if o["type"] == "Browser"][0]
        self.context = (await self.call(browser, "newContext", {}))["context"]["guid"]
        self.page = (await self.call(self.context, "newPage", {}))["page"]["guid"]
        self.frame = self.objects[self.page]["initializer"]["mainFrame"]["guid"]

    async def goto(self, url):
        await self.call(self.frame, "goto", {"url": url, "waitUntil": "load", "timeout": 30000})

    async def back(self):
        await self.call(self.page, "goBack", {"waitUntil": "load", "timeout": 30000})

    async def title(self):
        return (await self.call(self.frame, "title"))["value"]

    async def last_pageshow(self):
        result = await self.call(self.frame, "evaluateExpression", {
            "expression": "window.shows[window.shows.length - 1]", "isFunction": False,
            "arg": {"value": {"v": "undefined"}, "handles": []}})
        return result["value"]["b"]


def test_the_option_sends_minimize_memory_and_the_engine_takes_it(firefox_binary, engine_at_least, monkeypatch):
    engine_at_least(ENGINE_WITH_THE_COMMAND)
    monkeypatch.setenv("INVPW_GC_EVERY_NAV", "2")
    monkeypatch.setenv("INVPW_GC_MODE", "minimize")
    server, base = _serve()
    launched = _launch(firefox_binary)

    async def drive(nav_gc):
        async with connect(launched.ws_endpoint, max_size=None) as ws:
            client = _Client(ws)
            await client.start()
            for i in range(6):
                await client.goto("%s/p%d" % (base, i))
                await asyncio.get_running_loop().run_in_executor(None, nav_gc.join, 60)
            await client.call(client.context, "close")

    try:
        nav_gc = launched._endpoint.nav_gc
        assert nav_gc is not None and nav_gc.mode == "minimize"
        asyncio.run(drive(nav_gc))
        assert nav_gc.commits >= 6 and nav_gc.started >= 3
        assert nav_gc.failed == 0, "the engine refused a round"
        assert (nav_gc.effective_mode, nav_gc.fell_back) == ("minimize", 0), "the engine has no such command"
    finally:
        launched.close()
        server.shutdown()


def _restored_from_bfcache(firefox_binary, minimize, monkeypatch):
    """Load A, leave for B, optionally minimize, go back: the `persisted` of the last pageshow of A."""
    monkeypatch.delenv("INVPW_GC_EVERY_NAV", raising=False)
    server, base = _serve()
    launched = _launch(firefox_binary)

    async def drive():
        async with connect(launched.ws_endpoint, max_size=None) as ws:
            client = _Client(ws)
            await client.start()
            await client.goto(base + "/a")
            await client.goto(base + "/b")
            await asyncio.sleep(0.5)
            if minimize:
                dispatcher = launched._endpoint.browser.contexts[-1].pages[-1]
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: dispatcher.send("Heap.minimizeMemory", {}, timeout=120))
                await asyncio.sleep(0.5)
            await client.back()
            await asyncio.sleep(0.5)
            assert await client.title() == "a"
            persisted = await client.last_pageshow()
            await client.call(client.context, "close")
            return persisted

    try:
        return asyncio.run(drive())
    finally:
        launched.close()
        server.shutdown()


def test_a_page_the_tab_left_is_restored_from_the_bfcache_when_nothing_is_sent(firefox_binary, engine_at_least,
                                                                              monkeypatch):
    engine_at_least(ENGINE_WITH_THE_COMMAND)
    assert _restored_from_bfcache(firefox_binary, False, monkeypatch) is True


def test_minimize_memory_evicts_the_bfcache_as_a_real_firefox_under_memory_pressure_does(firefox_binary,
                                                                                         engine_at_least,
                                                                                         monkeypatch):
    engine_at_least(ENGINE_WITH_THE_COMMAND)
    assert _restored_from_bfcache(firefox_binary, True, monkeypatch) is False
