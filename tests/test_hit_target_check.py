"""The hit target is checked before the action and again after it.

⛔ IT USED TO BE AN INTERCEPTOR, and this file exists because of what that cost.
The bundle installed capture listeners on the PAGE's window for the duration of
every action, validated each event as it arrived, and called `preventDefault`
plus `stopImmediatePropagation` on the ones that landed elsewhere.

Two things were wrong with that, and only one of them was the listeners. The
block itself was a tell: a real `mousedown` that vanishes is not something any
input stack produces, so the defence announced us exactly when it worked.

What replaces it is two pure DOM reads around the action. The one BEFORE refuses
to act on a point that has stopped belonging to the element. The one AFTER
catches a target that moved in between and turns it into a retry. What a page
sees in that case is a click that landed where the thing it was aimed at used to
be - which is what a hand produces when the layout shifts under it.

The companion property, that the bundle now touches the page's window zero times
in every phase, is measured in `tests/test_injected_page_surface.py`.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.actions import Actions, WrongHitTarget

MAIN = "frame-main"
CHILD = "frame-child"


class _Injected:
    """Answers the two reads `_with_hit_target` makes, and records them."""

    def __init__(self, verdicts=("done", "done"), origins=None):
        self.verdicts = list(verdicts)
        self.points: list = []
        self.frames: list = []
        self.origins = origins or {}
        self.origin_reads = 0

    def check_hit_target(self, frame, element, point):
        self.points.append(point)
        self.frames.append(frame)
        return self.verdicts.pop(0) if self.verdicts else "done"

    def content_origin(self, frame):
        self.origin_reads += 1
        return self.origins[frame]


class _Lifecycle:
    main_frame = MAIN


def _actions(verdicts=("done", "done"), origins=None) -> Actions:
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected(verdicts, origins)
    return actions


def test_the_check_runs_before_and_after_the_action():
    actions = _actions()
    ran: list = []
    result = actions._with_hit_target(
        MAIN, "element", (10.0, 20.0), lambda: ran.append("acted") or "ok")
    assert result == "ok"
    assert ran == ["acted"]
    assert actions.inj.points == [(10.0, 20.0), (10.0, 20.0)], (
        "the check must run twice, on the same point")


def test_a_point_that_has_already_moved_stops_the_action_HAPPENING():
    """⛔ THE FIRST KNOWN-BAD INPUT. The before-check is not advisory: if it
    fails, the action must not run at all. To watch this fail, move the
    `act()` call above the first check in `_with_hit_target`."""
    actions = _actions(verdicts=("<div id='overlay'>", "done"))
    ran: list = []

    with pytest.raises(WrongHitTarget, match="overlay"):
        actions._with_hit_target(
            MAIN, "element", (10.0, 20.0), lambda: ran.append("acted"))

    assert ran == [], "the action ran even though the point had moved"
    assert len(actions.inj.points) == 1, "it kept checking after refusing"


def test_a_target_that_moves_DURING_the_action_is_caught_afterwards():
    """⛔ THE SECOND KNOWN-BAD INPUT, and the one that replaces the interceptor.

    The action succeeds, and the check afterwards is what notices the click
    landed somewhere else. To watch this fail, delete the second check: the
    click then reports success and the retry loop never learns.
    """
    actions = _actions(verdicts=("done", "<div id='banner'>"))
    ran: list = []

    with pytest.raises(WrongHitTarget, match="banner"):
        actions._with_hit_target(
            MAIN, "element", (10.0, 20.0), lambda: ran.append("acted"))

    assert ran == ["acted"], "the action should have run before being judged"
    assert len(actions.inj.points) == 2


def test_a_nested_frame_is_asked_about_ITS_OWN_coordinates():
    """⛔ A POINT IS IN THE MAIN FRAME'S SPACE when it arrives here, because
    that is what `getContentQuads` answers and what `dispatchMouseEvent` wants.
    Handing that number to a hit test running inside a child asks about a
    coordinate that means something else, and the child answers `<html>` for an
    element sitting right under the pointer.

    To watch this fail, return `point` unchanged from `_hit_point`.
    """
    actions = _actions(origins={MAIN: {"x": 100.0, "y": 200.0},
                                CHILD: {"x": 130.0, "y": 260.0}})
    actions._with_hit_target(CHILD, "element", (10.0, 20.0), lambda: "ok")

    # the child's content starts 30 right and 60 down from the main frame's, so
    # the same screen point is 30 left and 60 up in the child's own space
    assert actions.inj.points == [(-20.0, -40.0), (-20.0, -40.0)]
    assert actions.inj.frames == [CHILD, CHILD]


def test_the_main_frame_pays_for_no_conversion_at_all():
    """The shift costs two extra round trips. The frame that never needs them
    must not make them: `origins` is deliberately empty here, so a lookup would
    raise instead of quietly returning something."""
    actions = _actions(origins={})
    actions._with_hit_target(MAIN, "element", (7.0, 8.0), lambda: "ok")
    assert actions.inj.origin_reads == 0
    assert actions.inj.points == [(7.0, 8.0), (7.0, 8.0)]
