"""The Playwright server on a WebSocket: what any client sees, with no browser started.

`_juggler/remote.py` serves one launched browser's registry to many connections. Here the
browser is a stand-in `Dispatcher` of type Browser, so everything that is the SERVER's own
is real: the WebSocket, the handshake, `initialize`, the ownership of objects, the
defaults a context gets.

Known-bad, run before this file was trusted: with no `remote.py` the import fails; with
`_process_request` comparing nothing (every path accepted) the wrong-token test goes red;
with `route` sending every message to every client, the isolation test goes red; with
`RemoteEndpoint.host` defaulting to 0.0.0.0 the bind test goes red.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from urllib.parse import quote

import pytest

import websockets  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip

from websockets.asyncio.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed, InvalidStatus  # noqa: E402

from invisible_playwright._juggler import remote  # noqa: E402
from invisible_playwright._juggler.dispatcher import Dispatcher  # noqa: E402
from invisible_playwright._juggler.server import LocalUtilsDispatcher  # noqa: E402

pytestmark = pytest.mark.unit


class FakeContext(Dispatcher):
    TYPE = "BrowserContext"
    METHODS = {"close": "op_close", "ping": "op_ping"}
    closed: list = []

    def op_close(self, params):
        FakeContext.closed.append(self.guid)
        self.dispose()

    def op_ping(self, params):
        self.emit("pong", {"n": 1})
        return {"ok": True}


class FakeBrowser(Dispatcher):
    TYPE = "Browser"
    METHODS = {"newContext": "op_new_context", "newPage": "op_new_page"}

    def __init__(self, server, parent):
        super().__init__(server, parent, {"version": "firefox/1", "name": "firefox",
                                          "browserName": "firefox"})
        self.seen = []

    def op_new_context(self, params):
        self.seen.append(("newContext", params))
        return {"context": FakeContext(self.server, self).channel}

    def op_new_page(self, params):
        self.seen.append(("newPage", params))
        return None


@pytest.fixture
def served():
    FakeContext.closed = []
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    browser = FakeBrowser(server, browser_type)
    endpoint = remote.RemoteEndpoint(
        server, browser_type, browser,
        context_defaults={"viewport": {"width": 1000, "height": 700},
                          "locale": "it-IT", "timezoneId": "Europe/Rome"})
    endpoint.start()
    yield endpoint
    endpoint.close()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def call(ws, msg_id, guid, method, params=None):
    """Send one call; return (the messages before its reply, the reply)."""
    await ws.send(json.dumps({"id": msg_id, "guid": guid, "method": method,
                              "params": params or {}, "metadata": {}}))
    before = []
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == msg_id:
            return before, message
        before.append(message)


def test_initialize_creates_the_launch_server_objects(served):
    async def go():
        async with connect(served.ws_endpoint) as ws:
            before, reply = await call(ws, 1, "", "initialize", {"sdkLanguage": "python"})
            return before, reply
    before, reply = run(go())
    created = [(m["params"]["type"], m["params"]["guid"]) for m in before
               if m["method"] == "__create__"]
    assert [t for t, _ in created] == ["BrowserType"] * 3 + ["Android", "Electron", "LocalUtils",
                                                              "Browser", "Playwright"]
    assert reply["result"] == {"playwright": {"guid": "Playwright"}}
    playwright = before[-1]["params"]["initializer"]
    assert playwright["preLaunchedBrowser"] == {"guid": served.browser.guid}
    assert playwright["firefox"] == {"guid": served.browser_type.guid}


def test_unknown_handshake_headers_and_any_user_agent_are_accepted(served):
    async def go():
        headers = {"x-playwright-browser": "chromium",
                   "x-playwright-launch-options": json.dumps({"headless": False, "x": [1]}),
                   "x-playwright-proxy": "*",
                   "x-playwright-client-version": "9.99.0",
                   "x-something-else": "?",
                   "User-Agent": "Playwright/0.0.1 (a client that does not exist)"}
        async with connect(served.ws_endpoint, additional_headers=headers) as ws:
            return await call(ws, 1, "", "initialize", {})
    _, reply = run(go())
    assert "result" in reply
    assert served.clients == [] or served.clients[0].handshake["browser"] == "chromium"


def test_the_query_form_of_the_handshake_is_read(served):
    async def go():
        url = served.ws_endpoint + "?browser=firefox&launch-options=" + quote(json.dumps({"a": 1}))
        async with connect(url) as ws:
            await call(ws, 1, "", "initialize", {})
            return list(served.clients)
    clients = run(go())
    assert clients[0].handshake["browser"] == "firefox"
    assert clients[0].handshake["launch_options"] == {"a": 1}


def test_a_wrong_token_is_refused_with_a_reason(served):
    bad = served.ws_endpoint.rsplit("/", 1)[0] + "/not-the-secret"

    async def go():
        async with connect(bad):
            pass
    with pytest.raises(InvalidStatus) as refused:
        run(go())
    assert refused.value.response.status_code == 403
    assert b"launch_server()" in refused.value.response.body


def test_launch_options_that_are_not_json_are_refused_with_a_reason(served):
    async def go():
        async with connect(served.ws_endpoint,
                           additional_headers={"x-playwright-launch-options": "{nope"}):
            pass
    with pytest.raises(InvalidStatus) as refused:
        run(go())
    assert refused.value.response.status_code == 400
    assert b"not JSON" in refused.value.response.body


def test_a_message_that_is_not_json_closes_with_a_reason_and_the_server_lives(served):
    async def go():
        async with connect(served.ws_endpoint) as ws:
            await ws.send("this is not json")
            with pytest.raises(ConnectionClosed) as closed:
                await ws.recv()
            reason = closed.value.rcvd.reason
        async with connect(served.ws_endpoint) as ws:
            _, reply = await call(ws, 1, "", "initialize", {})
        return reason, reply
    reason, reply = run(go())
    assert "not a protocol message" in reason
    assert "result" in reply


def test_it_binds_to_loopback_by_default(served):
    assert inspect.signature(remote.launch_server).parameters["host"].default == "127.0.0.1"
    assert inspect.signature(remote.RemoteEndpoint).parameters["host"].default == "127.0.0.1"
    assert served.ws_endpoint.startswith("ws://127.0.0.1:")
    assert len(served.token) >= 16


def test_a_client_sees_only_its_own_objects(served):
    async def go():
        async with connect(served.ws_endpoint) as a, connect(served.ws_endpoint) as b:
            await call(a, 1, "", "initialize", {})
            await call(b, 1, "", "initialize", {})
            _, made = await call(a, 2, served.browser.guid, "newContext", {})
            guid = made["result"]["context"]["guid"]
            before_a, ping = await call(a, 3, guid, "ping")
            _, stolen = await call(b, 2, guid, "ping")
            # b gets nothing a did: its next reply is the only thing waiting.
            _, again = await call(b, 3, served.browser.guid, "newContext", {})
            return before_a, ping, stolen, again
    before_a, ping, stolen, again = run(go())
    assert any(m.get("method") == "pong" for m in before_a)
    assert ping["result"] == {"ok": True}
    assert "never created" in stolen["error"]["error"]["message"]
    assert "result" in again


def test_a_leaving_client_closes_its_contexts_and_not_the_browser(served):
    async def go():
        async with connect(served.ws_endpoint) as ws:
            await call(ws, 1, "", "initialize", {})
            _, made = await call(ws, 2, served.browser.guid, "newContext", {})
            return made["result"]["context"]["guid"]
    guid = run(go())
    for _ in range(100):
        if guid in FakeContext.closed:
            break
        import time
        time.sleep(0.05)
    assert FakeContext.closed == [guid]
    assert not served.browser.disposed


def test_a_new_context_gets_the_sessions_defaults_where_the_client_says_nothing(served):
    async def go():
        async with connect(served.ws_endpoint) as ws:
            await call(ws, 1, "", "initialize", {})
            await call(ws, 2, served.browser.guid, "newContext", {"locale": "fr-FR"})
            await call(ws, 3, served.browser.guid, "newContext", {"noDefaultViewport": True})
    run(go())
    (_, first), (_, second) = served.browser.seen
    assert first["locale"] == "fr-FR"                       # the client's wins
    assert first["viewport"] == {"width": 1000, "height": 700}
    assert first["timezoneId"] == "Europe/Rome"
    assert "viewport" not in second and second["locale"] == "it-IT"


def test_a_client_without_a_page_number_gets_one(served):
    async def go():
        async with connect(served.ws_endpoint) as ws:
            await call(ws, 1, "", "initialize", {})
            await call(ws, 2, served.browser.guid, "newPage", {})
            await call(ws, 3, served.browser.guid, "newPage", {})
    run(go())
    numbers = [p["pageNumber"] for _, p in served.browser.seen]
    assert numbers == [0, 1]


#: The `Playwright` initializer of protocol 1.62 (packages/protocol/spec/playwright.yml): the
#: field -> the type it names; required unless it is in OPTIONAL. A client built on it reads every
#: required one at construction and does not check for a missing one (playwright-go's
#: newPlaywright panics: "fromChannel: expected *channel, got <nil>").
SPEC_162_INITIALIZER = {"chromium": "BrowserType", "firefox": "BrowserType", "webkit": "BrowserType",
                        "android": "Android", "electron": "Electron", "utils": "LocalUtils",
                        "preLaunchedBrowser": "Browser", "preConnectedAndroidDevice": "AndroidDevice",
                        "socksSupport": "SocksSupport"}
OPTIONAL = {"utils", "preLaunchedBrowser", "preConnectedAndroidDevice", "socksSupport"}


def test_the_playwright_initializer_is_the_one_of_spec_162(served):
    """Known-bad: the initializer with firefox, utils and preLaunchedBrowser only."""
    async def go():
        async with connect(served.ws_endpoint) as ws:
            before, _ = await call(ws, 1, "", "initialize", {"sdkLanguage": "javascript"})
            return before
    before = run(go())
    types = {m["params"]["guid"]: m["params"]["type"] for m in before if m["method"] == "__create__"}
    order = [m["params"]["guid"] for m in before if m["method"] == "__create__"]
    initializer = before[-1]["params"]["initializer"]
    assert before[-1]["params"]["type"] == "Playwright"
    for field, kind in SPEC_162_INITIALIZER.items():
        if field not in initializer:
            assert field in OPTIONAL, "required field %s is missing" % field
            continue
        guid = initializer[field]["guid"]
        assert types[guid] == kind, (field, types[guid])
        assert order.index(guid) < len(order) - 1, "%s is announced after Playwright" % field
    assert set(initializer) <= set(SPEC_162_INITIALIZER)


def test_chromium_and_webkit_are_real_browser_types_that_refuse_with_a_reason(served):
    async def go():
        async with connect(served.ws_endpoint) as ws:
            before, _ = await call(ws, 1, "", "initialize", {"sdkLanguage": "javascript"})
            initializer = before[-1]["params"]["initializer"]
            out = {}
            for n, name in enumerate(("chromium", "webkit"), start=2):
                _, reply = await call(ws, n, initializer[name]["guid"], "launch", {})
                out[name] = reply
            return out
    out = run(go())
    for name, reply in out.items():
        assert "error" in reply
        assert name in reply["error"]["error"]["message"]
        assert "not" in reply["error"]["error"]["message"]
