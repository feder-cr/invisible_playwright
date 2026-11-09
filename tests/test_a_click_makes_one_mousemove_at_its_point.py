"""`Mouse.click` in a humanising session puts ONE `mousemove` on its point.

The stroke the hand draws to the point stops short of it (`Pointer.travel`
leaves the endpoint to the caller). The click then used to emit that endpoint
twice: `Pointer.click_at` sent it as an explicit move, and the core's
`Actions.click_at` sent it again as its own approach before the press, so the
engine received two `mousemove` with the same coordinates at the same instant,
which no mouse produces and a page can count.

Nothing here launches a browser. The page is a real `PageDispatcher` over a real
`invisible_core` `Actions` whose connection only records (the same fakes as
`test_pointer_server`), on a virtual clock. Both transports end in the same
call, `PageDispatcher.call("mouseClick", ...)`: the in-process client sends it
through `_impl/_input.py`, a remote client over the wire, and the table that
routes it is asserted below, so one sequence is the sequence of both.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from invisible_playwright._juggler.server import PageDispatcher

from tests.test_pointer_server import _boxes_of, build_page, clock  # noqa: F401

pytestmark = pytest.mark.unit

GOLDEN = json.loads(
    (pathlib.Path(__file__).parent / "data" / "pointer_golden.json").read_text(
        encoding="utf-8"))["scenarios"]


def _mouse_events(log):
    return [e for e in log if e["m"] == "Page.dispatchMouseEvent"]


def _repeated_moves(events):
    """Consecutive `mousemove` with the same coordinates: (index, x, y)."""
    return [(i, e["x"], e["y"]) for i, e in enumerate(events)
            if i and e["type"] == "mousemove"
            and events[i - 1]["type"] == "mousemove"
            and (e["x"], e["y"]) == (events[i - 1]["x"], events[i - 1]["y"])]


@pytest.mark.parametrize("point", [(120.0, 60.0), (640.5, 360.25), (3.0, 700.0)])
@pytest.mark.parametrize("seed", [4242, 31337])
def test_a_humanised_mouse_click_has_no_repeated_mousemove(clock, seed, point):
    page = build_page(clock, _boxes_of("x"), seed=seed, number=0, budget=1.5)
    page.call("mouseClick", {"x": point[0], "y": point[1]})
    events = _mouse_events(page.log)
    assert _repeated_moves(events) == []
    # and the click is still where it was asked: one move onto the exact
    # point, then the press and the release on it
    assert [e["type"] for e in events[-3:]] == ["mousemove", "mousedown", "mouseup"]
    assert all((e["x"], e["y"]) == point for e in events[-3:])
    assert len(events) > 10  # still a stroke, not a jump


def test_the_point_is_reached_by_exactly_one_mousemove(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.call("mouseClick", {"x": 120.0, "y": 60.0})
    on_point = [e for e in _mouse_events(page.log)
                if e["type"] == "mousemove" and (e["x"], e["y"]) == (120.0, 60.0)]
    assert len(on_point) == 1


def test_a_double_click_still_presses_twice_on_one_move(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.call("mouseClick", {"x": 120.0, "y": 60.0, "clickCount": 2})
    events = _mouse_events(page.log)
    assert _repeated_moves(events) == []
    assert [e["type"] for e in events[-5:]] == [
        "mousemove", "mousedown", "mouseup", "mousedown", "mouseup"]


def test_without_a_hand_a_click_is_one_move_and_the_press(clock):
    """No humanising: the server adds nothing, and the click's own approach is
    the only move (the sequence a plain protocol client always got)."""
    page = build_page(clock, _boxes_of("x"), seed=None, number=0, budget=None)
    assert not page.pointer.active
    page.call("mouseClick", {"x": 120.0, "y": 60.0})
    assert [e["type"] for e in _mouse_events(page.log)] == [
        "mousemove", "mousedown", "mouseup"]


def test_both_transports_reach_the_click_through_the_same_operation():
    """The in-process client and a remote one send `mouseClick`; the server
    routes that one name to the one function that draws the stroke."""
    assert PageDispatcher.METHODS["mouseClick"] == "op_mouse_click"
    import inspect
    from invisible_playwright._pw._impl import _input
    assert '"mouseClick"' in inspect.getsource(_input)


def test_the_pointer_stands_on_the_point_after_the_click(clock):
    """The next walk departs from where the click ended, not from where the
    stroke stopped short."""
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.call("mouseClick", {"x": 120.0, "y": 60.0})
    assert page.pointer._here() == (120.0, 60.0)
    before = len(page.log)
    clock.t += 0.01
    page.frame.op_click({"selector": "#b", "noWaitAfter": True})
    first_of_next = [e for e in _mouse_events(page.log[before:])
                     if e["type"] == "mousemove"][0]
    assert abs(first_of_next["x"] - 120.0) < 60
    assert abs(first_of_next["y"] - 60.0) < 60


def test_the_golden_mouse_click_session_has_one_move_on_its_point():
    events = _mouse_events(GOLDEN["explicit_pointer"]["engine_events"])
    downs = [i for i, e in enumerate(events) if e["type"] == "mousedown"]
    i = downs[0]
    on_point = [e for e in events[:i] if e["type"] == "mousemove"
                and (e["x"], e["y"]) == (120.0, 60.0)]
    assert len(on_point) == 1
