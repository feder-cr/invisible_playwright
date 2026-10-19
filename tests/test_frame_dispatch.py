"""Frame identity must travel with every selector and handle."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from invisible_playwright._juggler.dispatcher import Dispatcher, ProtocolException
from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle
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
    context.options = {}    # created without a base URL
    page = object.__new__(PageDispatcher)
    page.context = context
    # The connection's own registry, as every stand-in for it inherits
    # (`EventListeners`): it carries the event count the lifecycle reads.
    page.lifecycle = Lifecycle(EventListeners(), "session")
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


@pytest.mark.parametrize("method,params,what,args", [
    ("op_text_content", {}, "textContent", ()),
    ("op_inner_text", {}, "innerText", ()),
    ("op_inner_html", {}, "innerHTML", ()),
    ("op_input_value", {}, "inputValue", ()),
    ("op_get_attribute", {"name": "id"}, "attribute", ("id",)),
    ("op_is_checked", {}, "state", ("checked",)),
])
def test_frame_crossing_reads_run_in_the_resolved_frame(page, method, params, what, args):
    """The read is made in the frame the compound selector resolved to, with
    the selector that is left. Since 0.34.1 the element is found and read in
    one command (`read_match`), so no handle crosses and none is disposed."""
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.read_match.return_value = (True, "answer")
    assert getattr(page.frame, method)(
        {"selector": "compound", "strict": True, "timeout": 100, **params}
    ) == {"value": "answer"}
    call = page.injected.read_match.call_args
    assert call.args == ("widget", "#target", what, *args)
    assert call.kwargs["strict"] is True


def test_a_failed_read_reaches_the_caller_as_it_is(page):
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.read_match.side_effect = RuntimeError("document gone")
    with pytest.raises(RuntimeError, match="document gone"):
        page.frame.op_text_content({"selector": "compound"})


def test_a_read_waits_for_a_match_within_the_callers_timeout(page):
    """Playwright's reads wait for the element; until 0.34.1 a missing one
    failed at once with "no element matches"."""
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.read_match.side_effect = [(False, None), (False, None),
                                            (True, "late")]
    assert page.frame.op_inner_text({"selector": "compound",
                                     "timeout": 5000}) == {"value": "late"}
    page.injected.read_match.side_effect = None
    page.injected.read_match.return_value = (False, None)
    with pytest.raises(TimeoutError, match="Timeout 200ms exceeded"):
        page.frame.op_inner_text({"selector": "compound", "timeout": 200})


@pytest.mark.parametrize("method,missing", [("op_is_visible", False),
                                            ("op_is_hidden", True)])
def test_visibility_answers_now_and_a_missing_element_is_hidden(page, method, missing):
    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.read_match.return_value = (False, None)
    assert getattr(page.frame, method)({"selector": "compound"}) == {"value": missing}
    assert page.injected.read_match.call_count == 1


def test_frame_element_adopts_into_parent_world(page):
    widget = page.frame_for("widget")
    page.injected.adopt.return_value = "owner"
    result = widget.op_frame_element({})
    page.injected.adopt.assert_called_once_with("widget", into="payment")
    handle = page.server._objects[result["element"]["guid"]]
    assert handle.frame is page.frame_for("payment")
    assert handle.object_id == "owner"


def test_frame_element_refuses_main_or_detached_frame(page):
    with pytest.raises(ProtocolException, match="no parent"):
        page.frame.op_frame_element({})
    page.injected.adopt.return_value = None
    with pytest.raises(ProtocolException, match="detached"):
        page.frame_for("widget").op_frame_element({})


@pytest.mark.parametrize("element,into,expected", [
    # The file chooser's input: a node, moved into its own frame's world.
    ("node", None, {"frameId": "widget", "executionContextId": "widget-utility",
                    "objectId": "node"}),
    # frame_element(): no node, so the engine adopts the frame's owner, and
    # it has to land in the PARENT's world, where that element lives.
    (None, "payment", {"frameId": "widget",
                       "executionContextId": "payment-utility"}),
])
def test_adopt_is_one_wire_call_for_both_crossings(element, into, expected):
    from invisible_core.juggler.injected import UTILITY_WORLD, InjectedScript

    sent = []

    class Wire:
        def send(self, method, params, **kwargs):
            sent.append((method, params))
            return {"remoteObject": {"objectId": "adopted"}}

    injected = object.__new__(InjectedScript)
    injected.c = Wire()
    injected.session = "session"
    injected.contexts = {("widget", UTILITY_WORLD): "widget-utility",
                         ("payment", UTILITY_WORLD): "payment-utility"}
    assert injected.adopt("widget", element, into=into) == "adopted"
    assert sent == [("Page.adoptNode", expected)]


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
    page._hear_lifecycle()
    page.lifecycle._on_event("Page.sameDocumentNavigation", {
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


@pytest.mark.parametrize("verdict,value", [("done", True),
                                           ("<div#plus> intercepts", False)])
def test_cursor_hit_test_is_the_action_s_own(page, verdict, value):
    """The cursor's landing check is `Actions.hit_target`, with the handle's
    frame and the main-frame point untouched: the frame shift and the
    shadow-aware test happen there, once, for the action and the cursor."""
    from invisible_playwright._juggler.server import ElementHandleDispatcher

    handle = ElementHandleDispatcher(page.server, page.frame_for("widget"), "input")
    page.actions.hit_target.return_value = verdict
    answer = handle.call("checkHitTarget", {"point": {"x": 410, "y": 312.5}})
    assert answer == {"value": value}
    page.actions.hit_target.assert_called_once_with("widget", "input", (410.0, 312.5))


#: The three element-scoped evaluations and the receiver each one hands to the
#: caller's function before the argument.
ELEMENT_CALLBACKS = [
    ("handle.evaluate", "el"),
    ("evalOnSelector", "el"),
    ("evalOnSelectorAll", "els"),
]


@pytest.mark.parametrize("method,receiver", ELEMENT_CALLBACKS,
                         ids=[m for m, _ in ELEMENT_CALLBACKS])
def test_every_element_callback_receives_the_caller_s_argument(page, method, receiver):
    """Playwright's contract is `fn(element, arg)` on all three. The argument
    used to reach `handle.evaluate` only: `eval_on_selector` and
    `eval_on_selector_all` called `r(el)` / `r(els)`, so a callback reading its
    second parameter got `undefined` and threw inside the page.

    Known-bad: drop the argument from any of the three scripts, or build one of
    them by hand again instead of through the shared builder.
    """
    from invisible_playwright._juggler.server import ElementHandleDispatcher

    page.frame.enter_frames = Mock(return_value=("widget", "#target"))
    page.injected.query_selector.return_value = "node"
    page.injected.query_selector_all.return_value = ["node"]
    page.injected.caller_context.return_value = "caller-ctx"
    page.injected.node_to_caller.return_value = "copy"
    # The caller's text is evaluated as a script, and the function it makes is
    # called with the receivers and the argument as protocol values ([B340]).
    page.injected.evaluate_raw.return_value = {"type": "function", "objectId": "fn"}
    from invisible_playwright._juggler.server import _ARRAY, _CALL
    page.injected.call_raw.side_effect = lambda context, script, args, **k: (
        {"objectId": "array"} if script == _ARRAY else {"value": 6})
    params = {"selector": "compound", "expression": "(x, a) => a",
              "arg": {"value": {"o": [{"k": "n", "v": {"n": 2}}]}, "handles": []}}
    if method == "handle.evaluate":
        ElementHandleDispatcher(page.server, page.frame_for("widget"),
                                "node").call("evaluateExpression", params)
    else:
        page.frame.call(method, params)
    # the caller's code, in the caller's world, on the node carried there [B322]
    calls = [c.args[:3] for c in page.injected.call_raw.call_args_list]
    [(context, _, args)] = [c for c in calls if c[1] == _CALL]
    assert context == "caller-ctx"
    held = {"objectId": "array" if receiver == "els" else "copy"}
    assert args == [{"objectId": "fn"}, held, {"value": {"n": 2}}], args
    if receiver == "els":
        [(_, _, items)] = [c for c in calls if c[1] == _ARRAY]
        assert items == [{"objectId": "copy"}], items


def test_networkidle_reaches_the_client_as_a_load_state(page):
    """The server's half: the lifecycle's networkidle goes up as
    `loadstate {"add": "networkidle"}` on the frame, which is the event the
    client's `wait_for_load_state`, `wait_for_url` and `expect_navigation`
    wait for. It was never emitted.

    Known-bad: drop the announcement, or emit it from a second reading of the
    network instead of from the lifecycle.
    """
    import time

    from invisible_core.juggler.lifecycle import IDLE_QUIET

    page._hear_lifecycle()
    page.lifecycle._on_event("Page.eventFired", {"frameId": "main", "name": "load"})
    time.sleep(IDLE_QUIET * 3)
    added = [m["params"] for m in page.messages
             if m["guid"] == page.frame.guid and m["method"] == "loadstate"]
    assert {"add": "networkidle"} in added, added


def _events(page, method):
    return [m for m in page.messages if m.get("method") == method]


def test_frames_reach_the_client_from_the_lifecycle_alone(page):
    """The main frame re-announced by a cross-process navigation is not a new
    frame, and a new document retires the old one's frames. Until 0.34.1 the
    page answered the engine's raw events itself: `page.frames` grew by a
    stale iframe and a repeated main frame at every such navigation."""
    page.frame_for("widget")
    page.lifecycle.announce = page._on_lifecycle
    on = page.lifecycle._on_event
    on("Page.frameAttached", {"frameId": "main"})
    assert _events(page, "frameAttached") == []
    on("Page.navigationCommitted", {"frameId": "main", "navigationId": "n2",
                                    "url": "http://b/"})
    detached = [m["params"]["frame"]["guid"] for m in _events(page, "frameDetached")]
    assert len(detached) == 2 and page._frames == {}
    on("Page.frameAttached", {"frameId": "fresh", "parentFrameId": "main"})
    attached = _events(page, "frameAttached")
    assert len(attached) == 1
    assert attached[0]["params"]["frame"]["guid"] == page._frames["fresh"].guid


def _reading(matches, received=None, **more):
    """One reading as the injected script's `expect` answers it."""
    out = {"matches": matches, "missingReceived": False}
    if received is not None:
        out["received"] = received
    out.update(more)
    return out


def test_expect_polls_until_the_condition_holds(page):
    # The locator the wait names, from the injected script's `asLocator` ([B341]).
    page.injected.call.return_value = 'locator("x")'
    page.frame._expect_probe = Mock(side_effect=[_reading(False, 0), _reading(False, 1),
                                                 _reading(True, 2)])
    assert page.frame.op_expect({"expression": "to.have.count", "selector": "x",
                                 "timeout": 5000}) is None
    assert page.frame._expect_probe.call_count == 3


def test_expect_not_inverts_and_a_condition_that_never_holds_carries_details(page):
    from invisible_playwright._juggler.dispatcher import ExpectNotMet

    # The locator the wait names, from the injected script's `asLocator` ([B341]).
    page.injected.call.return_value = 'locator("x")'
    page.frame._expect_probe = Mock(return_value=_reading(True, "checked"))
    with pytest.raises(ExpectNotMet) as caught:
        page.frame.op_expect({"expression": "to.be.checked", "selector": "x",
                              "isNot": True, "timeout": 300})
    assert caught.value.details == {"received": {"value": {"s": "checked"}},
                                    "timedOut": True}
    assert page.frame.op_expect({"expression": "to.be.checked", "selector": "x",
                                 "isNot": False, "timeout": 300}) is None


def test_a_missing_element_says_so_as_playwright_does(page):
    """`element(s) not found` is the message upstream's server writes when the
    reading says nothing matched, and the aria snapshot of the page travels
    with the last value, for the error the client renders."""
    from invisible_playwright._juggler.dispatcher import ExpectNotMet

    # The locator the wait names, from the injected script's `asLocator` ([B341]).
    page.injected.call.return_value = 'locator("x")'
    page.frame._expect_probe = Mock(return_value={
        "matches": False, "missingReceived": True, "ariaSnapshot": "- main"})
    with pytest.raises(ExpectNotMet) as caught:
        page.frame.op_expect({"expression": "to.have.text", "selector": "#nope",
                              "timeout": 200})
    assert caught.value.details == {"received": {"ariaSnapshot": "- main"},
                                    "customErrorMessage": "element(s) not found",
                                    "timedOut": True}


def test_a_javascript_error_in_the_reading_ends_the_wait_with_its_message(page):
    """A strict-mode violation cannot start to hold later: upstream raises it at
    once as the message, and so does this, without spending the timeout."""
    import time as _time

    from invisible_core.juggler.injected import EvaluationError
    from invisible_playwright._juggler.dispatcher import ExpectNotMet

    # The locator the wait names, from the injected script's `asLocator` ([B341]).
    page.injected.call.return_value = 'locator("x")'
    page.frame._expect_probe = Mock(side_effect=EvaluationError(
        "Error: strict mode violation: locator('button') resolved to 2 elements\n"
        "    at strictModeViolationError"))
    started = _time.monotonic()
    with pytest.raises(ExpectNotMet) as caught:
        page.frame.op_expect({"expression": "to.have.text", "selector": "button",
                              "timeout": 5000})
    assert _time.monotonic() - started < 2
    assert caught.value.details == {
        "customErrorMessage": "strict mode violation: locator('button') resolved to 2 elements"}
    assert page.frame._expect_probe.call_count == 1


def test_a_navigation_under_the_reading_is_read_again(page):
    from invisible_core.juggler.injected import ContextDestroyed

    # The locator the wait names, from the injected script's `asLocator` ([B341]).
    page.injected.call.return_value = 'locator("x")'
    page.frame._expect_probe = Mock(side_effect=[ContextDestroyed("gone"), _reading(True, "x")])
    assert page.frame.op_expect({"expression": "to.have.text", "selector": "p",
                                 "timeout": 5000}) is None
    assert page.frame._expect_probe.call_count == 2


def test_the_reading_is_the_injected_scripts_expect_with_playwrights_options(page):
    """The options the caller sent, as upstream hands them over: `expectedValue`
    parsed, everything else as it came, and the page-level conditions with no
    selector at all."""
    calls = []

    def call(frame_id, declaration, sel, options):
        calls.append((frame_id, declaration, sel, options))
        return {"matches": True, "missingReceived": False}

    page.injected = Mock()
    page.injected.call = call
    page.frame.enter_frames = lambda selector: ("main", selector)
    page.frame._expect_probe("to.have.property", "#x", {
        "expression": "to.have.property", "expressionArg": "a.b", "selector": "#x",
        "expectedValue": {"value": {"n": 3}, "handles": []}, "isNot": False,
        "timeout": 1000})
    page.frame._expect_probe("to.have.title", None, {
        "expression": "to.have.title", "isNot": False,
        "expectedText": [{"string": "T", "matchSubstring": False}]})
    (_, declaration, sel, options), (_, _, sel2, options2) = calls
    assert "injected.expect(element, options, elements)" in declaration
    assert "strictModeViolationError" in declaration
    assert sel == "#x" and options == {
        "expression": "to.have.property", "expressionArg": "a.b", "selector": "#x",
        "isNot": False, "expectedValue": 3}
    assert sel2 is None and options2["expectedText"] == [{"string": "T", "matchSubstring": False}]


@pytest.mark.parametrize("value", [
    {"o": [{"k": "a", "v": {"n": 1}}], "id": 1},
    {"a": [{"n": 1}], "id": 1},
    {"d": "2021-07-28T20:26:32.000Z"},
    {"r": {"p": "x", "f": ""}},
])
def test_a_structured_js_property_is_refused_rather_than_called_different(page, value):
    """Across realms Playwright's `deepEquals` calls two equal objects different;
    the refusal says so instead of failing an assertion that holds."""
    from invisible_playwright._juggler.dispatcher import ProtocolException

    page.injected = Mock()
    with pytest.raises(ProtocolException, match="another realm"):
        page.frame._expect_probe("to.have.property", "div", {
            "expression": "to.have.property", "expressionArg": "foo",
            "expectedValue": {"value": value, "handles": []}})
    assert not page.injected.call.called


@pytest.mark.parametrize("value", [{"n": 3}, {"s": "x"}, {"b": True}, {"v": "null"}])
def test_a_primitive_js_property_is_compared(page, value):
    page.injected = Mock()
    page.injected.call = Mock(return_value={"matches": True, "missingReceived": False})
    page.frame.enter_frames = lambda selector: ("main", selector)
    assert page.frame._expect_probe("to.have.property", "div", {
        "expression": "to.have.property", "expressionArg": "foo",
        "expectedValue": {"value": value, "handles": []}})["matches"] is True


def test_to_match_aria_is_refused_by_name(page):
    from invisible_playwright._juggler.dispatcher import ProtocolException

    with pytest.raises(ProtocolException, match="to.match.aria"):
        page.frame._expect_probe("to.match.aria", "main", {"expression": "to.match.aria"})


def test_an_error_with_details_sends_them_where_the_client_reads_them():
    from invisible_playwright._juggler.dispatcher import ExpectNotMet
    from invisible_playwright._juggler.transport import InProcessTransport

    sent = []
    transport = object.__new__(InProcessTransport)
    transport.emit_message = sent.append
    transport.reply_error(7, ExpectNotMet("no", {"timedOut": True}))
    transport.reply_error(8, RuntimeError("plain"))
    assert sent[0]["errorDetails"] == {"timedOut": True}
    assert "errorDetails" not in sent[1]
