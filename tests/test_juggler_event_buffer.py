"""Events that arrive before their reader exists must not be dropped.

⛔ NO BROWSER HERE, and that is the point. The defect this file guards is a
RACE, and a race reproduces on demand by feeding events in the losing order -
which with a real browser happens "under load, on somebody else's machine, once
in twenty runs", and happened every single time only once an unrelated command
shifted the timing.

**The shape of the bug, measured 2026-08-28 at the protocol level.** The
browser sends `Page.frameAttached` and both `Runtime.executionContextCreated`
at 0.65 s, and answers `Browser.newPage` at 0.70 s. That reply is what tells
the server which session to build a `PageDispatcher` for, so the lifecycle -
which learns the main frame ONLY from `frameAttached` - and the injected script
- which learns its worlds ONLY from `executionContextCreated` - are both
registered after their own events have gone past. The visible failure is
`no main frame in 20s: the page never announced one. Frames seen: []`, which
names the page and blames the browser, while the browser did its job in 0.7 s.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.server import BrowserDispatcher, Server
from invisible_core.juggler.connection import EventListeners


class FakeConnection(EventListeners):
    """The minimum `BrowserDispatcher` touches: the registry and a `send`."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params, session))
        return {}


def browser():
    server = Server()
    return FakeConnection(), server


def make(conn, server):
    return BrowserDispatcher(server, None, conn, "151.0")


@pytest.mark.unit
def test_events_of_an_unread_session_are_held_and_replayed_in_order():
    conn, server = browser()
    b = make(conn, server)

    conn.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    conn.dispatch_event("Runtime.executionContextCreated", {"id": "C1"}, "S1")

    got = []
    replayed = b.replay("S1", lambda m, p, s: got.append((m, s)))
    assert replayed == 2
    assert got == [("Page.frameAttached", "S1"),
                   ("Runtime.executionContextCreated", "S1")]


@pytest.mark.unit
def test_a_session_with_a_reader_is_not_buffered_any_more():
    """Otherwise the buffer is a leak, and a second replay delivers twice."""
    conn, server = browser()
    b = make(conn, server)
    b.replay("S1", lambda m, p, s: None)

    conn.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    assert b.replay("S1", lambda m, p, s: None) == 0


@pytest.mark.unit
def test_sessions_do_not_borrow_each_other_events():
    """Two pages open at once is the ordinary case, not the exotic one."""
    conn, server = browser()
    b = make(conn, server)
    conn.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    conn.dispatch_event("Page.frameAttached", {"frameId": "F2"}, "S2")

    got = []
    b.replay("S2", lambda m, p, s: got.append(p["frameId"]))
    assert got == ["F2"]


@pytest.mark.unit
def test_a_browser_level_event_is_not_buffered():
    """`Browser.attachedToTarget` carries no session: buffering it under a
    key of `None` would build a list nobody can ever ask for."""
    conn, server = browser()
    b = make(conn, server)
    conn.dispatch_event("Browser.attachedToTarget",
                  {"targetInfo": {"targetId": "T1"}, "sessionId": "S1"}, None)
    assert b.replay("S1", lambda m, p, s: None) == 0


@pytest.mark.unit
def test_the_buffer_is_capped():
    """A page that never gets a dispatcher must not grow without bound.

    ⛔ The cap is a leak guard, not a correctness knob: it is far above the
    burst that precedes a `newPage` reply (three events, measured) and far
    below anything that would matter as memory.
    """
    conn, server = browser()
    b = make(conn, server)
    for i in range(b.BUFFER_CAP + 50):
        conn.dispatch_event("Page.eventFired", {"n": i}, "S1")
    assert b.replay("S1", lambda m, p, s: None) == b.BUFFER_CAP


@pytest.mark.unit
def test_forget_releases_a_session_nobody_will_read():
    conn, server = browser()
    b = make(conn, server)
    conn.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    b.forget("S1")
    assert b.replay("S1", lambda m, p, s: None) == 0


@pytest.mark.unit
def test_the_original_route_still_runs():
    """The buffer is one subscriber among others; it must not starve them.

    A version that returned early after buffering would starve every live
    consumer - and every test above would still pass, because they all read
    the buffer rather than the other subscribers.
    """
    conn, server = browser()
    seen = []
    conn.add_listener(lambda m, p, s: seen.append(m))
    b = make(conn, server)
    conn.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    assert "Page.frameAttached" in seen
