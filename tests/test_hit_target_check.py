"""The hit target is checked once, after the approach and before the press.

⛔ IT USED TO BE AN INTERCEPTOR, and then two pure reads, and this file exists
because of what the SECOND read cost. The bundle once installed capture
listeners on the page's window and blocked events whose point had stopped
belonging to the element; that was a tell, because a real `mousedown` that
vanishes under `preventDefault` is not something an input stack produces. It
was replaced by a read before the action and a read after it, and the one after
turned "the target moved" into a retry.

⛔ THE READ AFTER CANNOT ANSWER THE QUESTION IT WAS ASKED. Once the event has
gone out, "the point stopped belonging to the element" is the same observation
for a click that missed and for a click that worked and moved the control out
from under the pointer - and the second is most controls, not an edge case.
Measured on the published 0.22.0, one call each: a button that hides itself on
click received THIRTY-NINE clicks and the call reported failure; a panel toggle
was opened and closed by one call, which reported success; a button that only
moves, and one that does nothing, were right thirty times out of thirty.

So there is one read, and it sits between the approach and the press:

  * before the approach is too early - a page that rearranges itself on hover,
    which is what menus do, was being judged in the layout that existed before
    the pointer arrived;
  * after the press is unanswerable, and repeating the press is the one thing a
    retry loop must never do, because it cannot un-press a button.

What is left is one round trip between the read and the press. Closing that one
belongs to the engine, which is the only place that can verify and dispatch
without a gap. It is not closed by reading the DOM again afterwards.

The companion property, that the bundle touches the page's window zero times in
every phase, is measured in `tests/test_injected_page_surface.py`.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.actions import Actions, WrongHitTarget

MAIN = "frame-main"
CHILD = "frame-child"


class _Injected:
    """Answers the read `_act_on_target` makes, and records it in order."""

    def __init__(self, verdicts=("done",), origins=None, log=None):
        self.verdicts = list(verdicts)
        self.points: list = []
        self.frames: list = []
        self.origins = origins or {}
        self.origin_reads = 0
        self.log = log if log is not None else []

    def check_hit_target(self, frame, element, point):
        self.points.append(point)
        self.frames.append(frame)
        self.log.append("check")
        return self.verdicts.pop(0) if self.verdicts else "done"

    def content_origin(self, frame):
        self.origin_reads += 1
        return self.origins[frame]


class _Lifecycle:
    main_frame = MAIN


def _actions(verdicts=("done",), origins=None, log=None) -> Actions:
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected(verdicts, origins, log)
    return actions


def _halves(log):
    """An approach and a commit that write their own names into `log`."""
    return (lambda: log.append("approach"),
            lambda: log.append("commit") or "ok")


def test_the_check_sits_between_the_approach_and_the_press():
    """⛔ THE ORDER IS THE WHOLE POINT, so the order is what is asserted.

    To watch this fail, move the read above `approach()`: a menu that opens
    under the cursor is then judged in the layout from before the pointer got
    there.
    """
    log: list = []
    actions = _actions(log=log)
    approach, commit = _halves(log)

    result = actions._act_on_target(MAIN, "element", (10.0, 20.0),
                                    approach=approach, commit=commit)

    assert result == "ok"
    assert log == ["approach", "check", "commit"]


def test_the_target_is_read_ONCE_and_never_after_the_press():
    """⛔ THE KNOWN-BAD THIS FILE WAS WRITTEN FOR. A second read after the
    press cannot tell a miss from a control that did its job, and the caller
    turned it into a retry - which presses again.

    To watch this fail, add a read after `commit()`: this asserts on the number
    of reads AND on nothing following the commit, so narrowing the second read
    to some subset of verdicts does not satisfy it either.
    """
    log: list = []
    actions = _actions(log=log)
    approach, commit = _halves(log)

    actions._act_on_target(MAIN, "element", (10.0, 20.0),
                           approach=approach, commit=commit)

    assert len(actions.inj.points) == 1, (
        "the target was read %d times; a read after the press is unanswerable"
        % len(actions.inj.points))
    assert log[-1] == "commit", "something happened after the press"


def test_a_point_that_has_already_moved_stops_the_press_HAPPENING():
    """⛔ THE READ IS NOT ADVISORY: if it fails, the press must not happen.

    The approach has already run by then, and that is deliberate - moving the
    pointer is what a hand does before it presses, and it is what can make the
    page move. What must not happen is the press.

    To watch this fail, move `commit()` above the read.
    """
    log: list = []
    actions = _actions(verdicts=("<div id='overlay'>",), log=log)
    approach, commit = _halves(log)

    with pytest.raises(WrongHitTarget, match="overlay"):
        actions._act_on_target(MAIN, "element", (10.0, 20.0),
                               approach=approach, commit=commit)

    assert "commit" not in log, "it pressed even though the point had moved"
    assert log == ["approach", "check"]


def test_a_nested_frame_is_asked_about_ITS_OWN_coordinates():
    """⛔ A POINT IS IN THE MAIN FRAME'S SPACE when it arrives here, because
    that is what `getContentQuads` answers and what `dispatchMouseEvent` wants.
    Handing that number to a hit test running inside a child asks about a
    coordinate that means something else, and the child answers `<html>` for an
    element sitting right under the pointer.

    To watch this fail, return `point` unchanged from `_hit_point`.
    """
    log: list = []
    actions = _actions(origins={MAIN: {"x": 100.0, "y": 200.0},
                                CHILD: {"x": 130.0, "y": 260.0}}, log=log)
    approach, commit = _halves(log)
    actions._act_on_target(CHILD, "element", (10.0, 20.0),
                           approach=approach, commit=commit)

    # the child's content starts 30 right and 60 down from the main frame's, so
    # the same screen point is 30 left and 60 up in the child's own space
    assert actions.inj.points == [(-20.0, -40.0)]
    assert actions.inj.frames == [CHILD]


def test_the_main_frame_pays_for_no_conversion_at_all():
    """The shift costs two extra round trips. The frame that never needs them
    must not make them: `origins` is deliberately empty here, so a lookup would
    raise instead of quietly returning something."""
    log: list = []
    actions = _actions(origins={}, log=log)
    approach, commit = _halves(log)
    actions._act_on_target(MAIN, "element", (7.0, 8.0),
                           approach=approach, commit=commit)
    assert actions.inj.origin_reads == 0
    assert actions.inj.points == [(7.0, 8.0)]
