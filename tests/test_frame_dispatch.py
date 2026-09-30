"""Frame identity must travel with every selector and handle."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from invisible_playwright._juggler.dispatcher import Dispatcher, ProtocolException
from invisible_playwright._juggler.lifecycle import Lifecycle
from invisible_playwright._juggler.server import (
    FrameDispatcher,
    JugglerServer,
    PageDispatcher,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def page():
    server = JugglerServer()
    messages = []
    server.attach(SimpleNamespace(emit_message=messages.append))
    context = Dispatcher(server, None)
    page = object.__new__(PageDispatcher)
    page.context = context
    page.lifecycle = Lifecycle(SimpleNamespace(add_listener=lambda listener: None), "session")
    page.injected = Mock()
    page.actions = Mock()
    page._frames = {}
    page._navigation_requests = {}
    Dispatcher.__init__(page, server, context)
    page.frame = FrameDispatcher(server, page, "main")
    for fid, parent in (("main", None), ("payment", "main"), ("widget", "payment")):
        page.lifecycle._on_event("Page.frameAttached", {"frameId": fid, "parentFrameId": parent})
    page.messages = messages
    return page


def test_frame_tree_is_announced_parent_first(page):
    widget = page.frame_for("widget")
    payment = page.frame_for("payment")
    assert widget.parent_frame is payment
    assert payment.parent_frame is page.frame
    assert widget.initializer["parentFrame"] == payment.channel
    assert payment.initializer["parentFrame"] == page.frame.channel
    assert widget.parent is page
    assert page.frame_for("widget") is widget
    created = [m["params"]["guid"] for m in page.messages if m["method"] == "__create__"]
    assert created.index(payment.guid) < created.index(widget.guid)


@pytest.mark.parametrize("crossing", [False, True])
def test_wait_for_selector_returns_a_handle_owned_by_the_resolved_frame(page, crossing):
    widget = page.frame_for("widget")
    frame = page.frame if crossing else widget
    frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.actions.wait_for_selector.return_value = "node"
    result = frame.op_wait_for_selector({"selector": "compound", "state": "attached",
                                         "strict": True, "timeout": 100})
    page.actions.wait_for_selector.assert_called_once_with(
        "#target", state="attached", frame_id="widget", strict=True, timeout=0.1)
    handle = page.server._objects[result["element"]["guid"]]
    assert handle.frame is widget
    assert handle.parent is widget


@pytest.mark.parametrize("method,params,read,args", [
    ("op_text_content", {}, "text_content", ()),
    ("op_inner_text", {}, "inner_text", ()),
    ("op_inner_html", {}, "inner_html", ()),
    ("op_input_value", {}, "input_value", ()),
    ("op_get_attribute", {"name": "id"}, "get_attribute", ("id",)),
    ("op_is_checked", {}, "element_state", ("checked",)),
])
def test_frame_crossing_reads_use_the_handle_world(page, method, params, read, args):
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.query_selector.return_value = "node"
    getattr(page.injected, read).return_value = "answer"
    assert getattr(page.frame, method)({"selector": "compound", **params}) == {"value": "answer"}
    getattr(page.injected, read).assert_called_once_with("widget", "node", *args)
    page.injected.dispose.assert_called_once_with("widget", "node")


def test_failed_read_disposes_in_the_resolved_frame(page):
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.query_selector.return_value = "node"
    page.injected.text_content.side_effect = RuntimeError("document gone")
    with pytest.raises(RuntimeError, match="document gone"):
        page.frame.op_text_content({"selector": "compound"})
    page.injected.dispose.assert_called_once_with("widget", "node")


def test_frame_element_adopts_into_parent_world(page):
    widget = page.frame_for("widget")
    page.injected.context_id.return_value = "payment-utility"
    page.send = Mock(return_value={"remoteObject": {"objectId": "owner"}})
    result = widget.op_frame_element({})
    page.injected.context_id.assert_called_once_with("payment")
    page.send.assert_called_once_with("Page.adoptNode", {
        "frameId": "widget", "executionContextId": "payment-utility",
    })
    handle = page.server._objects[result["element"]["guid"]]
    assert handle.frame is page.frame_for("payment")
    assert handle.object_id == "owner"


def test_frame_element_refuses_main_or_detached_frame(page):
    with pytest.raises(ProtocolException, match="no parent"):
        page.frame.op_frame_element({})
    page.send = Mock(return_value={"remoteObject": None})
    with pytest.raises(ProtocolException, match="detached"):
        page.frame_for("widget").op_frame_element({})


def test_resolve_selector_returns_child_channel_and_tail(page):
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    assert page.frame.op_resolve_selector({"selector": "compound"}) == {
        "frame": page.frame_for("widget").channel, "selector": "#target",
    }


def test_navigation_updates_url_and_clears_old_load_states(page):
    page._on_juggler_event("Page.navigationCommitted", {
        "frameId": "widget", "url": "http://localhost/new", "name": "widget",
    })
    widget = page.frame_for("widget")
    assert widget.url == "http://localhost/new"
    assert widget.name == "widget"
    events = [m for m in page.messages if m["guid"] == widget.guid]
    assert events[-2]["method"] == "navigated"
    assert events[-1]["params"] == {"add": "commit"}
    assert {m["params"]["remove"] for m in events[:-2]} == {
        "commit", "domcontentloaded", "load", "networkidle",
    }
    page.messages.clear()
    page._on_juggler_event("Page.sameDocumentNavigation", {
        "frameId": "widget", "url": "http://localhost/new#hash",
    })
    assert widget.url.endswith("#hash")
    assert page.messages == [{"guid": widget.guid, "method": "navigated",
                             "params": {"url": widget.url, "name": "widget"}}]


def test_goto_does_not_overwrite_a_later_navigation_with_its_snapshot(page):
    def goto(*args, **kwargs):
        page._on_juggler_event("Page.navigationCommitted", {
            "frameId": "main", "url": "http://localhost/latest", "name": "",
        })
        return {"navigationId": "earlier", "url": "http://localhost/earlier"}

    page.lifecycle.goto = goto
    page.frame.op_goto({"url": "http://localhost/earlier"})
    navigations = [m["params"]["url"] for m in page.messages if m["method"] == "navigated"]
    assert navigations == ["http://localhost/latest"]


@pytest.mark.parametrize("has_request", [False, True])
def test_committed_navigation_request_is_a_channel_or_omitted(page, has_request):
    if has_request:
        page._navigation_requests["NAV"] = SimpleNamespace(channel={"guid": "request"})
    page._on_juggler_event("Page.navigationCommitted", {
        "frameId": "widget", "navigationId": "NAV", "url": "http://localhost/new",
    })
    events = [m for m in page.messages if m["method"] == "navigated"]
    expected = {"request": {"guid": "request"}} if has_request else {}
    assert [m["params"]["newDocument"] for m in events] == [expected]


def test_cursor_hit_test_uses_the_handle_frame_and_injected_checker(page):
    from invisible_playwright._juggler.server import ElementHandleDispatcher

    handle = ElementHandleDispatcher(page.server, page.frame_for("widget"), "input")
    page.injected.call.return_value = True
    point = {"x": 0.25, "y": 0.75}
    assert handle.call("checkHitTarget", {"point": point}) == {"value": True}
    args = page.injected.call.call_args.args
    assert args[0] == "widget"
    assert "injected.checkHitTarget" in args[1]
    assert args[2:] == ({"objectId": "input"}, point)
