"""A frame that left the page answers a call with "Frame was detached", as Playwright's driver does.

⛔ THE KNOWN-BAD INPUT: an iframe removed from the DOM (in the same process or in another, with a process per site).
The lifecycle retires it and the server disposed the frame's dispatcher on the spot, so the next call on the Frame
object named a guid that no longer existed and was answered "Target page, context or browser has been closed". The
page was not closed. Measured on the engine with the Fission work, in both places: `frame.frame_element()`,
`frame.evaluate(...)`, `frame.content()` and `frame.query_selector(...)` on a removed iframe, every one. The driver
never disposes a Frame at detach: the call reaches the dispatcher, which refuses "Frame was detached" (the page and its
other frames keep working, and a script that handles that error finds it).
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle
from invisible_playwright._juggler.dispatcher import Dispatcher, ProtocolException, TargetClosedError
from invisible_playwright._juggler.server import FrameDispatcher, JugglerServer, PageDispatcher

pytestmark = pytest.mark.unit


@pytest.fixture
def page():
    server = JugglerServer()
    messages = []
    server.attach(SimpleNamespace(emit_message=messages.append))
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
    for fid, parent in (("main", None), ("payment", "main"), ("widget", "payment")):
        page.lifecycle._on_event("Page.frameAttached", {"frameId": fid, "parentFrameId": parent})
    page.messages = messages
    page.lifecycle.announce = page._on_lifecycle
    return page


def _call(page, frame, method, params=None):
    return page.server.handle({"guid": frame.guid, "method": method, "params": params or {}})


def _detach(page, frame_id):
    page.lifecycle._on_event("Page.frameDetached", {"frameId": frame_id})


@pytest.mark.parametrize("method", ["frameElement", "content", "title", "evaluateExpression", "querySelector"])
def test_a_call_on_a_detached_frame_says_it_was_detached_not_that_the_target_closed(page, method):
    payment = page.frame_for("payment")
    _detach(page, "payment")
    with pytest.raises(ProtocolException, match="detached") as raised:
        _call(page, payment, method, {"selector": "#h", "expression": "1"})
    assert not isinstance(raised.value, TargetClosedError)


def test_the_frame_is_told_detached_and_not_disposed(page):
    payment = page.frame_for("payment")
    page.messages.clear()
    _detach(page, "payment")
    mine = [m for m in page.messages if m.get("guid") == payment.guid or
            (m.get("params") or {}).get("frame", {}).get("guid") == payment.guid]
    assert [m["method"] for m in mine] == ["frameDetached"], mine
    assert not payment.disposed and payment.guid in page.server._objects
    assert page._frames.get("payment") is None


def test_the_handles_of_a_detached_frame_go_with_it(page):
    from invisible_playwright._juggler.server import ElementHandleDispatcher
    payment = page.frame_for("payment")
    handle = ElementHandleDispatcher(page.server, payment, "object-1")
    _detach(page, "payment")
    assert handle.disposed and handle.guid not in page.server._objects


def test_the_other_frames_and_the_page_keep_answering(page):
    page.frame_for("payment")
    _detach(page, "payment")
    with pytest.raises(ProtocolException) as raised:
        _call(page, page.frame, "no-such-method")
    assert "detached" not in str(raised.value)      # the main frame is not the detached one


def test_a_detached_frame_leaves_with_its_page(page):
    payment = page.frame_for("payment")
    _detach(page, "payment")
    page.dispose()
    assert payment.guid not in page.server._objects and payment.disposed


def test_a_frame_that_comes_back_with_the_same_id_is_a_new_one(page):
    first = page.frame_for("payment")
    _detach(page, "payment")
    page.lifecycle._on_event("Page.frameAttached", {"frameId": "payment", "parentFrameId": "main"})
    again = page.frame_for("payment")
    assert again is not first and not again.detached
    with pytest.raises(ProtocolException, match="detached"):
        _call(page, first, "content")
