"""The server draws the pointer's walk, and draws exactly what the client did.

Nothing here launches a browser. The page is a real `PageDispatcher` over a real
`invisible_core` `Actions`, whose connection only RECORDS what the engine would
receive (`Page.dispatchMouseEvent`, `Page.dispatchWheelEvent`), on a virtual
clock that advances only when something sleeps.

THE GOLDEN. `tests/data/pointer_golden.json` is what the engine received from the
in-process client's humanised pointer at commit 5d9ad70, when the walk was the
client's (`_cursor._approach` and friends sending N `mouseMove` before the
action), for the same seeds, page numbers, boxes and idle gaps. Its origin is
written in the file and the script that made it is `tests/data/
pointer_golden_origin.py`. These tests assert that a plain `Frame.click` (and
hover, dblclick, tap, check) arriving at the server draws the SAME sequence of
events - same coordinates, same instants - which is what makes a page see the
same through any client. They fail on the old server, where the one
`Frame.click` produced one `mousemove`.
"""
from __future__ import annotations

import json
import pathlib
import time
from types import SimpleNamespace

import pytest

from invisible_core.juggler import PageActs
from invisible_core.juggler import actions as core_actions
from invisible_core.juggler.actions import Actions
from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle

from invisible_playwright import _cursor
from invisible_playwright._juggler import pointer as pointer_mod
from invisible_playwright._juggler.dispatcher import Dispatcher
from invisible_playwright._juggler.pointer import Pointer
from invisible_playwright._juggler.server import (
    FrameDispatcher, JugglerServer, PageDispatcher, take_session_motion,
    session_draws_pointer, POINTER_PREF, SESSION_SEED_PREF, MOTION_BUDGET_PREF)

pytestmark = pytest.mark.unit

GOLDEN = json.loads(
    (pathlib.Path(__file__).parent / "data" / "pointer_golden.json").read_text(
        encoding="utf-8"))["scenarios"]

VIEWPORT = {"width": 1280, "height": 720}


# ── the engine's side: a connection that records ────────────────────────────

class VirtualClock:
    """Advances only when something sleeps, for the pointer AND for the pauses
    the core's actions take (press settle, dwell)."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


class RecordingConnection:
    def __init__(self, clock, boxes) -> None:
        self.clock = clock
        self.boxes = boxes
        self.log = []
        self.current = None

    def send(self, method, params=None, session=None, timeout=None, **kw):
        if method.startswith("Page.dispatch"):
            rec = {"t": round(self.clock.t, 9), "m": method}
            rec.update({k: v for k, v in (params or {}).items()
                        if k != "landsOn"})
            self.log.append(rec)
        if method == "Page.getContentQuads":
            b = self.boxes[self.current]
            return {"quads": [{
                "p1": {"x": b["x"], "y": b["y"]},
                "p2": {"x": b["x"] + b["width"], "y": b["y"]},
                "p3": {"x": b["x"] + b["width"], "y": b["y"] + b["height"]},
                "p4": {"x": b["x"], "y": b["y"] + b["height"]}}]}
        if method == "Page.dispatchMouseEvent" and "landsOn" in (params or {}):
            return {"landing": {"landed": True, "on": "el"}}
        return {}


class FakeInjected:
    """The page's script, answering what the actions and the hand ask."""

    def __init__(self, conn, boxes, hits="all") -> None:
        self.conn = conn
        self.boxes = boxes
        self.hits = hits
        self.state_reads = 0
        self.queries = []
        self.disposed = []

    def query_selector(self, f, selector, strict=False):
        self.queries.append(selector)
        if selector in self.boxes:
            self.conn.current = selector
            return "el"
        return None

    def element_states(self, f, el, states):
        return {"ok": True}

    def evaluate(self, f, expr, **kw):
        return {"w": VIEWPORT["width"], "h": VIEWPORT["height"]}

    def check_hit_target(self, f, el, point):
        # the engine's hit test is ONE for the hand's probe and for the
        # action's own check (`Actions.hit_target`); "centre" is a round
        # control whose only sure point is the middle
        if self.hits == "all":
            return "done"
        b = self.boxes[self.conn.current]
        near = (abs(point[0] - (b["x"] + b["width"] / 2.0)) <= 1.0
                and abs(point[1] - (b["y"] + b["height"] / 2.0)) <= 1.0)
        return "done" if near else "other"

    def element_state(self, f, el, state):
        # unchecked before the click, checked after it
        self.state_reads += 1
        return self.state_reads > 1

    def bounding_box(self, f, el):
        b = self.boxes[self.conn.current]
        return dict(b)

    def dispose(self, f, el):
        self.disposed.append(el)

    def scroll_into_view(self, *a, **k):
        raise AssertionError("not expected")


class Life:
    main_frame = "main"


def build_page(clock, boxes, *, seed, number, budget, hits="all", draws=True,
               viewport=VIEWPORT):
    """A PageDispatcher over a real Actions and a real Pointer, as
    `PageDispatcher._build` assembles them."""
    server = JugglerServer()
    messages = []
    server.attach(SimpleNamespace(emit_message=messages.append))
    context = Dispatcher(server, None)
    context.options = {}
    conn = RecordingConnection(clock, boxes)
    injected = FakeInjected(conn, boxes, hits)
    actions = Actions(conn, "sess", Life(), injected, acts=PageActs(number),
                      session_seed=seed, motion_budget_s=budget, layout="us")
    page = object.__new__(PageDispatcher)
    page.context = context
    page.lifecycle = Lifecycle(EventListeners(), "session")
    page.injected = injected
    page.actions = actions
    page.viewport_size = dict(viewport)
    page.pointer = Pointer(actions, injected, number, seed, budget,
                           lambda: page.viewport_size, draws=draws)
    page._frames = {}
    page._navigation_requests = {}
    page._announced_closed = False
    Dispatcher.__init__(page, server, context)
    page.frame = FrameDispatcher(server, page, "main")
    page.log = conn.log
    page.injected_fake = injected
    return page


@pytest.fixture
def clock(monkeypatch):
    c = VirtualClock()
    monkeypatch.setattr(pointer_mod, "_CLOCK", c)
    # the core's actions pause with `time.sleep` (press settle, dwell)
    monkeypatch.setattr(core_actions, "time", SimpleNamespace(
        sleep=c.sleep, monotonic=time.monotonic, perf_counter=time.perf_counter))
    monkeypatch.delenv(_cursor.ENGINE_ENV, raising=False)
    monkeypatch.delenv(_cursor.LANDING_ENV, raising=False)
    _cursor._reset_warnings()
    yield c
    _cursor._reset_warnings()


def _boxes_of(name):
    # the golden's boxes: see tests/data/pointer_golden_origin.py
    return {
        "#b": {"x": 400.0, "y": 300.0, "width": 100.0, "height": 40.0},
        "#c": {"x": 880.5, "y": 120.25, "width": 160.0, "height": 36.0},
        "#tiny": {"x": 700.0, "y": 500.0, "width": 8.0, "height": 8.0},
    }


def play(page, clock, ops):
    """What a PLAIN protocol client sends, op by op, to the server."""
    for op in ops:
        kind = op[0]
        if kind == "wait":
            clock.t += op[1]
        elif kind in ("click", "hover", "dblclick", "tap", "check"):
            params = {"selector": op[1], "noWaitAfter": True}
            params.update(op[2] if len(op) > 2 else {})
            getattr(page.frame, "op_" + kind)(params)
        elif kind == "move":
            # the in-process client marks its own explicit move
            page.op_mouse_move({"x": op[1], "y": op[2], "humanise": True})
        elif kind == "mouse_click":
            page.op_mouse_click({"x": op[1], "y": op[2]})
        elif kind == "wheel":
            page.op_mouse_wheel({"deltaX": op[1], "deltaY": op[2]})
        else:  # pragma: no cover
            raise ValueError(kind)


def _normalised(log):
    # JSON round trip: the golden is JSON, and a float survives it exactly.
    return json.loads(json.dumps(log))


# ═══════════════════════════════════════════════════════════════════════════
# 1. Through the server, a plain action draws what the client used to draw
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_a_plain_action_draws_the_walk_the_client_used_to_draw(clock, name):
    """Same seed, same page number, same targets, same idle gaps: the engine
    receives the same events at the same instants as from the client-side
    walk of 5d9ad70. One test per session of the golden: clicks, hovers, a
    double click, a tap, a check, a pinned position, an idle gap that fidgets,
    a tight cap, a landing the server refuses, an explicit move, a
    `Mouse.click` and three wheels."""
    spec = GOLDEN[name]["spec"]
    page = build_page(clock, _boxes_of(name), seed=spec["seed"],
                      number=spec["number"], budget=spec["budget"],
                      hits=spec.get("hits", "all"))
    play(page, clock, spec["ops"])
    expected = GOLDEN[name]["engine_events"]
    got = _normalised(page.log)
    assert len(got) == len(expected)
    assert got == expected


def test_a_click_is_more_than_one_mousemove(clock):
    """The defect, stated plainly: one Frame.click and the page saw one move."""
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    play(page, clock, [["click", "#b"]])
    moves = [e for e in page.log if e.get("type") == "mousemove"]
    assert len(moves) > 10


# ═══════════════════════════════════════════════════════════════════════════
# 2. What the server does NOT add
# ═══════════════════════════════════════════════════════════════════════════

def _kinds(page):
    return [e.get("type", e["m"]) for e in page.log]


def test_without_humanize_the_server_adds_no_movement(clock):
    """No seed: the approach is the one event the action itself makes."""
    page = build_page(clock, _boxes_of("x"), seed=None, number=0, budget=None)
    assert not page.pointer.active
    play(page, clock, [["click", "#b"]])
    moves = [e for e in page.log if e.get("type") == "mousemove"]
    assert len(moves) == 1
    assert (moves[0]["x"], moves[0]["y"]) == (450.0, 320.0)  # the box's centre


def test_a_session_whose_pointer_is_not_the_servers_adds_nothing(clock):
    """The engine's own expansion (`binary`) or none (`off`) carries a seed and
    a budget too; the server must not draw a path on top of either."""
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5,
                      draws=False)
    assert not page.pointer.active
    play(page, clock, [["click", "#b"], ["hover", "#c"], ["wheel", 0.0, 900.0]])
    assert [e["type"] for e in page.log if e["m"].endswith("MouseEvent")] == [
        "mousemove", "mousedown", "mouseup", "mousemove"]
    assert len([e for e in page.log if e["m"].endswith("WheelEvent")]) == 1


def test_an_explicit_mouse_move_is_not_walked(clock):
    """A client's `mouse.move` reaches the page as exactly the point it named,
    even in a session that humanises: the walk is only for the move the
    in-process client marks."""
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.op_mouse_move({"x": 800.0, "y": 500.0})
    assert [(e["x"], e["y"]) for e in page.log] == [(800.0, 500.0)]
    page.op_mouse_move({"x": 10.0, "y": 20.0, "steps": 1})
    assert len(page.log) == 2


def test_a_marked_move_with_steps_is_the_callers_own_interpolation(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.op_mouse_move({"x": 800.0, "y": 500.0, "steps": 4, "humanise": True})
    assert len(page.log) == 4


def test_a_trial_run_does_not_move_the_pointer(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    # the retry loop checks and acts on nothing under `trial`
    play(page, clock, [["click", "#b", {"trial": True}]])
    assert page.log == []
    assert page.injected_fake.queries == ["#b"]  # only the action's own query


def test_a_missing_element_adds_no_wait_and_no_movement(clock):
    """The probe gives up at once; the action does its own waiting and raises
    its own error."""
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    with pytest.raises(TimeoutError):
        page.frame.op_click({"selector": "#nowhere", "noWaitAfter": True,
                             "timeout": 60})
    assert page.log == []
    # one probe from the hand, then the action's own turns
    assert page.injected_fake.queries[0] == "#nowhere"


def test_a_caller_supplied_position_is_never_overridden(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    play(page, clock, [["click", "#b", {"position": {"x": 10.0, "y": 5.0}}]])
    downs = [e for e in page.log if e["type"] == "mousedown"]
    assert (downs[0]["x"], downs[0]["y"]) == (410.0, 305.0)


def test_the_landing_is_off_the_centre_and_differs_between_actions(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    play(page, clock, [["click", "#b"], ["wait", 0.1], ["click", "#b"]])
    downs = [(e["x"], e["y"]) for e in page.log if e["type"] == "mousedown"]
    assert len(downs) == 2 and downs[0] != downs[1]
    assert (450.0, 320.0) not in downs


def test_the_landing_can_be_switched_off(clock, monkeypatch):
    monkeypatch.setenv(_cursor.LANDING_ENV, "off")
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    play(page, clock, [["click", "#b"]])
    downs = [(e["x"], e["y"]) for e in page.log if e["type"] == "mousedown"]
    assert downs == [(450.0, 320.0)]


def test_a_landing_the_page_refuses_falls_back_to_the_centre(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5,
                      hits="centre")
    play(page, clock, [["click", "#b"]])
    downs = [(e["x"], e["y"]) for e in page.log if e["type"] == "mousedown"]
    assert downs == [(450.0, 320.0)]


def test_an_offscreen_target_is_left_to_the_action(clock):
    boxes = {"#far": {"x": 400.0, "y": 5000.0, "width": 100.0, "height": 40.0}}
    page = build_page(clock, boxes, seed=4242, number=0, budget=1.5)
    # the action would scroll it into view; the fake refuses that, which is how
    # this test sees that the walk added nothing before it
    with pytest.raises(AssertionError):
        page.frame.op_click({"selector": "#far", "noWaitAfter": True})
    assert page.log == []


def test_a_broken_planner_never_fails_the_action(clock, monkeypatch):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)

    def boom(*a, **k):
        raise RuntimeError("planner broke")
    monkeypatch.setattr(pointer_mod, "plan_approach", boom)
    with pytest.warns(RuntimeWarning, match="approach raised"):
        play(page, clock, [["click", "#b"]])
    assert [e["type"] for e in page.log if e["type"] != "mousemove"] == [
        "mousedown", "mouseup"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. The seed, the page, the budget
# ═══════════════════════════════════════════════════════════════════════════

def _first_move(clock, seed, number, budget=1.5):
    page = build_page(clock, _boxes_of("x"), seed=seed, number=number,
                      budget=budget)
    play(page, clock, [["click", "#b"]])
    return [(e["x"], e["y"], e["t"]) for e in page.log
            if e.get("type") == "mousemove"]


def test_the_same_seed_and_page_replay_the_walk(clock):
    a = _first_move(clock, 4242, 0)
    clock.t = 0.0
    b = _first_move(clock, 4242, 0)
    assert a == b


def test_another_seed_or_another_page_walks_differently(clock):
    base = _first_move(clock, 4242, 0)
    clock.t = 0.0
    assert _first_move(clock, 4243, 0) != base
    clock.t = 0.0
    assert _first_move(clock, 4242, 1) != base


def test_the_motion_seed_is_the_pages_own(clock, monkeypatch):
    """One derivation for the session seed and the page number, the client's
    (`_cursor.page_motion_seed`)."""
    seen = []
    real = _cursor._build_profile

    def spy(factory, seed, max_seconds):
        seen.append((seed, max_seconds))
        return real(factory, seed, max_seconds)
    monkeypatch.setattr(_cursor, "_build_profile", spy)
    build_page(clock, _boxes_of("x"), seed=4242, number=3, budget=0.75)
    assert seen == [(_cursor.page_motion_seed(4242, 3), 0.75)]


def test_the_budget_caps_the_whole_walk(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=0.3)
    play(page, clock, [["click", "#b"]])
    moves = [e for e in page.log if e.get("type") == "mousemove"]
    assert moves[-1]["t"] - moves[0]["t"] <= 0.3 + 0.05


# ═══════════════════════════════════════════════════════════════════════════
# 4. Who is asked to draw, and where the pointer stands
# ═══════════════════════════════════════════════════════════════════════════

def test_the_launch_prefs_carry_who_draws_and_leave_no_trace_in_the_profile():
    prefs = {SESSION_SEED_PREF: 7, MOTION_BUDGET_PREF: 1500, POINTER_PREF: True,
             "a.pref": 1}
    assert session_draws_pointer(prefs) is True
    rest, seed, budget = take_session_motion(prefs)
    assert rest == {"a.pref": 1} and seed == 7 and budget == 1.5
    assert session_draws_pointer({SESSION_SEED_PREF: 7}) is False


def test_the_pointer_starts_where_the_page_left_it(clock):
    """After an explicit move the next walk departs from there, not from the
    seeded start point of a page that never moved."""
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.op_mouse_move({"x": 640.0, "y": 360.0})
    clock.t += 0.01
    play(page, clock, [["click", "#b"]])
    first = [e for e in page.log if e.get("type") == "mousemove"][1]
    # the first waypoint is a few pixels from where the pointer stood
    assert abs(first["x"] - 640.0) < 60 and abs(first["y"] - 360.0) < 60


def test_the_viewport_follows_set_viewport_size(clock):
    page = build_page(clock, _boxes_of("x"), seed=4242, number=0, budget=1.5)
    page.send = lambda *a, **k: None
    page.op_set_viewport_size({"viewportSize": {"width": 800, "height": 600}})
    assert page.viewport_size == {"width": 800, "height": 600}
    assert page.pointer._bounds() == (799.0, 599.0)
