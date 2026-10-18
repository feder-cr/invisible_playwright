"""The caller's code is sent once; a poll goes on through a navigation.

Juggler sends a message still waiting for its answer again to the document that
replaces the one it went to, so a world that went under a call does not say
whether the call ran ([B285]). From 41.36.0 the core made `page.evaluate` again
in the new world, which ran a script with an effect twice. These are the two
places of this server that run the caller's code and decide what a vanished
world means there: `$$eval` is sent once, and `wait_for_function` asks again on
its next turn, which is what polling is.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from invisible_core.juggler.injected import ContextDestroyed
from invisible_playwright._juggler import server

pytestmark = pytest.mark.unit


FUNCTION = {"type": "function", "objectId": "fn"}


class _Injected:
    """`answers` are what the caller's script produces, turn by turn;
    `called` what its function answers, call by call."""

    def __init__(self, answers, called=()):
        self.answers = list(answers)
        self.called = list(called)
        self.calls = []
        self.repeatable = False

    def evaluate_raw(self, context, expression, *, by_value=False, timeout=30.0):
        self.calls.append(("evaluate_raw", expression))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def in_current_world(self, attempt, *, timeout):
        self.repeatable = True
        return attempt(None)

    def call(self, frame_id, declaration, *arguments, **kwargs):
        self.calls.append(("call", kwargs))
        return [1, 2]

    def read_all(self, frame_id, selector, what, *args, **kwargs):
        self.calls.append(("read_all", what))
        return ["a", "b"]

    def query_selector_all(self, frame_id, selector):
        self.calls.append(("query_selector_all", selector))
        return ["u1", "u2"]

    def caller_context(self, frame_id):
        return "caller-ctx"

    def node_to_caller(self, frame_id, element):
        self.calls.append(("node_to_caller", element))
        return "c-" + element

    def call_raw(self, context, declaration, args, *, by_value=False, timeout=30.0):
        self.calls.append(("call_raw", (context, [a.get("objectId") for a in args])))
        if declaration == server._ARRAY:
            return {"objectId": "els", "type": "object", "subtype": "array"}
        return self.called.pop(0) if self.called else {"value": 2}

    def release(self, context, object_id):
        self.calls.append(("release", object_id))

    def dispose(self, frame_id, object_id):
        self.calls.append(("dispose", object_id))


def _frame(injected):
    frame = server.FrameDispatcher.__new__(server.FrameDispatcher)
    frame.page = SimpleNamespace(injected=injected)
    frame.frame_id = "F"
    frame.server = None
    return frame


def test_a_poll_goes_on_through_a_world_that_went(monkeypatch):
    monkeypatch.setattr(server, "ElementHandleDispatcher",
                        lambda *a, **k: SimpleNamespace(channel="handle"))
    # A turn whose world went, then a falsy turn, then a truthy one. Each turn
    # is ONE command ([B323]): the script is evaluated in the world that is
    # there, and the function it produced is called on the turns after, as
    # Playwright's poll keeps `evaledExpression` ([B340]). The truthy value is
    # already the handle, and the function's reference is released at the end.
    injected = _Injected([ContextDestroyed("Execution context was destroyed"),
                          FUNCTION],
                         called=[{"value": False},
                                 {"objectId": "oid", "type": "object"}])
    answer = _frame(injected).op_wait_for_function(
        {"expression": "() => window.ready", "timeout": 5000})
    assert answer == {"handle": "handle"}
    assert [c for c, _ in injected.calls] == [
        "evaluate_raw", "evaluate_raw", "call_raw", "call_raw", "release"]
    assert injected.calls[-1] == ("release", "fn")


def test_eval_on_selector_all_sends_the_callers_function_once_in_its_world():
    """The elements are this package's read; the caller's function runs ONCE
    ([B285]), in the caller's world, on the same nodes carried there ([B322]),
    and the copies and the reads are released after it."""
    injected = _Injected([FUNCTION])
    answer = _frame(injected).op_eval_on_selector_all(
        {"selector": "li", "expression": "els => els.length"})
    assert answer == {"value": {"n": 2}}
    assert injected.calls == [
        ("query_selector_all", "li"),
        ("node_to_caller", "u1"), ("node_to_caller", "u2"),
        ("call_raw", ("caller-ctx", ["c-u1", "c-u2"])),
        ("evaluate_raw", "els => els.length"),
        ("call_raw", ("caller-ctx", ["fn", "els", None])),
        ("release", "fn"),
        ("release", "els"), ("release", "c-u1"), ("release", "c-u2"),
        ("dispose", "u1"), ("dispose", "u2"),
    ]


def test_the_librarys_read_of_every_match_is_the_cores_repeatable_read():
    """`all_inner_texts` is not the caller's code: it is answered by the core's
    `read_all`, which the page moving on under it does not end ([B282])."""
    injected = _Injected([])
    answer = _frame(injected).op_read_all({"selector": "p", "what": "innerText"})
    assert answer == {"value": ["a", "b"]}
    assert injected.calls == [("read_all", "innerText")]


@pytest.mark.parametrize("method, what", [("all_inner_texts", "innerText"),
                                          ("all_text_contents", "textContent")])
def test_the_fork_s_locator_asks_the_server_s_read_and_not_eval_on_selector_all(
        method, what):
    """Upstream's client sends these through `evalOnSelectorAll` with a fixed
    function, which the server cannot tell from the caller's `$$eval` and
    sends once; the fork asks for the read by name."""
    import asyncio

    from invisible_playwright._pw._impl._locator import Locator

    asked = []

    class _Frame:
        async def _read_all(self, selector, kind):
            asked.append((selector, kind))
            return ["x"]

        async def eval_on_selector_all(self, *a, **k):
            asked.append("eval_on_selector_all")
            return ["x"]

    locator = Locator.__new__(Locator)
    locator._frame = _Frame()
    locator._selector = "#p"
    assert asyncio.run(getattr(locator, method)()) == ["x"]
    assert asked == [("#p", what)]


@pytest.mark.parametrize("sent, repeatable", [({}, False),
                                               ({"repeatable": True}, True),
                                               ({"repeatable": "yes"}, False)])
def test_only_evaluate_read_makes_the_page_world_code_again(sent, repeatable):
    """`page.evaluate` sends the caller's code once; the fork's
    `evaluate_read` alone says `repeatable`, and only a real True counts."""
    injected = _Injected([{"value": 2}])
    params = {"expression": "1 + 1"}
    params.update(sent)
    _frame(injected).op_evaluate(params)
    assert injected.repeatable is repeatable


def test_read_all_refuses_what_is_not_one_of_its_two_reads():
    with pytest.raises(server.ProtocolException, match="innerText or textContent"):
        _frame(_Injected([])).op_read_all({"selector": "p", "what": "innerHTML"})
