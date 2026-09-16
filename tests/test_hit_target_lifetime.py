"""The interceptor is disarmed even when the action it guards raises.

⛔ THE DEFECT THIS FILE EXISTS FOR. `stop()` used to live on the success path of
`_with_hit_target`, inside the `try`. An action that raised - a timeout, a lost
target, a closed page - therefore never called it, and `stop()` is not a
formality: it is what removes the capture listeners the interceptor installs on
the page's window, and what clears the armed listener itself.

What a leaked interceptor does is not passive. Its listener runs on every event
of the action's kind, and once its `result` is anything other than `"done"` it
calls `preventDefault`, `stopPropagation` and `stopImmediatePropagation`. So a
click that timed out could go on eating events on that page afterwards - a
person's click disappearing is observable, and nothing in the suite could see
it, because every test that drove an action drove one that SUCCEEDED.

The companion property, that the listeners exist only while an action is in
flight, is measured in `tests/test_injected_page_surface.py`.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.actions import Actions, WrongHitTarget


class _Injected:
    """Records what `_with_hit_target` asks the page to do.

    It answers the three calls that method makes, and nothing else: the handle
    for the armed interceptor, the `error` read, and the `stop()` read.
    """

    def __init__(self, stop_verdict: str = "done"):
        self.calls: list = []
        self.disposed: list = []
        self.stop_verdict = stop_verdict

    def call(self, frame, declaration, *args, **kw):
        if "setupHitTargetInterceptor" in declaration:
            self.calls.append("arm")
            return "handle"
        if "h.error" in declaration:
            self.calls.append("read-error")
            return ""
        if "h.stop" in declaration:
            self.calls.append("stop")
            return self.stop_verdict
        raise AssertionError("unexpected call: %s" % declaration[:80])

    def dispose(self, frame, handle):
        self.disposed.append(handle)


class _Lifecycle:
    main_frame = "frame-1"


def _actions(stop_verdict: str = "done") -> Actions:
    injected = _Injected(stop_verdict)
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = injected
    return actions


def test_a_successful_action_stops_the_interceptor_exactly_once():
    """The path that already worked, asserted so the fix below cannot be paid
    for with a second round trip on every click."""
    actions = _actions()
    result = actions._with_hit_target(
        "frame-1", "element", (1.0, 2.0), "mouse", lambda: "clicked")
    assert result == "clicked"
    assert actions.inj.calls == ["arm", "read-error", "stop"]
    assert actions.inj.disposed == ["handle"]


def test_an_action_that_RAISES_still_disarms_the_interceptor():
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE.

    To watch it fail, move the `self._stop_hit_target(f, h)` call out of the
    `finally` in `_with_hit_target` and back onto the success path: `stop`
    disappears from the recorded calls, and the listeners it would have removed
    stay on the page's window.
    """
    actions = _actions()

    def act():
        raise TimeoutError("the click never landed")

    with pytest.raises(TimeoutError):
        actions._with_hit_target(
            "frame-1", "element", (1.0, 2.0), "mouse", act)

    assert "stop" in actions.inj.calls, (
        "the action raised and the interceptor was left armed: %s"
        % actions.inj.calls)
    assert actions.inj.disposed == ["handle"], "the handle leaked as well"


def test_the_error_the_caller_needs_survives_a_failing_disarm():
    """⛔ A cleanup that raises must not replace the exception on its way up.

    The ordinary reason the disarm fails is that the document holding the
    listeners is gone, in which case they went with it - so the caller has to
    keep reading the real failure, not a second one about the cleanup.
    """
    actions = _actions()
    injected = actions.inj
    real_call = injected.call

    def call(frame, declaration, *args, **kw):
        if "h.stop" in declaration:
            injected.calls.append("stop")
            raise RuntimeError("the page is gone")
        return real_call(frame, declaration, *args, **kw)

    injected.call = call

    with pytest.raises(TimeoutError, match="the click never landed"):
        actions._with_hit_target(
            "frame-1", "element", (1.0, 2.0), "mouse",
            lambda: (_ for _ in ()).throw(TimeoutError("the click never landed")))

    assert "stop" in injected.calls, "the disarm was not even attempted"
    assert injected.disposed == ["handle"]


def test_a_wrong_hit_target_is_still_reported_after_the_move():
    """The verdict is read on the success path precisely because
    `done != "done"` is itself a failure. Moving the disarm must not swallow
    it into the cleanup path, where its return value would be dropped."""
    actions = _actions(stop_verdict='{"hit": "other"}')
    with pytest.raises(WrongHitTarget, match="other"):
        actions._with_hit_target(
            "frame-1", "element", (1.0, 2.0), "mouse", lambda: "clicked")
    assert actions.inj.calls.count("stop") == 1, (
        "the verdict path stopped twice: %s" % actions.inj.calls)
