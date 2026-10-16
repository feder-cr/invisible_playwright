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


class _Injected:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def evaluate_in_main(self, frame_id, expression, *, by_value=True, timeout=30.0,
                         repeatable=False):
        self.calls.append(("evaluate_in_main", by_value))
        self.repeatable = repeatable
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def poll_in_main(self, frame_id, expression, *, timeout=30.0):
        self.calls.append(("poll_in_main", None))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def call(self, frame_id, declaration, *arguments, **kwargs):
        self.calls.append(("call", kwargs))
        return [1, 2]

    def read_all(self, frame_id, selector, what, *args, **kwargs):
        self.calls.append(("read_all", what))
        return ["a", "b"]


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
    # sends the caller's code ONCE (`poll_in_main`, [B323]): the truthy value
    # is already the handle, and nothing runs the predicate again to get one.
    injected = _Injected([ContextDestroyed("Execution context was destroyed"),
                          None, {"objectId": "oid", "type": "object"}])
    answer = _frame(injected).op_wait_for_function(
        {"expression": "() => window.ready", "isFunction": True, "timeout": 5000})
    assert answer == {"handle": "handle"}
    assert [c for c, _ in injected.calls] == ["poll_in_main"] * 3


def test_eval_on_selector_all_sends_the_callers_function_once():
    injected = _Injected([])
    _frame(injected).op_eval_on_selector_all(
        {"selector": "li", "expression": "els => els.length", "isFunction": True})
    assert injected.calls == [("call", {"repeatable": False})]


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
    injected = _Injected([2])
    params = {"expression": "1 + 1", "isFunction": False}
    params.update(sent)
    _frame(injected).op_evaluate(params)
    assert injected.repeatable is repeatable


def test_read_all_refuses_what_is_not_one_of_its_two_reads():
    with pytest.raises(server.ProtocolException, match="innerText or textContent"):
        _frame(_Injected([])).op_read_all({"selector": "p", "what": "innerHTML"})
