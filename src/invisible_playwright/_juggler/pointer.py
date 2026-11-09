"""The page's pointer, humanised ONCE, on the server.

⛔ WHY IT LIVES HERE AND NOT IN THE CLIENT. Until this module the walk of the
pointer onto an element belonged to the in-process Playwright client
(`_cursor.py` patched `Frame._click`, `Mouse.click`, `Mouse.wheel`, ... and sent
N `mouseMove` to the page before the action). A client that is not ours - a
Go, Node or stock Python Playwright talking to `launch_server` - sends ONE
`Frame.click` and the page saw one `mousemove`: measured with humanize=True on
a served browser. The rule is that a page sees EXACTLY the same through every
client, so the walk is drawn where every client's actions arrive. The in-process
client now sends the plain action and this module does the rest, which is also
why nothing here is asked of a client: there is no copy of it on that side to
get out of step.

WHAT IS HUMANISED, and what is not (this is the list the in-process client
used to answer, carried over unchanged):

* `Frame.click / dblclick / hover / tap / check / uncheck` - the approach to the
  element, the landing off its centre, the fidget that fills an idle gap;
* `Mouse.click / dblclick` - the stroke to the point, then the click;
* `Mouse.wheel` - a burst of detents with the hand creeping between them;
* NOT `Mouse.move`: an explicit move is the caller's. (The in-process client
  marks its own with `humanise`, see `_cursor._wrap_mouse_move`; a client that
  does not say so is answered with exactly the point it named.)
* NOT the actions of an ElementHandle (they name a node, never a selector, and
  the in-process client never walked onto them either) and NOT `dragAndDrop`,
  whose travel the core's `Actions` already draws.

HOW IT STAYS THE SAME AS THE CLIENT WAS. The stroke generator, the planners
(`_behaviour`) and the discipline events are delivered under (`Pacer`) are the
core's, unchanged; the page's motion seed is `_cursor.page_motion_seed(session
seed, page number)`, the budget is the session's `humanize=` cap. What changed
is only who calls them, and `tests/data/pointer_golden.json` holds what the
engine received from the client-side code of 5d9ad70 for the same seed, page
and targets: this module must reproduce it to the last coordinate and
millisecond.

⛔ NOTHING HERE MAY FAIL AN ACTION THAT WOULD HAVE SUCCEEDED. A probe that finds
the element gone, a planner that raises, a viewport that cannot be read: the
action runs exactly as the caller wrote it. Only a broken install says so, once
per process.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from invisible_core.juggler import (
    DONE, SLEEP, Ev as _Ev, Pacer, PointerPersona, Step,
    clamp_to_viewport as _clamp, fine_timer as _fine_timer,
    fit_timeline as _fit_timeline, initial_pointer, landing_point,
    plan_aimless_move, plan_approach, plan_idle, plan_scroll,
    steps_from_waypoints, tail_within,
)
from invisible_core.juggler.connection import ReplyTimeout
from invisible_core.juggler.deadline import Deadline
from invisible_core.juggler.injected import ContextDestroyed, EvaluationError
from invisible_core.seedmix import sub_seed

from .. import _cursor
from .dispatcher import ProtocolException

# Below this much wall clock since the last event we do not fidget at all: two
# actions back to back are one continuous piece of work, and a hand does not
# stop to fiddle between them.
_IDLE_MIN_GAP_MS = 400.0
# ...and above this we stop pretending we know what happened. Half a minute of
# planned idle is plenty to draw a plausible tail from.
_IDLE_MAX_GAP_MS = 45_000.0
# Share of the movement budget an action may spend on the fidget that precedes
# it. The rest belongs to the approach, which is the part that has to work.
_IDLE_BUDGET_FRAC = 0.35

# Used only when the page will not tell us its size.
_FALLBACK_VIEWPORT = (1280.0, 720.0)

# Landing off-centre hands the automation layer a click position we chose. If
# that position turns out not to be actionable we retry with the caller's own
# arguments, so the first attempt is given a bounded slice of the time rather
# than the caller's whole timeout.
_LANDING_ATTEMPT_S = 5.0
_LANDING_MIN_BOX_PX = 12.0
# How far from the centre of a control the landing point may stray, as a
# fraction of the box: sigma of the draw and the hard clip. Tighter than the
# planner's own default because this point is also the click position.
_LANDING_SPREAD = 0.14
_LANDING_KEEP = 0.26

#: What a probe of the page can legitimately answer while we are only AIMING:
#: the element went away, the frame navigated, the page closed.
_PROBE_ERRORS = (ContextDestroyed, EvaluationError, ReplyTimeout,
                 ProtocolException, TimeoutError)


# ── the clock ───────────────────────────────────────────────────────────────
#
# Injectable so the delivered-vs-planned timing can be asserted without a
# browser and without a test that takes as long as the schedule it checks.

class _RealClock:
    __slots__ = ()

    @staticmethod
    def now() -> float:
        return time.perf_counter()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


_CLOCK: Any = _RealClock()


# ── timelines ───────────────────────────────────────────────────────────────

def _timeline(steps: Iterable[Any]) -> List[_Ev]:
    """Turn a ``_behaviour`` Step list (relative delays) into absolute times."""
    out: List[_Ev] = []
    t = 0.0
    for s in steps:
        t += max(0.0, float(s.delay_ms))
        if s.kind == "wait":  # pure elapsed time: nothing to dispatch
            continue
        out.append(_Ev(t, s.x, s.y, "wheel" if s.kind == "wheel" else "move",
                       getattr(s, "dx", 0.0), getattr(s, "dy", 0.0)))
    return out


def _normalise_waypoints(raw: Iterable[Any], max_seconds: float) -> List[Tuple[float, float, float]]:
    """Reduce whatever the generator returns to ``(x, y, delay_seconds)``.

    Three shapes are accepted: a waypoint object carrying ``x`` / ``y`` /
    ``dt_ms``, an ``(x, y, delay_seconds)`` triple, and a bare ``(x, y)`` pair
    whose pacing is then spread evenly over the cap.

    The cap from ``humanize=<seconds>`` is applied through
    ``fit_timeline``, so a tighter cap makes the movement shorter AND coarser
    rather than merely denser.
    """
    points: List[Tuple[float, float, float]] = []
    for p in raw:
        x = getattr(p, "x", None)
        if x is not None and hasattr(p, "y"):
            dt_ms = getattr(p, "dt_ms", None)
            delay = float(dt_ms) / 1000.0 if dt_ms is not None else 0.0
            points.append((float(x), float(p.y), max(0.0, delay)))
            continue
        seq = tuple(p)
        if len(seq) >= 3:
            points.append((float(seq[0]), float(seq[1]), max(0.0, float(seq[2]))))
        else:
            points.append((float(seq[0]), float(seq[1]), -1.0))
    if not points:
        return []
    if all(d <= 0.0 for _, _, d in points):
        # Every waypoint at the same instant is not pacing, it is the absence
        # of pacing - and dispatching it would ask for the whole movement in
        # one tick. Treated exactly like the bare (x, y) case below.
        points = [(x, y, -1.0) for x, y, _ in points]
    if points[0][2] < 0:  # bare (x, y): no pacing supplied, spread the cap
        even = max_seconds / len(points)
        points = [(x, y, even) for x, y, _ in points]

    evs: List[_Ev] = []
    t = 0.0
    for x, y, d in points:
        t += d * 1000.0
        evs.append(_Ev(t, x, y))
    evs = _fit_timeline(evs, max_seconds)

    out: List[Tuple[float, float, float]] = []
    prev = 0.0
    for ev in evs:
        out.append((ev.x, ev.y, (ev.t_ms - prev) / 1000.0))
        prev = ev.t_ms
    return out


def _drive(evs: Sequence[_Ev], emit_move: Callable[[float, float], Any],
           emit_wheel: Optional[Callable[[float, float], Any]] = None, *,
           emit_last: bool = True,
           origin: Optional[Tuple[float, float]] = None) -> int:
    """The synchronous driver of `Pacer`. Returns events sent.

    The rules it obeys - absolute deadlines from one ``t0``, drop rather than
    send late while the drop stays within reach, never two events in one
    instant - are the pacer's, the core's, and are the same ones the client's
    asynchronous driver obeyed. This supplies a clock, a way of waiting and
    where the pointer starts from; it makes no decision the pacer has not made.
    """
    if not evs:
        return 0
    clock = _CLOCK
    pacer = Pacer(evs, emit_last=emit_last, origin=origin)
    with _fine_timer():
        while True:
            what, arg = pacer.step(clock.now())
            if what == DONE:
                break
            if what == SLEEP:
                clock.sleep(arg)
                continue
            if arg.is_wheel:
                if emit_wheel is None:
                    pacer.dropped()  # nothing here can carry a notch
                    continue
                emit_wheel(arg.dx, arg.dy)
            else:
                emit_move(arg.x, arg.y)
            pacer.emitted(clock.now())
    return pacer.delivered


# ── one page's hand ─────────────────────────────────────────────────────────

class _Hand:
    """State of one page's pointer: where it is, and how it likes to move."""

    __slots__ = ("seed", "max_seconds", "_profile", "persona", "x", "y",
                 "busy", "action", "last_event_at", "synced")

    def __init__(self, seed: int, max_seconds: float, factory: Callable[..., Any],
                 synced: Any) -> None:
        self.seed = int(seed)
        self.max_seconds = float(max_seconds)
        self._profile = _cursor._build_profile(factory, self.seed, self.max_seconds)
        self.persona = PointerPersona.from_seed(self.seed)
        self.x: Optional[float] = None
        self.y: Optional[float] = None
        self.busy = False
        # Counts actions, so every draw that has to differ between two actions
        # of one session (the landing point, the fidget) has something to vary
        # on while staying reproducible from the seed.
        self.action = 0
        self.last_event_at: Optional[float] = None
        #: The engine-side pointer position (`Actions.position`, the object
        #: itself) as this hand last knew it. Anything else moving the pointer
        #: - an explicit `mouse.move`, the click an action ends with - replaces
        #: it with a new object, and the hand then stands where the pointer
        #: really is.
        self.synced = synced

    def start_point(self, width: Optional[float], height: Optional[float]) -> Tuple[float, float]:
        """Where the pointer is before this page's first movement.

        Never the origin, and never a constant: a session whose first movement
        departs from (0, 0) is announcing that it had no pointer state at all
        before it started, which no real navigation produces.
        """
        w, h = width, height
        if not w or not h:
            _cursor._warn_once(
                "no-viewport",
                "a page reported no viewport size; the cursor start position "
                "falls back to a nominal %dx%d viewport. Coordinates are still "
                "seeded, but they are not derived from the real window."
                % (int(_FALLBACK_VIEWPORT[0]), int(_FALLBACK_VIEWPORT[1])),
            )
            w, h = _FALLBACK_VIEWPORT
        fn = getattr(self._profile, "start_point", None)
        if callable(fn):
            try:
                px, py = fn(w, h)
                return float(px), float(py)
            except (TypeError, ValueError, ArithmeticError) as exc:
                _cursor._warn_once(
                    "bad-start-point",
                    "the motion generator's start_point() failed (%s: %s); "
                    "using the behaviour planner's instead."
                    % (type(exc).__name__, exc),
                )
        return initial_pointer(self.seed, (float(w), float(h)))

    def path(self, fx: float, fy: float, tx: float, ty: float) -> List[Tuple[float, float, float]]:
        raw = self._profile.path(fx, fy, tx, ty)
        return _normalise_waypoints(raw, self.max_seconds)

    def renderer(self, bounds: Tuple[float, float]) -> Callable[..., List[Any]]:
        """A ``_behaviour`` renderer backed by the stroke generator.

        This is what keeps ONE stroke model in the product: the macro planner
        decides that a movement happens and where it goes, and every stroke it
        asks for is drawn by the generator.
        """
        profile = self._profile
        nominal = self.persona.sample_ms

        def render(start: Tuple[float, float], end: Tuple[float, float], *,
                   kind: str, lead_ms: float, bounds: Any = bounds,
                   target_w: Optional[float] = None) -> List[Any]:
            try:
                raw = list(profile.path(start[0], start[1], end[0], end[1],
                                        target_w=target_w))
            except TypeError:
                # A generator predating the target_w argument. Accepting it and
                # dropping it is better than refusing to move: pacing degrades
                # to the per-seed default, everything else still works.
                raw = list(profile.path(start[0], start[1], end[0], end[1]))
            if raw and not any(float(getattr(p, "dt_ms", 0.0) or 0.0)
                               if hasattr(p, "dt_ms") else
                               (float(p[2]) if len(tuple(p)) > 2 else 0.0)
                               for p in raw):
                # A generator that paces nothing has not said "instantly", it
                # has said nothing. Fall back to the session's own sampling
                # interval rather than asking for the whole stroke in one tick.
                raw = [(float(getattr(p, "x", None) if hasattr(p, "x") else p[0]),
                        float(getattr(p, "y", None) if hasattr(p, "y") else p[1]),
                        nominal)
                       for p in raw]
            return steps_from_waypoints(
                raw, kind=kind, lead_ms=lead_ms, bounds=bounds
            )

        return render

    def rng(self, tag: str) -> random.Random:
        """A per-(page, action, purpose) stream. Reproducible from the seed."""
        return random.Random(
            sub_seed(self.seed, "%s:%d" % (tag, self.action))
        )


@dataclass
class _Aim:
    """Where this action is going, and how the caller must be adjusted for it.

    ``override`` is the position the action should run with so that it clicks
    the point we walked to rather than the element's centre; None means the
    action is left exactly as the caller wrote it.
    """

    box: Dict[str, float]
    landing: tuple
    override: Optional[Dict[str, float]]

    @property
    def rect(self) -> tuple:
        return (self.box["x"], self.box["y"],
                self.box["width"], self.box["height"])


def _inside(x: float, y: float, w: Optional[float], h: Optional[float]) -> bool:
    if not w or not h:
        return True
    return 0.0 <= x < float(w) and 0.0 <= y < float(h)


class Pointer:
    """The hand of one page: what an action does with the pointer before it
    acts. Inert (`active` False) when the session did not ask for it, which
    turns every method into the plain action."""

    def __init__(self, actions: Any, injected: Any, number: int,
                 session_seed: Optional[int], budget_s: Optional[float],
                 viewport: Callable[[], Any], *, draws: bool = True) -> None:
        self.actions = actions
        self.injected = injected
        self._viewport_of = viewport
        self.hand: Optional[_Hand] = None
        if not draws or session_seed is None or not budget_s:
            return
        factory = _cursor._motion_factory()
        if factory is None:
            return
        seed = _cursor.page_motion_seed(session_seed, number)
        try:
            self.hand = _Hand(seed, budget_s, factory,
                              getattr(actions, "position", None))
        except Exception as exc:  # noqa: BLE001 - a broken generator must not break clicking
            _cursor._warn_once(
                "profile-construction",
                "the cursor motion generator could not be constructed (%s: %s); "
                "this session will not humanise its pointer. This is a broken "
                "install, not a configuration." % (type(exc).__name__, exc),
            )

    @property
    def active(self) -> bool:
        return self.hand is not None

    # -- geometry ----------------------------------------------------------

    def _viewport(self) -> Tuple[Optional[float], Optional[float]]:
        size = self._viewport_of()
        if isinstance(size, dict):
            return size.get("width"), size.get("height")
        return None, None

    def _bounds(self) -> Tuple[float, float]:
        """Viewport size to plan inside, falling back to a nominal one."""
        w, h = self._viewport()
        if not w or not h:
            return _FALLBACK_VIEWPORT
        return float(w) - 1.0, float(h) - 1.0

    def _here(self) -> Tuple[float, float]:
        """Current pointer position, placing it first if it has never moved."""
        hand = self.hand
        now = getattr(self.actions, "position", None)
        if now is not hand.synced:
            # Something else moved the pointer since we last did.
            hand.synced = now
            hand.x, hand.y = float(now[0]), float(now[1])
        elif hand.x is None or hand.y is None:
            w, h = self._viewport()
            hand.x, hand.y = hand.start_point(w, h)
        return float(hand.x), float(hand.y)

    def _emit_move(self, x: float, y: float) -> None:
        hand = self.hand
        self.actions.move(x, y)
        hand.synced = getattr(self.actions, "position", None)

    # -- the probes --------------------------------------------------------

    def _element_box(self, frame_id: str, selector: str) -> Tuple[Optional[str], Optional[dict]]:
        """Resolve the element and its box, or (None, None). Never raises.

        Deliberately non-blocking and deliberately forgiving: this is a hint
        used to aim a mouse path, never a correctness step. It resolves the
        element if it is already there and gives up otherwise, so it can
        neither add a wait to a passing call nor invent an exception on a
        failing one - the action itself still does its own waiting and raises
        its own errors.
        """
        try:
            handle = self.injected.query_selector(frame_id, selector)
        except _PROBE_ERRORS:
            return None, None  # gone, navigated, closed, or not a selector we know
        if not handle:
            return None, None
        try:
            box = self.injected.bounding_box(frame_id, handle)
        except _PROBE_ERRORS:
            box = None
        if not box:
            self._dispose(frame_id, handle)
            return None, None
        return handle, box

    def _dispose(self, frame_id: str, handle: str) -> None:
        try:
            self.injected.dispose(frame_id, handle)
        except _PROBE_ERRORS:
            pass  # the page or the frame is already gone; nothing to release

    def _hits(self, frame_id: str, handle: str, x: float, y: float) -> bool:
        """Does (x, y), in the same main-frame space as the box, land on this
        element?

        A bounding box is not the element. An inline link that wraps across
        two lines, a rotated control, a rounded button: all of them have points
        inside their box that belong to something else. The centre is checked
        by the automation layer itself; a point we chose has to be checked by
        us, or we would be turning working clicks into hit-target failures.

        ⛔ THE ACTION'S OWN QUESTION (`Actions.hit_target`): the same frame
        shift, the same shadow-aware test, so the cursor accepts exactly the
        points the action itself would accept.
        """
        try:
            return self.actions.hit_target(frame_id, handle, (x, y)) == "done"
        except _PROBE_ERRORS:
            return False

    def _choose_landing(self, frame_id: str, selector: str,
                        position: Optional[dict]) -> Optional[_Aim]:
        """Pick the point on the element this action will land on."""
        hand = self.hand
        handle, box = self._element_box(frame_id, selector)
        if handle is None or box is None:
            return None
        try:
            if isinstance(position, dict) and "x" in position and "y" in position:
                # The caller aimed explicitly. Honour it exactly and override
                # nothing: choosing our own point here would silently move a
                # click the caller had every reason to think was pinned.
                return _Aim(box, (box["x"] + float(position["x"]),
                                  box["y"] + float(position["y"])), None)

            landing = landing_point(
                (box["x"], box["y"], box["width"], box["height"]),
                hand.rng("cursor:landing"),
                spread=_LANDING_SPREAD, keep=_LANDING_KEEP,
            )
            if (
                _cursor.landing_enabled()
                and box["width"] >= _LANDING_MIN_BOX_PX
                and box["height"] >= _LANDING_MIN_BOX_PX
                and self._hits(frame_id, handle, landing[0], landing[1])
            ):
                return _Aim(box, landing, {"x": landing[0] - box["x"],
                                           "y": landing[1] - box["y"]})
            # The off-centre point missed the element (it is not a rectangle,
            # or it is too small for the spread to be safe). Fall back to the
            # centre and do NOT override: an override naming a point that
            # misses would turn a working click into a failing one.
            return _Aim(box, (box["x"] + box["width"] / 2.0,
                              box["y"] + box["height"] / 2.0), None)
        finally:
            self._dispose(frame_id, handle)

    # -- travelling --------------------------------------------------------

    def _run_steps(self, steps: Sequence[Any], emit_wheel: Optional[Callable] = None,
                   *, budget_s: float, emit_last: bool = True) -> None:
        """Dispatch a planned ``_behaviour`` timeline, inside a time budget."""
        hand = self.hand
        evs = _fit_timeline(_timeline(steps), budget_s)
        if not evs:
            return

        def move(x: float, y: float) -> None:
            self._emit_move(x, y)
            hand.x, hand.y = x, y

        _drive(evs, move, emit_wheel, emit_last=emit_last,
               origin=(hand.x, hand.y))
        if not emit_last and evs:
            hand.x, hand.y = evs[-1].x, evs[-1].y
        hand.last_event_at = _CLOCK.now()

    def _idle_budget_ms(self) -> float:
        """How much of the movement budget this action may spend on a fidget.

        Zero when the previous event was moments ago: two calls back to back
        are one piece of work, and inserting a fidget between them would be
        both a lie and a delay.
        """
        hand = self.hand
        if hand.last_event_at is None:
            return 0.0
        gap_ms = (_CLOCK.now() - hand.last_event_at) * 1000.0
        if gap_ms < _IDLE_MIN_GAP_MS:
            return 0.0
        return min(hand.max_seconds * _IDLE_BUDGET_FRAC * 1000.0, gap_ms * 0.5)

    def _plan_fidget(self) -> List[Any]:
        """What the hand did while the caller's script was busy elsewhere.

        We cannot dispatch into the past, so the idle period is planned in full
        and only its tail is kept (`tail_within`). The alternative, inventing a
        short episode from scratch, would have a distribution of its own that no
        amount of seeding makes match the planner's.
        """
        hand = self.hand
        budget = self._idle_budget_ms()
        if budget <= 0.0:
            return []
        gap_ms = min((_CLOCK.now() - (hand.last_event_at or _CLOCK.now())) * 1000.0,
                     _IDLE_MAX_GAP_MS)
        bounds = self._bounds()
        here = self._here()
        gate = hand.rng("cursor:fidget-gate")
        if gate.random() < hand.persona.aimless_rate:
            # A movement that ends nowhere in particular. If every movement of
            # a session terminates on a control then the SET OF ENDPOINTS is a
            # signature on its own, whatever the paths between them look like.
            steps = plan_aimless_move(
                hand.seed, hand.persona, here, bounds,
                nonce=hand.action, render=hand.renderer(bounds),
            )
            return tail_within(steps, budget, here, bounds)
        steps = plan_idle(
            hand.seed, hand.persona, here, bounds, gap_ms,
            nonce=hand.action, render=hand.renderer(bounds),
        )
        return tail_within(steps, budget, here, bounds)

    def _walk_onto(self, aim: _Aim) -> None:
        """Spend the time budget getting the pointer to ``aim``."""
        hand = self.hand
        bounds = self._bounds()
        budget = hand.max_seconds

        fidget = self._plan_fidget()
        if fidget:
            # The fidget spends its own share of the budget and hands the rest
            # to the approach, which is the part that has to work. The floor
            # keeps a pathological share from leaving the approach no time.
            spent = min(sum(s.delay_ms for s in fidget) / 1000.0,
                        budget * _IDLE_BUDGET_FRAC)
            self._run_steps(fidget, budget_s=spent)
            budget = max(budget - spent, 0.2)

        steps = plan_approach(
            hand.seed, hand.persona, self._here(), aim.rect, bounds,
            nonce=hand.action, landing=aim.landing,
            render=hand.renderer(bounds),
        )
        if steps:
            # ``emit_last=False``: the final hop onto the landing point is left
            # to the action itself. That is the whole hover fix restated - the
            # only event inside the hit-target window is the automation layer's
            # own move, and it is on target - and it also removes what would
            # otherwise be a zero-displacement event immediately before every
            # mousedown, since the action moves to the point we just landed on.
            self._run_steps(steps, budget_s=budget, emit_last=False)
        else:
            # Sub-pixel approach: the pointer is already standing on the
            # target. Emitting a move to where it already is would be a
            # zero-displacement event, so the action's own move is left to do
            # the whole of it.
            hand.x, hand.y = aim.landing

    # -- the entry points --------------------------------------------------

    def approach(self, frame_id: str, selector: str, position: Optional[dict],
                 trial: bool = False) -> Optional[Dict[str, float]]:
        """Walk the pointer onto the element this action is about to touch.

        Returns the `position` the action should run with, or None.

        ⛔ A TRIAL RUN performs the actionability checks without touching
        anything, so it must not move the pointer either. It is honoured in
        `Actions._retry`, where actionability lives; this only keeps the
        pointer still while that happens.
        """
        hand = self.hand
        if hand is None or hand.busy or trial:
            return None
        hand.busy = True
        hand.action += 1
        try:
            aim = self._choose_landing(frame_id, selector, position)
            if aim is None:
                return None
            w, h = self._viewport()
            if not _inside(aim.landing[0], aim.landing[1], w, h):
                # The element is off-screen; the action is about to scroll it
                # into view and we would be aiming at where it used to be. Let
                # the action place the pointer itself this once.
                return None
            self._walk_onto(aim)
            return aim.override
        finally:
            hand.busy = False

    def act(self, frame_id: str, selector: str, position: Optional[dict],
            timeout: Optional[float], run: Callable[[Optional[dict], Optional[float]], Any],
            *, trial: bool = False) -> Any:
        """Run a pointer action on an element the way a hand would.

        `run(position, timeout)` performs the plain action with those two
        arguments. The approach comes first; if it chose a landing point the
        action is told to click it, within a bounded slice of the caller's
        time, and if that point is not actionable the caller's own call is made
        with what is left of its time, so a call that would have succeeded
        still does.
        """
        override = None
        try:
            override = self.approach(frame_id, selector, position, trial)
        except Exception as exc:  # noqa: BLE001 - motion never fails an action
            _cursor._warn_once(
                "approach-failed",
                "the cursor approach raised (%s: %s); the action itself is "
                "unaffected and ran normally. Please report this."
                % (type(exc).__name__, exc),
            )
        if not override:
            return run(position, timeout)
        deadline = Deadline(timeout)
        first = (_LANDING_ATTEMPT_S if timeout is None
                 else min(_LANDING_ATTEMPT_S, timeout))
        try:
            return run(override, first)
        except TimeoutError as exc:
            _cursor._warn_once(
                "landing-retry",
                "an off-centre landing point was not actionable (%s); the "
                "action was retried with its own arguments. Set %s=off to "
                "disable off-centre landing."
                % (type(exc).__name__, _cursor.LANDING_ENV),
            )
        return run(position,
                   None if timeout is None else deadline.left(floor=0.05))

    def travel(self, x: float, y: float, what: str = "mouse.click") -> None:
        """Emit the intermediate waypoints of one plain stroke to (x, y), the
        endpoint itself left to the caller.

        Used before `Mouse.click`: the caller named the exact destination and
        the macro layer has nothing to add. The endpoint is the caller's to
        emit - it has to land on the exact coordinates it was given, unclamped -
        so this returns after waiting out the last waypoint's deadline without
        sending it.
        """
        hand = self.hand
        if hand is None or hand.busy:
            return
        hand.busy = True
        hand.action += 1
        try:
            self._travel(x, y)
        except Exception as exc:  # noqa: BLE001
            _cursor._warn_once(
                "move-failed",
                "the cursor path raised during %s (%s: %s); the action "
                "itself is unaffected. Please report this."
                % (what, type(exc).__name__, exc),
            )
        finally:
            hand.busy = False

    def _travel(self, to_x: float, to_y: float) -> None:
        hand = self.hand
        w, h = self._viewport()
        hand.x, hand.y = self._here()
        try:
            waypoints = hand.path(hand.x, hand.y, to_x, to_y)
        except Exception as exc:  # noqa: BLE001 - never let motion break an action
            _cursor._warn_once(
                "path-failed",
                "the cursor motion generator raised while planning a path "
                "(%s: %s); this movement is not humanised. This is a broken "
                "install, not a configuration." % (type(exc).__name__, exc),
            )
            waypoints = []

        evs: List[_Ev] = []
        t = 0.0
        end_key = (round(to_x), round(to_y))
        # ⛔ THE FIRST WAYPOINT OF A STROKE IS WHERE IT STARTS. When the engine
        # has already been told the pointer is on that pixel (a move that
        # stopped there, then a pause), sending it again is a zero-displacement
        # `mousemove` that no mouse reports; the stroke begins at its first
        # real step. A pointer the engine has never been told about (the
        # page's first movement, from the seeded start point) is not "there"
        # for the page, so its first waypoint is a real move and goes out.
        known = getattr(self.actions, "position", None)
        at_start = (None if known is None
                    else (round(known[0]), round(known[1])))
        for px, py, delay in waypoints:
            t += delay * 1000.0
            cx, cy = _clamp(px, py, w, h)
            if (not evs and at_start is not None
                    and (round(cx), round(cy)) == at_start
                    and (round(hand.x), round(hand.y)) == at_start):
                continue  # still standing on the start pixel: nothing moved
            # Never pre-empt the endpoint - it is the caller's to emit, once.
            #
            # Nothing else here filters the stream (the start waypoint above is not
            # a repeat of a shape, it is the position itself). Whether two consecutive
            # events may share a device pixel is a question about the SHAPE of
            # a path, and the shape belongs to the generator: a dispatcher that
            # silently removed every repeat would make "the duplicate fraction
            # is exactly zero" true no matter what the generator did.
            if (round(cx), round(cy)) == end_key:
                continue
            evs.append(_Ev(t, cx, cy))
        # The endpoint closes the schedule so its deadline is waited out even
        # though the caller is the one that emits it.
        evs.append(_Ev(max(t, evs[-1].t_ms if evs else 0.0), to_x, to_y))

        def emit(x: float, y: float) -> None:
            self._emit_move(x, y)
            hand.x, hand.y = x, y

        _drive(evs, emit, emit_last=False, origin=(hand.x, hand.y))
        hand.x, hand.y = to_x, to_y
        hand.last_event_at = _CLOCK.now()

    def click_at(self, x: float, y: float, run: Callable[[], Any]) -> Any:
        """`Mouse.click`: the stroke, then the click `run` makes.

        ⛔ THE ENDPOINT IS EMITTED ONCE. The stroke stops short of it
        (`travel`), and `run` is `Actions.click_at`, whose own approach is the
        one `mousemove` onto the exact point before the press. This used to send
        the endpoint as a move of its own first (as the client's `Mouse._click`
        did), so the engine got two `mousemove` with the same coordinates at the
        same instant: no mouse reports that, and a page can count it. A hand
        arrives at the button in one last motion and presses; so the last
        waypoint is the click's own approach, and nothing else lands on the
        point before the press. Every transport comes here (`op_mouse_click`),
        so the sequence is one.
        """
        if self.hand is None:
            return run()
        self.travel(x, y)
        try:
            return run()
        finally:
            self.hand.synced = getattr(self.actions, "position", None)

    def wheel(self, dx: float, dy: float) -> bool:
        """`Mouse.wheel` as a burst of detents with the hand on the mouse.
        True when it was done here; False means the caller sends the plain
        wheel event."""
        hand = self.hand
        if hand is None or hand.busy:
            return False
        try:
            hand.busy = True
            hand.action += 1
            try:
                plan = self._scroll_plan(float(dx), float(dy))
                if plan:
                    self._run_steps(plan, self.actions.wheel,
                                    budget_s=hand.max_seconds)
                    return True
            finally:
                hand.busy = False
        except Exception as exc:  # noqa: BLE001
            _cursor._warn_once(
                "wheel-failed",
                "the scroll planner raised (%s: %s); the scroll itself is "
                "unaffected. Please report this." % (type(exc).__name__, exc),
            )
        return False

    def _scroll_plan(self, delta_x: float, delta_y: float) -> List[Any]:
        """A wheel burst carrying exactly (delta_x, delta_y), with the hand on it.

        One protocol-level wheel event of 900 px is not a scroll a person
        performs; a notched wheel produces detents. The number of detents is
        bounded by the movement budget, and the deltas are rescaled so the burst
        sums EXACTLY to what the caller asked for - a scroll that lands
        somewhere else is a broken call, not a subtle one.
        """
        hand = self.hand
        total = abs(delta_x) + abs(delta_y)
        if total < 1.0:
            return []
        notch = 100.0
        max_ticks = max(1, int(hand.max_seconds * 1000.0 / 90.0))
        ticks = int(max(1, min(round(total / notch), max_ticks, 24)))
        if ticks < 2:
            return []
        bounds = self._bounds()
        steps = plan_scroll(
            hand.seed, hand.persona, self._here(), bounds,
            ticks=ticks, tick_dy=1.0, nonce=hand.action,
            render=hand.renderer(bounds),
        )
        shares = [s.dy for s in steps if s.kind == "wheel"]
        tot = sum(shares)
        if not shares or tot <= 0:
            return []
        out: List[Any] = []
        used_x = used_y = 0.0
        seen = 0
        for s in steps:
            if s.kind != "wheel":
                out.append(s)
                continue
            seen += 1
            if seen == len(shares):  # the last notch absorbs the rounding
                dx, dy = delta_x - used_x, delta_y - used_y
            else:
                frac = s.dy / tot
                dx, dy = delta_x * frac, delta_y * frac
            used_x += dx
            used_y += dy
            out.append(Step(x=s.x, y=s.y, delay_ms=s.delay_ms,
                            kind="wheel", dx=dx, dy=dy))
        return out

    def move(self, x: float, y: float) -> None:
        """An explicit `Mouse.move` the in-process client asked to be walked.
        The endpoint is emitted by the caller, exactly."""
        if self.hand is not None:
            self.travel(x, y, "mouse.move")
