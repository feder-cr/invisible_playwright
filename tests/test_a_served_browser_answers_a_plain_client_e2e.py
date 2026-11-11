"""`launch_server()` launches ONE real browser and a client that only speaks the protocol
over a WebSocket drives it: a context, a page, a navigation, a read. Then it closes it all.

The client is hand-written on `websockets` (no Playwright on this side), which is the
point: nothing of this package is in the client. One browser, once.

Known-bad, run before this file was trusted: with no `remote.py` the import fails.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import websockets  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
from websockets.asyncio.client import connect  # noqa: E402

pytestmark = pytest.mark.e2e


async def _drive(endpoint):
    objects = {}
    pending = {}
    async with connect(endpoint, max_size=None) as ws:
        counter = [0]

        async def call(guid, method, params=None):
            counter[0] += 1
            await ws.send(json.dumps({"id": counter[0], "guid": guid, "method": method,
                                      "params": params or {}, "metadata": {}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("method") == "__create__":
                    objects[m["params"]["guid"]] = m["params"]
                if m.get("id") == counter[0]:
                    assert "error" not in m, m
                    return m.get("result")

        init = await call("", "initialize", {"sdkLanguage": "python"})
        browser = [g for g, p in objects.items() if p["type"] == "Browser"][0]
        ctx = (await call(browser, "newContext", {}))["context"]["guid"]
        page = (await call(ctx, "newPage", {}))["page"]["guid"]
        frame = objects[page]["initializer"]["mainFrame"]["guid"]
        await call(frame, "goto", {"url": "data:text/html,<title>served</title>",
                                   "waitUntil": "load", "timeout": 30000})
        title = (await call(frame, "title", {}))["value"]
        await call(ctx, "close", {})
        return title


def test_a_plain_client_drives_a_served_browser(firefox_binary):
    from invisible_playwright._juggler.remote import launch_server
    closed = []
    server = launch_server(headless=True, binary_path=firefox_binary,
                           locale="en-US", timezone="Europe/Rome",
                           on_engine_closed=lambda: closed.append(1))
    try:
        assert server.ws_endpoint.startswith("ws://127.0.0.1:")
        pid = server.process.pid
        assert isinstance(server.seed, int)
        title = asyncio.run(asyncio.wait_for(_drive(server.ws_endpoint), 120))
        assert title == "served"
        assert server.process.poll() is None      # the client leaving left the browser
    finally:
        server.close()
    assert server.process.poll() is not None
    assert closed == []                           # close() is not "the engine died"
