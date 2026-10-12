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

    def evaluate_in_main(self, frame_id, expression, *, by_value=True, timeout=30.0):
        self.calls.append(("evaluate_in_main", by_value))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def call(self, frame_id, declaration, *arguments, **kwargs):
        self.calls.append(("call", kwargs))
        return [1, 2]


def _frame(injected):
    frame = server.FrameDispatcher.__new__(server.FrameDispatcher)
    frame.page = SimpleNamespace(injected=injected)
    frame.frame_id = "F"
    frame.server = None
    return frame


def test_a_poll_goes_on_through_a_world_that_went(monkeypatch):
    monkeypatch.setattr(server, "ElementHandleDispatcher",
                        lambda *a, **k: SimpleNamespace(channel="handle"))
    injected = _Injected([ContextDestroyed("Execution context was destroyed"),
                          True, "oid"])
    answer = _frame(injected).op_wait_for_function(
        {"expression": "() => window.ready", "isFunction": True, "timeout": 5000})
    assert answer == {"handle": "handle"}
    assert [c for c, _ in injected.calls] == ["evaluate_in_main"] * 3


def test_eval_on_selector_all_sends_the_callers_function_once():
    injected = _Injected([])
    _frame(injected).op_eval_on_selector_all(
        {"selector": "li", "expression": "els => els.length", "isFunction": True})
    assert injected.calls == [("call", {"repeatable": False})]
