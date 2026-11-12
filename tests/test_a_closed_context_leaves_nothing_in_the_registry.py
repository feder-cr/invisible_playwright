"""A context that is closed leaves nothing behind in a long-lived browser.

NO BROWSER HERE: the real `BrowserDispatcher`, `BrowserContextDispatcher` and
`PageDispatcher` over a scripted Juggler connection that answers the handful of
commands a context and a page need and sends the events a page's birth sends.

Measured before the fix (500 contexts, a page each, opened and closed): the
guid registry went from 7 objects to 1,507, `browser.contexts` kept 500
disposed contexts - each with its options and the connection that owned it -
and `browser._artifacts` kept every download. A recycled context (a server that
renews its context every few dozen fetches) pays that for the life of the
browser. The three children of a context (Debugger, Tracing, APIRequestContext)
were announced under the browser and adopted by the context on the wire, but
`dispose` forgets a subtree by walking `parent`, and theirs stayed the browser.

Known-bad: with `child.parent = self` removed from `BrowserContextDispatcher`,
the registry test fails; with `released` removed from the context, the list test
and the census fail; with the artifact's `released` removed, the artifact test
fails.
"""
from __future__ import annotations

import collections
import gc
import itertools
import os
import tempfile

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_playwright._juggler import remote
from invisible_playwright._juggler.server import (
    ArtifactDispatcher, BrowserContextDispatcher, BrowserDispatcher,
    DebuggerDispatcher, JugglerServer, LocalUtilsDispatcher, PageDispatcher,
    StreamDispatcher, TracingDispatcher)

pytestmark = pytest.mark.unit


class ScriptedJuggler(EventListeners):
    """Just enough of the engine: contexts, pages born with their events, a
    page's close as the engine's detach."""

    def __init__(self):
        super().__init__()
        self._contexts = itertools.count(1)
        self._targets = itertools.count(1)
        self.sessions = {}

    def startup_send(self, method, params=None):
        return {}

    def post(self, method, params=None, session=None):
        pass

    def send(self, method, params=None, session=None, timeout=30):
        params = params or {}
        if method == "Browser.createBrowserContext":
            return {"browserContextId": "c%d" % next(self._contexts)}
        if method == "Browser.newPage":
            n = next(self._targets)
            target, session = "t%d" % n, "s%d" % n
            self.sessions[target] = session
            self.dispatch_event("Browser.attachedToTarget", {
                "sessionId": session,
                "targetInfo": {"targetId": target, "type": "page",
                               "browserContextId": params.get("browserContextId")}},
                None)
            self.dispatch_event("Page.frameAttached",
                                {"frameId": "f" + target, "parentFrameId": None}, session)
            for world in ("", "utility"):
                self.dispatch_event("Runtime.executionContextCreated", {
                    "executionContextId": "e%s%s" % (target, world),
                    "auxData": {"frameId": "f" + target, "name": world}}, session)
            # the real engine says so once the page's initial about:blank is done; the page is handed over on it
            self.dispatch_event("Page.ready", {}, session)
            return {"targetId": target}
        if method == "Page.close":
            target = next(t for t, s in self.sessions.items() if s == session)
            del self.sessions[target]
            self.dispatch_event("Browser.detachedFromTarget",
                                {"targetId": target, "sessionId": session}, None)
        return {}


@pytest.fixture
def browser():
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    conn = ScriptedJuggler()
    browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
    yield browser
    server.shutdown()


def _traffic(conn, session, tag, size=20000):
    """What a fetch does to a page: requests and responses carrying a payload,
    a console line."""
    blob = "x" * size
    for k in range(5):
        rid = "r%s-%d" % (tag, k)
        conn.dispatch_event("Network.requestWillBeSent", {
            "requestId": rid, "url": "http://x/%d" % k, "method": "GET",
            "headers": [{"name": "h", "value": blob}], "frameId": None,
            "navigationId": None, "resourceType": "Script"}, session)
        conn.dispatch_event("Network.responseReceived", {
            "requestId": rid, "status": 200, "statusText": "OK",
            "headers": [{"name": "h", "value": blob}]}, session)
        conn.dispatch_event("Network.requestFinished",
                            {"requestId": rid, "protocolVersion": "h2"}, session)
    conn.dispatch_event("Runtime.console", {"type": "log", "args": [{"value": blob}]},
                        session)


def _cycle(browser, n, *, close_page_first):
    """One recycled context: newContext, newPage, traffic, close."""
    server = browser.server
    context = server.object(server.handle(
        {"guid": browser.guid, "method": "newContext", "params": {}}
    )["context"]["guid"])
    page = server.object(server.handle(
        {"guid": context.guid, "method": "newPage",
         "params": {"pageNumber": n}})["page"]["guid"])
    _traffic(browser.conn, page.session, n)
    if close_page_first:
        server.handle({"guid": page.guid, "method": "close", "params": {}})
    server.handle({"guid": context.guid, "method": "close", "params": {}})
    return context


def _registry(browser):
    return collections.Counter(o.TYPE for o in browser.server._objects.values())


@pytest.mark.parametrize("close_page_first", [True, False])
def test_the_registry_is_what_it_was_after_the_contexts_close(browser, close_page_first):
    before = _registry(browser)
    for n in range(25):
        _cycle(browser, n, close_page_first=close_page_first)
    assert _registry(browser) == before


def test_the_browser_does_not_keep_the_contexts_it_closed(browser):
    for n in range(10):
        _cycle(browser, n, close_page_first=True)
    assert browser.contexts == []
    assert browser._page_targets == {} and browser._sessions == {}
    assert browser._buffered == {} and browser._live == set()


def test_the_count_of_objects_is_stable_across_cycles(browser):
    """The census of what the server builds, after a collection, does not move
    between 20 cycles and 60."""
    kinds = (BrowserContextDispatcher, PageDispatcher, DebuggerDispatcher,
             TracingDispatcher)

    def census():
        gc.collect()
        return collections.Counter(type(o).__name__ for o in gc.get_objects()
                                   if type(o) in kinds)

    for n in range(20):
        _cycle(browser, n, close_page_first=n % 2 == 0)
    first = census()
    for n in range(20, 60):
        _cycle(browser, n, close_page_first=n % 2 == 0)
    assert census() == first
    assert first.get("BrowserContextDispatcher", 0) == 0, first


def test_a_download_and_its_stream_are_let_go_with_their_context(browser):
    server = browser.server
    context = server.object(server.handle(
        {"guid": browser.guid, "method": "newContext", "params": {}}
    )["context"]["guid"])
    directory = tempfile.mkdtemp(prefix="invpw-test-dl-")
    path = os.path.join(directory, "u1")
    with open(path, "wb") as out:
        out.write(b"hello")
    artifact = ArtifactDispatcher(server, context, "u1", path)
    browser._artifacts["u1"] = artifact
    artifact.report_finished(None)
    stream = server.object(artifact.op_stream({})["stream"]["guid"])
    assert isinstance(stream, StreamDispatcher) and not stream._file.closed
    server.handle({"guid": context.guid, "method": "close", "params": {}})
    try:
        assert browser._artifacts == {}
        assert stream._file.closed, "the descendant's file stayed open"
    finally:
        stream._file.close()
        os.remove(path)
        os.rmdir(directory)


def test_the_errors_of_the_wire_are_the_last_few_not_all_of_them():
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    conn = ScriptedJuggler()
    browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
    endpoint = remote.RemoteEndpoint(server, browser_type, browser)
    client = remote.RemoteClient(endpoint, None, None, {})
    client.alive = False
    for _ in range(500):
        client.post_message({"guid": "x", "method": "m", "params": {"bad": {1, 2}}})
    assert 0 < len(endpoint.wire_errors) <= 64
