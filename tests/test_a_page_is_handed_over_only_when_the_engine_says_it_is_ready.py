"""A page is handed over to the client only once the engine says Page.ready.

NO BROWSER HERE: the real dispatchers over a scripted Juggler connection (the one of the registry test) whose pages announce their
frame at birth and say `Page.ready` only when the test tells them to, as the real engine does after its transient about:blank
navigations.

⛔ THE KNOWN-BAD INPUT. The engine commits the page's initial about:blank AFTER it announces the frame; a `goto` sent in between is
'interrupted by another navigation to about:blank'. A wrapper that hands the page over on the main frame alone (no wait for
Page.ready) returns `new_page()` while the engine is still in that window; so does the adoption of a popup.

Known-bad: with the `wait_for_page_ready` call of `PageDispatcher.__init__` removed (a21e1de), both tests fail - the page is
delivered before Page.ready; with it, the page is delivered right after.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_playwright._juggler import remote
from invisible_playwright._juggler.server import LocalUtilsDispatcher

from test_a_closed_context_leaves_nothing_in_the_registry import ScriptedJuggler

pytestmark = pytest.mark.unit

#: how long a page that must NOT be delivered yet is watched
WATCH = 0.4


class LateReadyJuggler(ScriptedJuggler):
    """The engine of the registry test, except that Page.ready is held back until `say_ready(session)`."""

    def __init__(self):
        super().__init__()
        self.held = {}
        self.released = set()

    def dispatch_event(self, method, params, session=None):
        if method == "Page.ready" and session not in self.released:
            self.held[session] = (method, params)
            return None
        return super().dispatch_event(method, params, session)

    def say_ready(self, session):
        self.released = set(self.released) | {session}
        method, params = self.held.pop(session)
        super().dispatch_event(method, params, session)

    def popup_born(self, opener_target, context_id, n):
        """A target the opener opened: attached and framed, but not ready."""
        target, session = "p%d" % n, "ps%d" % n
        self.sessions[target] = session
        self.dispatch_event("Browser.attachedToTarget", {
            "sessionId": session,
            "targetInfo": {"targetId": target, "type": "page", "openerId": opener_target,
                           "browserContextId": context_id}}, None)
        self.dispatch_event("Page.frameAttached", {"frameId": "f" + target, "parentFrameId": None}, session)
        for world in ("", "utility"):
            self.dispatch_event("Runtime.executionContextCreated", {
                "executionContextId": "e%s%s" % (target, world),
                "auxData": {"frameId": "f" + target, "name": world}}, session)
        self.dispatch_event("Page.ready", {}, session)
        return target, session


@pytest.fixture
def late_browser():
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    conn = LateReadyJuggler()
    browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
    yield browser
    for session in list(conn.held):
        conn.say_ready(session)
    server.shutdown()


def _new_context(browser):
    server = browser.server
    return server.object(server.handle(
        {"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])


def test_new_page_returns_only_after_page_ready(late_browser):
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    answer = {}

    def new_page():
        answer["reply"] = server.handle(
            {"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})

    worker = threading.Thread(target=new_page, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not conn.held and time.monotonic() < deadline:
        time.sleep(0.01)
    assert conn.held, "the page was never born in the scripted engine"
    worker.join(WATCH)
    assert worker.is_alive() and "reply" not in answer, (
        "new_page() returned before the engine said Page.ready: the page would be handed over inside the window where a goto is "
        "interrupted by the engine's own about:blank")
    assert not context.pages, "the page was registered before it was ready"
    (session,) = conn.held
    conn.say_ready(session)
    worker.join(5)
    assert not worker.is_alive() and "reply" in answer, "new_page() did not return after Page.ready"
    assert len(context.pages) == 1


def test_a_popup_is_announced_only_after_page_ready(late_browser):
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    # the opener: ready at once
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    assert len(context.pages) == 1
    opener_target = next(iter(conn.sessions))
    announced = []
    original = context.emit

    def spy(event, params=None):
        if event == "page":
            announced.append(time.monotonic())
        return original(event, params)

    context.emit = spy
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    time.sleep(WATCH)
    assert not announced and len(context.pages) == 1, (
        "the popup was announced before the engine said Page.ready for it")
    conn.say_ready(session)
    deadline = time.monotonic() + 5
    while not announced and time.monotonic() < deadline:
        time.sleep(0.01)
    assert announced, "the popup was never announced after Page.ready"
    assert len(context.pages) == 2
