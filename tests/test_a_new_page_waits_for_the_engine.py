"""`newPage` waits for the engine's answer, bounded only by the caller (issue #321).

⛔ A FIXED 30 s ON THE REPLY WAS SHORTER THAN THE CALLER'S OWN BOUND. Playwright's
newPage carries no timeout, so the server put one in: `Browser.newPage` with
`timeout=30`. On a loaded host, where the launch itself had taken 36 to 115 s, the
first navigation of an MCP session (45 s of its own) failed at 31 s, and the same
navigation repeated loaded normally: 5 failures in 773 page calls of a real audit.

The engine here answers the way the core's `Connection.send` would make a slow
one look: any finite bound shorter than its 35 s answer raises, as the real
connection raises "no response in 30s". A bound of None is waited out.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners, ProtocolError
from invisible_playwright._juggler import server as srv
from invisible_playwright._juggler.server import (
    BrowserContextDispatcher, BrowserDispatcher, Server,
)

#: How long this engine takes to answer `Browser.newPage`, in its own clock.
ANSWER_AFTER_S = 35.0


class _SlowEngine(EventListeners):
    """Answers `Browser.newPage` after ANSWER_AFTER_S, attaching the target
    first as Juggler does (`TargetRegistry.newPage` waits for it to exist)."""

    def __init__(self):
        super().__init__()
        self.browser = None
        self.bounds = []

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout=30.0):
        if method != "Browser.newPage":
            return {}
        self.bounds.append(timeout)
        if timeout is not None and timeout < ANSWER_AFTER_S:
            raise ProtocolError("%s: no response in %.0fs" % (method, timeout))
        self.browser._route_browser_event(
            "Browser.attachedToTarget",
            {"sessionId": "S-T0", "targetInfo": {
                "targetId": "T0", "type": "page", "browserContextId": "C1"}},
            None)
        return {"targetId": "T0"}


class _Page:
    def __init__(self, server, context, session, target_id, number, opener=None):
        self.target_id = target_id
        self.number = number
        self.channel = {"guid": "page-" + target_id}

    def announce_closed(self):
        pass


@pytest.mark.unit
def test_a_page_the_engine_answers_after_35_s_is_still_a_page(monkeypatch):
    monkeypatch.setattr(srv, "PageDispatcher", _Page)
    engine = _SlowEngine()
    browser = BrowserDispatcher(Server(), None, engine, "151.0", session_seed=1)
    engine.browser = browser
    context = BrowserContextDispatcher(browser.server, browser, {}, "C1")
    browser.contexts.append(context)

    result = context.op_new_page({"pageNumber": 0})

    assert result == {"page": {"guid": "page-T0"}}
    assert engine.bounds == [None], (
        "the reply to Browser.newPage was given a bound of its own: %r"
        % engine.bounds)
