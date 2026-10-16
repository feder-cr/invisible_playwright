"""A closed page's session events reach nothing, and a close waits for the
event being handled.

The client threw `'dict' object has no attribute '_object'` from inside
`BrowserContext._on_response` when pages were opened and closed quickly: 3
runs out of 4 of 150 `new_page`/`goto`/`close` cycles, measured 2026-10-15.
The page's close (`announce_closed`, on the caller's thread or the engine's
detach) and the delivery of its session's events (`_route_juggler_event`, on
the reader thread) were not serialized, so the reader could be inside the
page's handler, or enter it, after the client had been sent `close` and
`__dispose__`: a `Network.responseReceived` then created a Response under a
request the client had already dropped with its page, and the context's
`response` event named an object the client never built. The guid stayed a
dict, and `from_channel` read `_object` off it.

One gate now: every event of the page goes through `_deliver`, under the
close's own lock, and does nothing once the page is closed.
"""
from __future__ import annotations

import threading
import types

import pytest

from invisible_playwright._juggler.server import PageDispatcher

pytestmark = pytest.mark.unit


class _FakeServer:
    def __init__(self):
        self.sent = []
        self._lock = threading.Lock()

    def send_up(self, message):
        with self._lock:
            self.sent.append(message)

    def unregister(self, _obj):
        pass

    def descendants_of(self, _obj):
        return []


def _page(handled):
    """A PageDispatcher with its close path and its event entry points wired,
    and a handler that records what reached it."""
    page = PageDispatcher.__new__(PageDispatcher)
    page.server = _FakeServer()
    page.guid = "page@test"
    page.parent = None
    page.session = "S1"
    page._close_lock = threading.RLock()
    page._announced_closed = False
    page.disposed = False
    page._held = None
    page._held_lock = threading.Lock()
    page._detach_listeners = lambda: None
    page._hear_lifecycle = lambda: None
    page.context = types.SimpleNamespace(browser=types.SimpleNamespace(
        _page_over=lambda _page: None))
    page._on_juggler_event = lambda method, params: handled.append(method)
    return page


RESPONSE = ("Network.responseReceived", {"requestId": "R1"})


def test_an_event_after_the_close_reaches_nothing():
    handled = []
    page = _page(handled)
    page.announce_closed()
    page._route_juggler_event(*RESPONSE, "S1")
    assert handled == [], (
        "an event of a closed page reached its handler: a Response is created "
        "under a request the client dropped with the page")


def test_a_held_event_released_after_the_close_reaches_nothing():
    handled = []
    page = _page(handled)
    page._held = [RESPONSE]
    page.announce_closed()
    page._install_events()
    assert handled == []


def test_the_close_waits_for_the_event_being_handled():
    """The reader is inside the page's handler when the close comes: the close
    must not reach the client before what that handler sends."""
    handled = []
    page = _page(handled)
    inside, release = threading.Event(), threading.Event()

    def slow(method, params):
        inside.set()
        release.wait(5)
        page.server.send_up({"guid": "context@1", "method": "response"})

    page._on_juggler_event = slow
    reader = threading.Thread(target=page._route_juggler_event, args=(*RESPONSE, "S1"))
    reader.start()
    assert inside.wait(5)
    closer = threading.Thread(target=page.announce_closed)
    closer.start()
    closer.join(0.3)
    early = [m.get("method") for m in page.server.sent]
    release.set()
    reader.join(5)
    closer.join(5)
    order = [m.get("method") for m in page.server.sent]
    assert "close" not in early, (
        "the close was announced while the page was still handling an event")
    assert order.index("response") < order.index("close"), order


def test_a_handler_that_ends_its_own_page_does_not_deadlock():
    """An event whose handling closes the page, on the same thread: the gate's
    lock is the close's, so it has to be re-entrant."""
    handled = []
    page = _page(handled)
    page._on_juggler_event = lambda method, params: page.announce_closed()
    done = threading.Thread(target=page._route_juggler_event, args=(*RESPONSE, "S1"))
    done.start()
    done.join(5)
    assert not done.is_alive(), "the page deadlocked closing itself from its handler"
    assert [m.get("method") for m in page.server.sent].count("close") == 1
