"""`page.request_gc()` (Playwright's own API) reaches the engine, which answers it.

⛔ THE KNOWN-BAD INPUT, measured on a real engine: `Page.requestGC` was answered by `op_request_gc` with
`Heap.collectGarbage` sent on the BROWSER connection, and Juggler's browser-level handler does not have it:
`Page.request_gc: Heap.collectGarbage: Handler for does not implement method "Heap.collectGarbage"`, on every
call. The command is a page command (`PageHandler`), so it goes with the session of the page.

No browser: the page dispatcher is built the way tests/test_navigation_*.py build one, with the two senders
replaced by mocks.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle
from invisible_playwright._juggler.dispatcher import Dispatcher
from invisible_playwright._juggler.server import (
    FrameDispatcher,
    JugglerServer,
    PageDispatcher,
)

pytestmark = pytest.mark.unit


def make_page(server):
    server.attach(SimpleNamespace(emit_message=lambda m: None))
    context = Dispatcher(server, None)
    context.options = {}
    page = object.__new__(PageDispatcher)
    page.context = context
    page.lifecycle = Lifecycle(EventListeners(), "session")
    page.injected = Mock()
    page.actions = Mock()
    page._frames = {}
    page._navigation_requests = {}
    page._announced_closed = False
    Dispatcher.__init__(page, server, context)
    page.frame = FrameDispatcher(server, page, "main")
    for fid, parent in (("main", None), ("sub", "main")):
        page.lifecycle._on_event("Page.frameAttached", {"frameId": fid, "parentFrameId": parent})
    return page


def test_page_request_gc_is_sent_with_the_page_session_not_the_browser_connection():
    page = make_page(JugglerServer())
    page.send = Mock()
    page.context.browser = SimpleNamespace(conn=Mock())
    assert page.op_request_gc({}) is None
    page.send.assert_called_once_with("Heap.collectGarbage", {}, timeout=None)
    page.context.browser.conn.send.assert_not_called()


def test_the_protocol_name_it_writes_is_the_one_the_engine_declares():
    from invisible_core.juggler.protocol import COMMANDS

    assert "Heap.collectGarbage" in COMMANDS
