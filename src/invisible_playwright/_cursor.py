"""Wiring that routes pointer movement through the wrapper's own generator.

WHY THE MOTION IS GENERATED HERE AND NOT IN THE BROWSER
-------------------------------------------------------
Until now every ``mousemove`` was expanded inside the browser, by code that
ships with the binary.  That has three consequences we want to be rid of:

* The trajectory is built from compiled-in constants, so its shape is an
  invariant across every session of every install.  Generated here it derives
  from the session seed instead, which means two users - and two sessions of
  the same user - do not move the same way, and a replayed seed replays the
  cursor exactly like it replays the fingerprint.
* The expansion happened *inside* the browser's own mouse-dispatch entry
  point, which is what breaks hover (see HOVER, below).
* Changing anything about it required rebuilding and re-releasing a browser.

Nothing is lost by moving it.  The in-browser expansion dispatched its
waypoints with exactly the same arguments the ordinary ``mouse.move`` path
uses - same pressure, same input source, same pointer id, same synthesized
flags - so a waypoint driven from here is indistinguishable from one the
binary used to generate itself.

HOW IT REACHES ``page.click`` WITHOUT A NEW API
-----------------------------------------------
This package's promise is that it *is* Playwright: same objects, same
methods, no rewrite.  A ``page.move_like_a_human()`` helper would break that
promise - it only helps code that was written after we shipped it, and every
existing script keeps teleporting.  So there is no new API.  Instead we wrap
the *one* internal funnel every pointer action already goes through.

In Playwright's Python bindings, ``Page.click``, ``Frame.click`` and
``Locator.click`` all end up in the same coroutine on the implementation
object (``Frame._click``); ``hover`` / ``dblclick`` / ``tap`` / ``check`` /
``uncheck`` funnel the same way, and ``Page.set_checked`` funnels into
``check`` / ``uncheck``.  Wrapping those six coroutines therefore covers the
public surface for the sync API *and* the async API at once, because the sync
API is a greenlet driver over the very same implementation objects.  User code
that already says ``page.click("#buy")`` gets the motion with no edit.
``Mouse.move`` and ``Mouse.wheel`` are wrapped for the same reason.

The wrappers are installed on the class, once per process, but they are inert
unless the page belongs to a session that asked for them: the lookup is a
weak-keyed registry, and a page from a plain ``sync_playwright()`` browser in
the same process finds nothing and is delegated to untouched.

HOVER
-----
The in-browser expansion ran *inside* the mouse-dispatch entry point, i.e.
after the automation layer had already installed its hit-target interceptor
for this action.  The interceptor's job is to confirm that the point being
clicked really lands on the element it was told to click.  It sees the first
event of the action, and the first event of the action was the first waypoint
of the curve, which sits far away from the target - usually over ``<html>``.
The interceptor concludes the element is covered by another element and the
action fails.  On Windows that is roughly three hover calls in four.

Generating here fixes it by construction, not by tuning: the whole approach
runs to completion *before* we call the original coroutine, so the interceptor
is installed after the cursor has already arrived.  The only event it sees is
the single, exact move the automation layer makes itself, onto the point it
computed.  There is no waypoint left inside the checked window for it to
mistake for an obstruction.

THE THREE LAYERS, AND WHO OWNS WHAT
------------------------------------
``_motion``     the shape and the pacing of ONE stroke, A to B.
``_behaviour``  what an action is made of and what happens between actions:
                how many strokes, where they aim inside a control, the
                overshoot and the correction, the fidget while nothing is
                happening, the pointer creep during a scroll.  Pure: it
                returns a timeline and never touches a clock.
this module     everything with a side effect.  It decides WHEN the macro
                layer runs, walks the timeline against a real clock, keeps
                coordinates inside the viewport (a waypoint outside it is not
                merely dropped by the browser, it parks the cursor at the
                origin), and guarantees that none of the above can fail a call
                that would otherwise have succeeded.

``_motion`` is an optional dependency and its absence is survivable: without
it a session hands the job back to the browser's own expansion and says so.
``_behaviour`` is not optional - it decides where an action aims - so it is
imported plainly. It is pure stdlib and in this same package, so an install in
which it fails to import is an install in which nothing else works either.

WHEN THE MACRO LAYER RUNS - AND WHY IT IS NOT A BACKGROUND TASK
----------------------------------------------------------------
The honest way to make a pointer move while the caller's script is doing
nothing would be a background task dispatching pointer events on its own.  We
do not do that, deliberately.  The caller's script is single-threaded by
assumption; a second writer on the same page would interleave a ``mousemove``
into the middle of somebody's ``click`` - precisely the hit-target failure
described under HOVER - and it would keep sending input to a page whose script
believes nothing is touching it.  A stealth feature that intermittently breaks
clicks is not a stealth feature.

So every event this module sends is sent from inside a call the user made, and
the macro layer is folded into the head of the NEXT action instead:

* the wall clock since the last dispatch is real, and it is the input the idle
  planner wants.  We plan the whole of that idle period, then dispatch only
  the tail of it that fits the movement budget (:func:`_behaviour.tail_within`).
  What reaches the wire is therefore the last fraction of a second of a
  fidget that is planned as if it had been running all along;
* the approach to a control is planned by :func:`_behaviour.plan_approach`, so
  an action is one to three submovements with a dwell between them, ending on
  a point inside the element that is not its centre;
* ``mouse.wheel`` becomes a burst of detents with the pointer creeping between
  them, which is what a hand on a wheel does.

The whole of it is bounded by ``humanize=<seconds>``: one action never spends
more than that budget in motion, and the budget is enforced by DROPPING
waypoints, never by emitting them faster than a device could report them.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import random
import sys
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from weakref import WeakKeyDictionary

from . import _behaviour

# The escape hatch. ``INVPW_CURSOR_ENGINE`` picks who generates the motion:
#   "python"  (default) - this module, seeded from the session seed
#   "binary"            - the browser's own expansion, i.e. the pre-existing
#                         behaviour, for anyone who was depending on it
#   "off"                - no humanisation at all, same as humanize=False
ENGINE_ENV = "INVPW_CURSOR_ENGINE"

ENGINE_PYTHON = "python"
ENGINE_BINARY = "binary"
ENGINE_OFF = "off"

# Opt-out for the one behaviour that changes what the automation layer itself
# does rather than only what happens before it: landing off the centre of an
# element (see ``_landing_override``).
LANDING_ENV = "INVPW_CURSOR_LANDING"

# Hard ceiling on how far a single approach may be stretched, so a pathological
# generator cannot hang a click. Mirrors the constructor's own default cap.
_DEFAULT_MAX_SECONDS = 1.5

# The fastest a pointer stream may be delivered, in milliseconds between
# events. A 1000 Hz gaming mouse reports every millisecond and the browser
# coalesces on top of that, so 4 ms is already the optimistic end of what
# hardware produces. It exists because the duration cap used to be honoured by
# SCALING the schedule: ``humanize=0.05`` on a 90-waypoint path asked for an
# event every 0.55 ms, i.e. 1800 Hz, which no device on earth reports and which
# is trivially measurable from the page. The cap is now honoured by dropping
# waypoints instead.

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

# Used only when the page will not tell us its size (see ``start_point``).
_FALLBACK_VIEWPORT = (1280.0, 720.0)

# Landing off-centre hands the automation layer a click position we chose. If
# that position turns out not to be actionable we retry with the caller's own
# arguments, so the first attempt is given a bounded slice of the time rather
# than the caller's whole timeout.
_LANDING_ATTEMPT_TIMEOUT_MS = 5000.0
_LANDING_MIN_BOX_PX = 12.0
# How far from the centre of a control the landing point may stray, as a
# fraction of the box: sigma of the draw and the hard clip. Tighter than the
# planner's own default because this point is also the click position.
_LANDING_SPREAD = 0.14
_LANDING_KEEP = 0.26


# ── warnings ────────────────────────────────────────────────────────────────
#
# A broken install and a page that simply had no element must not look the
# same. Everything that indicates the former says so, exactly once per process
# (a warning per click would be its own kind of breakage), and everything that
# indicates the latter stays silent.

_warned: set = set()


def _warn_once(key: str, message: str, category: type = RuntimeWarning) -> None:
    if key in _warned:
        return
    _warned.add(key)
    warnings.warn("invisible_playwright: " + message, category, stacklevel=3)


def _reset_warnings() -> None:
    """Test hook: forget which warnings have already been emitted."""
    _warned.clear()


# ── the _motion contract ────────────────────────────────────────────────────
#
# ``_motion`` supplies the stroke generator. It must expose a per-session
# class:
#
#     CursorMotion(seed: int)     - the shipped name
#     MotionProfile(seed, max_seconds) / new_motion(...) - also accepted, so
#         the generator can grow a duration argument without this file moving
#
# and the instance must expose:
#
#     .path(from_x, from_y, to_x, to_y) -> sequence of waypoints
#         A waypoint is either an object with ``x`` / ``y`` / ``dt_ms``, or a
#         plain ``(x, y, delay_seconds)`` triple, or a bare ``(x, y)`` pair.
#         The delay is the pause taken BEFORE emitting that waypoint.
#     .start_point(width, height) -> (x, y)      [optional]
#         Where the cursor sits before the session's first movement. Optional
#         because it is policy, not geometry; without it the macro layer places
#         it, since a cursor that is at the top-left corner at the start of
#         every session is an invariant shared by every install.
#
# Anything else is treated as "no generator available" and the session falls
# back to the browser's own expansion, which is always a working browser.
try:  # pragma: no cover - exercised by the import-failure test via monkeypatch
    from . import _motion as _motion_mod  # type: ignore[attr-defined]
except Exception as _motion_exc:  # noqa: BLE001 - a broken _motion must not break launching
    _motion_mod = None  # type: ignore[assignment]
    _MOTION_IMPORT_ERROR: Optional[BaseException] = _motion_exc
else:
    _MOTION_IMPORT_ERROR = None

#: ONE physical floor for the package, taken from where it is defined.
#: This was an independent literal 4.0 until 2026-07-26 - 250 Hz - while
#: _motion and _behaviour both reasoned from 125 Hz / 8 ms. Two numbers for one
#: physical fact is the shape this project keeps finding, and the looser one
#: silently won wherever it happened to be applied last.
#:
#: The fallback is only reached when _motion failed to import, in which case
#: the session is already falling back to the browser's own expansion; keeping
#: a floor here rather than None means _fit_timeline still refuses to emit a
#: rate no device produces.
MIN_EVENT_INTERVAL_MS = float(getattr(_motion_mod, "SAMPLE_FLOOR_MS", 8.0))     if _motion_mod is not None else 8.0


_MOTION_FACTORY_NAMES = ("CursorMotion", "MotionProfile", "new_motion")


def _motion_factory() -> Optional[Callable[..., Any]]:
    """Return the callable that builds a per-page stroke generator, or None."""
    mod = _motion_mod
    if mod is not None:
        for name in _MOTION_FACTORY_NAMES:
            factory = getattr(mod, name, None)
            if callable(factory):
                return factory
    detail = (
        f" ({type(_MOTION_IMPORT_ERROR).__name__}: {_MOTION_IMPORT_ERROR})"
        if _MOTION_IMPORT_ERROR is not None
        else ""
    )
    _warn_once(
        "no-motion",
        "no cursor motion generator found "
        "(invisible_playwright._motion.CursorMotion)" + detail + "; falling "
        "back to the browser's own motion for this process. This is a broken "
        "install, not a configuration.",
    )
    return None


def _build_profile(factory: Callable[..., Any], seed: int, max_seconds: float) -> Any:
    """Construct a profile, whether or not the generator wants the cap.

    The cap belongs to the public constructor (``humanize=<seconds>``), so the
    wrapper honours it either way: a generator that takes it applies it itself,
    one that does not gets its schedule fitted here.
    """
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):  # C-level or otherwise unintrospectable
        params = {}
    if "max_seconds" in params:
        return factory(seed=seed, max_seconds=max_seconds)
    return factory(seed)


def motion_available() -> bool:
    """True when a generator exists AND the bindings could be wrapped."""
    return _motion_factory() is not None and _ensure_patched()


def resolve_cursor_engine(humanize: Any) -> str:
    """Decide who generates the motion for a session.

    ``humanize`` keeps exactly the meaning it has always had on the public
    constructor: falsy disables humanisation, ``True`` enables it with the
    default cap, a number enables it with that cap in seconds. This function
    only decides WHERE the motion comes from, never WHETHER the user asked
    for it.
    """
    if not humanize:
        return ENGINE_OFF
    choice = (os.environ.get(ENGINE_ENV) or "").strip().lower()
    if choice in (ENGINE_BINARY, "juggler"):
        return ENGINE_BINARY
    if choice in (ENGINE_OFF, "none", "false", "0"):
        return ENGINE_OFF
    # "", "python", "wrapper" and anything unrecognised: prefer the Python
    # generator, but never at the price of a session that cannot move at all.
    return ENGINE_PYTHON if motion_available() else ENGINE_BINARY


def max_seconds_for(humanize: Any) -> float:
    """The motion-duration cap implied by ``humanize=``."""
    if humanize is True:
        return _DEFAULT_MAX_SECONDS
    try:
        value = float(humanize)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_SECONDS
    return value if value > 0 else _DEFAULT_MAX_SECONDS


def humanize_prefs(engine: str, humanize: Any) -> dict:
    """The browser prefs implied by the chosen engine.

    One definition for both the sync and the async launcher, because getting
    this wrong in one of them is a silent double expansion: the wrapper draws a
    path, and the browser then draws a path between each pair of our waypoints.
    """
    prefs: dict = {"stealthfox.humanize": engine == ENGINE_BINARY}
    if engine == ENGINE_BINARY:
        prefs["stealthfox.humanize.maxTime"] = str(max_seconds_for(humanize))
    return prefs


def landing_enabled() -> bool:
    """Whether an action may be told to click somewhere other than the centre."""
    return (os.environ.get(LANDING_ENV) or "").strip().lower() not in (
        "off", "0", "false", "no",
    )


# ── the clock ───────────────────────────────────────────────────────────────
#
# Injectable so the delivered-vs-planned timing can be measured, and asserted
# on, without a browser and without a test that takes as long as the schedule
# it is checking.

class _RealTimer:
    """Wall clock and a real sleep."""

    __slots__ = ()

    @staticmethod
    def now() -> float:
        return time.perf_counter()

    @staticmethod
    async def sleep(seconds: float) -> None:
        await asyncio.sleep(seconds)


_TIMER: Any = _RealTimer()

# Windows quantises a waited-on timer to the system tick, which is 15.6 ms by
# default: a 12 ms sleep comes back at 16.7 ms (measured, p50), and there is no
# scheduling policy that can recover a resolution the platform will not give.
# Asking for a 1 ms period brings the same sleep back at 12.5 ms. It is
# reference-counted by the OS and released as soon as the movement is over, and
# it is the difference between dispatching the plan and dispatching a rounded
# copy of it - which matters here because per-seed differentiation is carried
# largely by timing.
TIMER_ENV = "INVPW_CURSOR_TIMER"

_timer_period_depth = 0
_winmm: Any = None
_winmm_tried = False


def _timer_resolution_available() -> Any:
    global _winmm, _winmm_tried
    if _winmm_tried:
        return _winmm
    _winmm_tried = True
    if (os.environ.get(TIMER_ENV) or "").strip().lower() in ("off", "0", "false"):
        return None
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        _winmm = ctypes.WinDLL("winmm")  # type: ignore[attr-defined]
    except (OSError, ImportError):  # no winmm is simply a coarser clock
        _winmm = None
    return _winmm


class _fine_timer:
    """Raise the system timer resolution for the length of one burst."""

    def __enter__(self) -> "_fine_timer":
        global _timer_period_depth
        dll = _timer_resolution_available()
        if dll is not None:
            if _timer_period_depth == 0:
                try:
                    dll.timeBeginPeriod(1)
                except (OSError, AttributeError):
                    return self
            _timer_period_depth += 1
        return self

    def __exit__(self, *exc: Any) -> None:
        global _timer_period_depth
        if _timer_period_depth <= 0:
            return
        _timer_period_depth -= 1
        if _timer_period_depth == 0 and _winmm is not None:
            try:
                _winmm.timeEndPeriod(1)
            except (OSError, AttributeError):
                pass


# ── per-page seeding ────────────────────────────────────────────────────────

def page_motion_seed(session_seed: int, ordinal: int) -> int:
    """Derive a page's motion seed from the session seed.

    Per PAGE rather than per session so that determinism survives more than
    one tab: a single shared stream would make every path depend on the order
    in which tabs happened to move. Same session seed plus same tab ordinal
    gives the same path, which is what makes a replayed seed replay the
    cursor. Kept inside int31 for consistency with the rest of the seeding.
    """
    h = (int(session_seed) & 0xFFFFFFFF) * 0x9E3779B1
    h = (h ^ ((int(ordinal) & 0xFFFF) * 0x85EBCA6B)) & 0xFFFFFFFF
    h ^= h >> 16
    return h & 0x7FFFFFFF


class _Session:
    """Everything a session hands to the pages underneath it."""

    __slots__ = ("seed", "max_seconds", "_ordinal")

    def __init__(self, seed: int, max_seconds: float) -> None:
        self.seed = int(seed)
        self.max_seconds = float(max_seconds)
        self._ordinal = 0

    def next_ordinal(self) -> int:
        n = self._ordinal
        self._ordinal += 1
        return n


class _PageCursor:
    """Cursor state for one page: where it is, and how it likes to move."""

    __slots__ = ("seed", "max_seconds", "_profile", "persona", "x", "y",
                 "busy", "action", "last_event_at")

    def __init__(self, seed: int, max_seconds: float, factory: Callable[..., Any]) -> None:
        self.seed = int(seed)
        self.max_seconds = float(max_seconds)
        self._profile = _build_profile(factory, self.seed, self.max_seconds)
        self.persona = _behaviour.PointerPersona.from_seed(self.seed)
        self.x: Optional[float] = None
        self.y: Optional[float] = None
        self.busy = False
        # Counts actions, so every draw that has to differ between two actions
        # of one session (the landing point, the fidget) has something to vary
        # on while staying reproducible from the seed.
        self.action = 0
        self.last_event_at: Optional[float] = None

    # -- geometry ----------------------------------------------------------

    def start_point(self, width: Optional[float], height: Optional[float]) -> Tuple[float, float]:
        """Where the pointer is before this page's first movement.

        Never the origin, and never a constant: a session whose first movement
        departs from (0, 0) is announcing that it had no pointer state at all
        before it started, which no real navigation produces.
        """
        w, h = width, height
        if not w or not h:
            # A page that will not report a viewport used to send the pointer
            # to exactly (0, 0) - reinstating, silently, the invariant this
            # whole change exists to remove. Say so, and pick a plausible
            # position anyway.
            _warn_once(
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
                _warn_once(
                    "bad-start-point",
                    "the motion generator's start_point() failed (%s: %s); "
                    "using the behaviour planner's instead."
                    % (type(exc).__name__, exc),
                )
        return _behaviour.initial_pointer(self.seed, (float(w), float(h)))

    def here(self, page: Any) -> Tuple[float, float]:
        """Current pointer position, placing it first if it has never moved."""
        if self.x is None or self.y is None:
            w, h = _viewport(page)
            self.x, self.y = self.start_point(w, h)
        return float(self.x), float(self.y)

    # -- the stroke seam ---------------------------------------------------

    def path(self, fx: float, fy: float, tx: float, ty: float) -> List[Tuple[float, float, float]]:
        raw = self._profile.path(fx, fy, tx, ty)
        return _normalise_waypoints(raw, self.max_seconds)

    def renderer(self, bounds: Tuple[float, float]) -> Callable[..., List[Any]]:
        """A ``_behaviour`` renderer backed by the stroke generator.

        This is what keeps ONE stroke model in the product: the macro planner
        decides that a movement happens and where it goes, and every stroke it
        asks for is drawn by ``_motion``.
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
            return _behaviour.steps_from_waypoints(
                raw, kind=kind, lead_ms=lead_ms, bounds=bounds
            )

        return render

    def rng(self, tag: str) -> random.Random:
        """A per-(page, action, purpose) stream. Reproducible from the seed."""
        return random.Random(
            _behaviour._sub_seed(self.seed, "%s:%d" % (tag, self.action))
        )


# ── timelines ───────────────────────────────────────────────────────────────
#
# One representation for everything that gets dispatched: absolute time from
# the start of the burst, plus what to do at that instant. Absolute, because
# the previous dispatcher slept once per waypoint and therefore accumulated
# every millisecond by which the platform overslept - a planned median gap of
# 12.15 ms was delivered at 16.13 ms, and mean 13.44 ms at 20.24 ms, a 1.51x
# stretch. Per-seed differentiation is carried largely by timing, so that
# stretch was quietly deleting the thing the seeding is for.

class _Ev:
    """One dispatchable instant."""

    __slots__ = ("t_ms", "x", "y", "kind", "dx", "dy")

    def __init__(self, t_ms: float, x: float, y: float, kind: str = "move",
                 dx: float = 0.0, dy: float = 0.0) -> None:
        self.t_ms = float(t_ms)
        self.x = float(x)
        self.y = float(y)
        self.kind = kind
        self.dx = float(dx)
        self.dy = float(dy)

    @property
    def is_wheel(self) -> bool:
        return self.kind == "wheel"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "_Ev(%.1f, %.1f, %.1f, %s)" % (self.t_ms, self.x, self.y, self.kind)


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


def _fit_timeline(evs: List[_Ev], budget_s: float) -> List[_Ev]:
    """Fit a timeline inside ``budget_s`` and inside what hardware can report.

    Two rules, in this order:

    * if the plan is longer than the budget, every timestamp is scaled - the
      same movement performed faster, not a movement that stops half way;
    * events closer together than :data:`MIN_EVENT_INTERVAL_MS` are then
      DROPPED, not squeezed. Scaling alone is what produced impossible event
      rates: the waypoint count never changed, so a tighter cap simply meant
      more events per second, without limit. The last event of the timeline
      and every wheel notch survive regardless - the first is where the
      movement has to end, the second carries a delta that would be lost.
    """
    if not evs:
        return []
    total = evs[-1].t_ms
    if budget_s > 0 and total > budget_s * 1000.0:
        scale = (budget_s * 1000.0) / total
        for ev in evs:
            ev.t_ms *= scale
    out: List[_Ev] = []
    last_t = -MIN_EVENT_INTERVAL_MS
    for i, ev in enumerate(evs):
        if out and not ev.is_wheel and not out[-1].is_wheel \
                and ev.t_ms == out[-1].t_ms:
            # Two positions at one instant are one sample, and the sample a
            # device reports is the newest one it has.
            out[-1] = ev
            continue
        is_last = i == len(evs) - 1
        keep = (
            ev.is_wheel
            or is_last
            or ev.t_ms - last_t >= MIN_EVENT_INTERVAL_MS
        )
        if not keep:
            continue
        if is_last and out and not ev.is_wheel and not out[-1].is_wheel                 and ev.t_ms - last_t < MIN_EVENT_INTERVAL_MS:
            # The destination must be dispatched, so its PREDECESSOR goes
            # instead. Keeping both unconditionally is what let this function
            # emit a gap under its own floor - measured at 1.83 ms, 547 Hz,
            # with a tight time cap. A movement that stops one sample short of
            # where it was asked to go is a worse defect than one sample fewer.
            out.pop()
            last_t = out[-1].t_ms if out else -MIN_EVENT_INTERVAL_MS
        out.append(ev)
        last_t = ev.t_ms
    return out


def _normalise_waypoints(raw: Iterable[Any], max_seconds: float) -> List[Tuple[float, float, float]]:
    """Reduce whatever the generator returns to ``(x, y, delay_seconds)``.

    Three shapes are accepted: a waypoint object carrying ``x`` / ``y`` /
    ``dt_ms``, an ``(x, y, delay_seconds)`` triple, and a bare ``(x, y)`` pair
    whose pacing is then spread evenly over the cap.

    The cap from ``humanize=<seconds>`` is applied through
    :func:`_fit_timeline`, so a tighter cap makes the movement shorter AND
    coarser rather than merely denser.
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


async def _dispatch(
    evs: Sequence[_Ev],
    emit_move: Callable[[float, float], Any],
    emit_wheel: Optional[Callable[[float, float], Any]] = None,
    *,
    timer: Any = None,
    emit_last: bool = True,
) -> int:
    """Walk a timeline against ABSOLUTE deadlines. Returns events delivered.

    Every deadline is measured from one ``t0``, so oversleeping on one event
    does not push the next one out: lateness cannot accumulate.

    When the platform cannot deliver an event on time - Windows quantises a
    short sleep to the system timer, so a 12 ms request routinely returns after
    16 - the event is DROPPED rather than sent late. The rule is
    parameter-free: an event is skipped when the NEXT event is already due,
    because sending it then would be sending two events at one instant and
    would push the whole rest of the movement backwards. What survives is a
    stream whose timestamps still land where the plan put them, at whatever
    density the machine can actually produce.

    The last event is never dropped (it is where the movement ends) and neither
    is a wheel notch (it carries a delta nobody else will send).
    """
    tm = timer if timer is not None else _TIMER
    if not evs:
        return 0
    min_gap = MIN_EVENT_INTERVAL_MS / 1000.0
    delivered = 0
    n = len(evs)
    with _fine_timer():
        t0 = tm.now()
        last_emit: Optional[float] = None
        for i, ev in enumerate(evs):
            last = i == n - 1
            droppable = not last and not ev.is_wheel
            deadline = t0 + ev.t_ms / 1000.0
            wait = deadline - tm.now()
            if wait > 0:
                await tm.sleep(wait)
            elif droppable and i + 1 < n:
                if tm.now() >= t0 + evs[i + 1].t_ms / 1000.0:
                    continue  # superseded: the next point is already due
            # Catching up after an overslept deadline must not produce two
            # events in the same instant: that is an event rate no device
            # reports, and it is as visible as being late was.
            if last_emit is not None:
                behind = min_gap - (tm.now() - last_emit)
                if behind > 0:
                    if droppable:
                        continue
                    await tm.sleep(behind)
            if last and not emit_last:
                break
            if ev.is_wheel:
                if emit_wheel is not None:
                    await emit_wheel(ev.dx, ev.dy)
                    delivered += 1
                    last_emit = tm.now()
                continue
            await emit_move(ev.x, ev.y)
            delivered += 1
            last_emit = tm.now()
    return delivered


# ── registry ────────────────────────────────────────────────────────────────
#
# Weak keys throughout: a closed browser, context or page must not be kept
# alive by our bookkeeping.
_SESSIONS: "WeakKeyDictionary[Any, _Session]" = WeakKeyDictionary()
_PAGES: "WeakKeyDictionary[Any, _PageCursor]" = WeakKeyDictionary()


def _impl(obj: Any) -> Any:
    """The implementation object behind a sync/async binding wrapper."""
    return getattr(obj, "_impl_obj", obj)


def enable_for(owner: Any, *, seed: int, max_seconds: float) -> bool:
    """Arm the Python cursor engine for a Browser or a BrowserContext.

    Registering the *owner* (rather than each page) is what lets pages created
    by any route benefit: ``context.new_page()``, ``browser.new_page()`` - which
    makes its context inside the driver, out of reach of any wrapper we install
    on our side - and popups a site opens by itself.  A page looks its session
    up lazily through its own context and browser the first time it moves.
    """
    if not _ensure_patched():
        return False
    if _motion_factory() is None:
        return False
    try:
        _SESSIONS[_impl(owner)] = _Session(seed, max_seconds)
    except TypeError:  # not weak-referenceable: refuse rather than leak
        return False
    return True


def _session_for_page(page: Any) -> Optional[_Session]:
    session = _SESSIONS.get(page)
    if session is not None:
        return session
    context = getattr(page, "_browser_context", None)
    if context is None:
        return None
    session = _SESSIONS.get(context)
    if session is not None:
        return session
    browser = getattr(context, "_browser", None)
    if browser is None:
        return None
    return _SESSIONS.get(browser)


def _cursor_for_page(page: Any) -> Optional[_PageCursor]:
    cursor = _PAGES.get(page)
    if cursor is not None:
        return cursor
    session = _session_for_page(page)
    if session is None:
        return None
    factory = _motion_factory()
    if factory is None:
        return None
    try:
        cursor = _PageCursor(
            page_motion_seed(session.seed, session.next_ordinal()),
            session.max_seconds,
            factory,
        )
    except Exception as exc:  # noqa: BLE001 - a broken generator must not break clicking
        _warn_once(
            "profile-construction",
            "the cursor motion generator could not be constructed (%s: %s); "
            "this session will not humanise its pointer. This is a broken "
            "install, not a configuration." % (type(exc).__name__, exc),
        )
        return None
    _PAGES[page] = cursor
    return cursor


def _page_of_frame(frame: Any) -> Optional[Any]:
    return getattr(frame, "_page", None)


def _page_of_mouse(mouse: Any) -> Optional[Any]:
    # A Mouse holds the page's own protocol channel, and a channel knows the
    # object it belongs to. That is the only back-reference there is.
    channel = getattr(mouse, "_channel", None)
    return getattr(channel, "_object", None) if channel is not None else None


# ── geometry ────────────────────────────────────────────────────────────────

def _viewport(page: Any) -> Tuple[Optional[float], Optional[float]]:
    size = getattr(page, "_viewport_size", None) or getattr(page, "viewport_size", None)
    if isinstance(size, dict):
        return size.get("width"), size.get("height")
    return None, None


def _bounds(page: Any) -> Tuple[float, float]:
    """Viewport size to plan inside, falling back to a nominal one."""
    w, h = _viewport(page)
    if not w or not h:
        return _FALLBACK_VIEWPORT
    return float(w) - 1.0, float(h) - 1.0


def _inside(x: float, y: float, w: Optional[float], h: Optional[float]) -> bool:
    if not w or not h:
        return True
    return 0.0 <= x < float(w) and 0.0 <= y < float(h)


def _clamp(x: float, y: float, w: Optional[float], h: Optional[float]) -> Tuple[float, float]:
    if not w or not h:
        return x, y
    return (
        min(max(x, 0.0), float(w) - 1.0),
        min(max(y, 0.0), float(h) - 1.0),
    )


# The errors a page can legitimately produce while we are only *aiming*: the
# element went away, the frame navigated, the page closed. Resolved lazily so
# that an unexpected bindings layout cannot stop the module importing.
def _page_errors() -> Tuple[type, ...]:
    try:
        from playwright._impl._errors import Error as PWError  # type: ignore
    except ImportError:  # an older bindings layout, or a newer one
        try:
            from playwright._impl._api_types import Error as PWError  # type: ignore
        except ImportError:
            return ()
    return (PWError,)


def _timeout_errors() -> Tuple[type, ...]:
    try:
        from playwright._impl._errors import TimeoutError as PWTimeout  # type: ignore
    except ImportError:
        try:
            from playwright._impl._api_types import TimeoutError as PWTimeout  # type: ignore
        except ImportError:
            return ()
    return (PWTimeout,)


async def _element_box(frame: Any, selector: str) -> Tuple[Optional[Any], Optional[dict]]:
    """Resolve the element and its box, or (None, None). Never raises.

    Deliberately non-blocking and deliberately forgiving: this is a hint used
    to aim a mouse path, never a correctness step. It resolves the element if
    it is already there and gives up otherwise, so it can neither add a wait
    to a passing call nor invent an exception on a failing one - the original
    coroutine still does its own waiting and still raises its own errors.
    """
    try:
        handle = await frame.query_selector(selector)
    except _page_errors():
        return None, None  # gone, navigated, closed, or not a selector we know
    if handle is None:
        return None, None
    try:
        box = await handle.bounding_box()
    except _page_errors():
        box = None
    if not box:
        await _dispose(handle)
        return None, None
    return handle, box


async def _dispose(handle: Any) -> None:
    try:
        await handle.dispose()
    except _page_errors():
        pass  # the page or the frame is already gone; nothing to release


async def _target_point(frame: Any, selector: str, position: Any) -> Optional[Tuple[float, float]]:
    """Where the action is about to touch, in viewport coordinates.

    Kept as its own function because it is the whole of what the aiming needs
    when the caller has specified a position: no landing draw, no hit test.
    """
    handle, box = await _element_box(frame, selector)
    if handle is None or box is None:
        return None
    await _dispose(handle)
    if isinstance(position, dict) and "x" in position and "y" in position:
        return box["x"] + float(position["x"]), box["y"] + float(position["y"])
    return box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0


async def _hits(handle: Any, x: float, y: float) -> bool:
    """Does (x, y) actually land on this element?

    A bounding box is not the element. An inline link that wraps across two
    lines, a rotated control, a rounded button: all of them have points inside
    their box that belong to something else. The centre is checked by the
    automation layer itself; a point we chose has to be checked by us, or we
    would be turning working clicks into hit-target failures.
    """
    try:
        return bool(await handle.evaluate(
            "(el, p) => { const e = document.elementFromPoint(p.x, p.y);"
            " return !!e && (e === el || el.contains(e) || e.contains(el)); }",
            {"x": x, "y": y},
        ))
    except _page_errors():
        return False


# ── the actual travelling ───────────────────────────────────────────────────

async def _travel(
    cursor: _PageCursor,
    page: Any,
    to_x: float,
    to_y: float,
    raw_move: Callable[[float, float], Any],
    *,
    timer: Any = None,
) -> float:
    """Emit the intermediate waypoints of one plain stroke.

    Used by ``mouse.move``, which is the one entry point where the caller named
    the exact destination and the macro layer has nothing to add.

    The endpoint is the caller's to emit - a bare ``mouse.move`` has to land on
    the exact coordinates it was given, unclamped - so this returns after
    waiting out the last waypoint's deadline without sending it. The return
    value is kept at 0.0: with absolute scheduling there is no owed pause left
    to hand back.
    """
    w, h = _viewport(page)
    cursor.here(page)
    try:
        waypoints = cursor.path(cursor.x, cursor.y, to_x, to_y)
    except Exception as exc:  # noqa: BLE001 - never let motion break an action
        _warn_once(
            "path-failed",
            "the cursor motion generator raised while planning a path "
            "(%s: %s); this movement is not humanised. This is a broken "
            "install, not a configuration." % (type(exc).__name__, exc),
        )
        waypoints = []

    evs: List[_Ev] = []
    t = 0.0
    end_key = (round(to_x), round(to_y))
    for px, py, delay in waypoints:
        t += delay * 1000.0
        cx, cy = _clamp(px, py, w, h)
        # Never pre-empt the endpoint - it is the caller's to emit, once.
        #
        # Nothing else here filters the stream. Whether two consecutive events
        # may share a device pixel is a question about the SHAPE of a path, and
        # the shape belongs to the generator: a dispatcher that silently
        # removed every repeat would make "the duplicate fraction is exactly
        # zero" true no matter what the generator did, and a quantity that is
        # exactly zero in every install is exactly the kind of invariant this
        # work exists to remove.
        if (round(cx), round(cy)) == end_key:
            continue
        evs.append(_Ev(t, cx, cy))
    # The endpoint closes the schedule so its deadline is waited out even
    # though the caller is the one that emits it.
    evs.append(_Ev(max(t, evs[-1].t_ms if evs else 0.0), to_x, to_y))

    async def emit(x: float, y: float) -> None:
        await raw_move(x, y)
        cursor.x, cursor.y = x, y

    await _dispatch(evs, emit, timer=timer, emit_last=False)
    cursor.x, cursor.y = to_x, to_y
    cursor.last_event_at = (timer or _TIMER).now()
    return 0.0


async def _run_steps(
    cursor: _PageCursor,
    steps: Sequence[Any],
    emit_move: Callable[[float, float], Any],
    emit_wheel: Optional[Callable[[float, float], Any]] = None,
    *,
    budget_s: float,
    timer: Any = None,
    emit_last: bool = True,
) -> None:
    """Dispatch a planned ``_behaviour`` timeline, inside a time budget."""
    evs = _fit_timeline(_timeline(steps), budget_s)
    if not evs:
        return

    async def move(x: float, y: float) -> None:
        await emit_move(x, y)
        cursor.x, cursor.y = x, y

    await _dispatch(evs, move, emit_wheel, timer=timer, emit_last=emit_last)
    if not emit_last and evs:
        cursor.x, cursor.y = evs[-1].x, evs[-1].y
    cursor.last_event_at = (timer or _TIMER).now()


def _idle_budget_ms(cursor: _PageCursor, timer: Any) -> float:
    """How much of the movement budget this action may spend on a fidget.

    Zero when the previous event was moments ago: two calls back to back are
    one piece of work, and inserting a fidget between them would be both a lie
    and a delay.
    """
    tm = timer if timer is not None else _TIMER
    if cursor.last_event_at is None:
        return 0.0
    gap_ms = (tm.now() - cursor.last_event_at) * 1000.0
    if gap_ms < _IDLE_MIN_GAP_MS:
        return 0.0
    return min(cursor.max_seconds * _IDLE_BUDGET_FRAC * 1000.0, gap_ms * 0.5)


def _plan_fidget(cursor: _PageCursor, page: Any, timer: Any) -> List[Any]:
    """What the hand did while the caller's script was busy elsewhere.

    We cannot dispatch into the past, so the idle period is planned in full and
    only its tail is kept - see :func:`_behaviour.tail_within`. The alternative,
    inventing a short episode from scratch, would have a distribution of its
    own that no amount of seeding makes match the planner's.
    """
    budget = _idle_budget_ms(cursor, timer)
    if budget <= 0.0:
        return []
    tm = timer if timer is not None else _TIMER
    gap_ms = min((tm.now() - (cursor.last_event_at or tm.now())) * 1000.0,
                 _IDLE_MAX_GAP_MS)
    bounds = _bounds(page)
    here = cursor.here(page)
    gate = cursor.rng("cursor:fidget-gate")
    if gate.random() < cursor.persona.aimless_rate:
        # A movement that ends nowhere in particular. If every movement of a
        # session terminates on a control then the SET OF ENDPOINTS is a
        # signature on its own, whatever the paths between them look like.
        steps = _behaviour.plan_aimless_move(
            cursor.seed, cursor.persona, here, bounds,
            nonce=cursor.action, render=cursor.renderer(bounds),
        )
        return _behaviour.tail_within(steps, budget, here, bounds)
    steps = _behaviour.plan_idle(
        cursor.seed, cursor.persona, here, bounds, gap_ms,
        nonce=cursor.action, render=cursor.renderer(bounds),
    )
    return _behaviour.tail_within(steps, budget, here, bounds)


# ── the wrappers ────────────────────────────────────────────────────────────

_PATCH_STATE: Optional[bool] = None
_ORIGINAL_MOUSE_MOVE: Optional[Callable[..., Any]] = None
_ORIGINAL_MOUSE_WHEEL: Optional[Callable[..., Any]] = None
# Kept so the wrappers can be reasoned about (and asserted on) without having
# to re-import a pristine copy of the bindings.
_ORIGINAL_FRAME_ACTIONS: dict = {}

# Every public pointer action funnels into one of these on the implementation
# Frame. ``set_checked`` is absent on purpose: it calls ``check`` / ``uncheck``.
_FRAME_ACTIONS = ("_click", "dblclick", "hover", "tap", "check", "uncheck")

_MARKER = "_invpw_cursor_wrapped"


def _first(args: Sequence[Any], kwargs: dict, name: str, index: int) -> Any:
    if name in kwargs:
        return kwargs[name]
    return args[index] if len(args) > index else None


def _wrap_frame_action(original: Callable[..., Any]) -> Callable[..., Any]:
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        overrides: Optional[Dict[str, Any]] = None
        try:
            overrides = await _approach(self, args, kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - motion never fails an action
            _warn_once(
                "approach-failed",
                "the cursor approach raised (%s: %s); the action itself is "
                "unaffected and ran normally. Please report this."
                % (type(exc).__name__, exc),
            )
        if not overrides:
            return await original(self, *args, **kwargs)
        # We told the action to click somewhere other than the centre. If that
        # point turns out not to be actionable, the caller's call must still
        # succeed exactly as it would have without us - so the original
        # arguments get their own attempt.
        try:
            return await original(self, *args, **{**kwargs, **overrides})
        except _timeout_errors() as exc:
            _warn_once(
                "landing-retry",
                "an off-centre landing point was not actionable (%s); the "
                "action was retried with its own arguments. Set %s=off to "
                "disable off-centre landing." % (type(exc).__name__, LANDING_ENV),
            )
        return await original(self, *args, **kwargs)

    setattr(wrapper, _MARKER, True)
    wrapper.__name__ = getattr(original, "__name__", "wrapper")
    wrapper.__doc__ = original.__doc__
    return wrapper


def _may_override(args: Sequence[Any], kwargs: dict) -> bool:
    """Is it safe to add ``position``/``timeout`` to this call's arguments?

    Only when the caller supplied neither. The bindings pass everything but the
    selector by keyword, so ``args`` longer than the selector means an unknown
    caller shape and we keep our hands off rather than risk a "got multiple
    values for argument" on somebody's click.

    Also requires that Playwright's TimeoutError be importable, because that is
    what the retry below catches: without it an off-centre point that turns out
    not to be actionable would fail a call that would otherwise have worked.
    """
    return (
        len(args) <= 1
        and "position" not in kwargs
        and "timeout" not in kwargs
        and bool(_timeout_errors())
    )


def _landing_override(box: dict, landing: Tuple[float, float]) -> Dict[str, Any]:
    """The kwargs that make the action itself land on our point.

    Without this the approach ends off-centre and the automation layer then
    moves to the exact geometric centre anyway, so the LAST event of every
    element-targeted action is ``box.x + width/2, box.y + height/2`` - one
    exact number, identical in every install, readable from a single event.

    The bounded ``timeout`` is the price of touching the action's own
    arguments: if our point is not actionable, the caller's call must still
    succeed, so the retry has to be reachable before the caller's own deadline
    rather than after it.
    """
    return {
        "position": {
            "x": landing[0] - box["x"],
            "y": landing[1] - box["y"],
        },
        "timeout": _LANDING_ATTEMPT_TIMEOUT_MS,
    }


async def _approach(frame: Any, args: Sequence[Any], kwargs: dict,
                    *, timer: Any = None) -> Optional[Dict[str, Any]]:
    """Walk the cursor onto the element this action is about to touch.

    Returns the kwargs overrides the action should run with, or None.
    """
    # A trial run performs the actionability checks without touching anything,
    # so it must not move a real cursor either.
    if kwargs.get("trial"):
        return None
    page = _page_of_frame(frame)
    if page is None:
        return None
    cursor = _cursor_for_page(page)
    if cursor is None or cursor.busy:
        return None
    selector = _first(args, kwargs, "selector", 0)
    if not isinstance(selector, str):
        return None
    move = _ORIGINAL_MOUSE_MOVE
    mouse = getattr(page, "mouse", None)
    if move is None or mouse is None:
        return None

    cursor.busy = True
    cursor.action += 1
    try:
        aim = await _choose_landing(frame, cursor, selector, args, kwargs)
        if aim is None:
            return None

        w, h = _viewport(page)
        if not _inside(aim.landing[0], aim.landing[1], w, h):
            # The element is off-screen; the action is about to scroll it into
            # view and we would be aiming at where it used to be. Let the
            # action place the cursor itself this once.
            return None

        await _walk_onto(cursor, page, aim, move, mouse, timer)
        return aim.override
    finally:
        cursor.busy = False


@dataclass
class _Aim:
    """Where this action is going, and how the caller must be adjusted for it.

    ``override`` is the kwargs change that tells the action to click the point
    we walked to rather than the element's centre; None means the action is
    left exactly as the caller wrote it.
    """

    box: Dict[str, float]
    landing: tuple
    override: Optional[Dict[str, Any]]

    @property
    def rect(self) -> tuple:
        return (self.box["x"], self.box["y"],
                self.box["width"], self.box["height"])


async def _choose_landing(frame: Any, cursor: Any, selector: str,
                          args: Sequence[Any], kwargs: dict) -> Optional[_Aim]:
    """Pick the point on the element this action will land on.

    Split out of ``_approach`` so the element handle's lifetime is one small
    scope with one ``finally`` in it, rather than a nested try inside a
    function that also spends a time budget and dispatches events.
    """
    handle, box = await _element_box(frame, selector)
    if handle is None or box is None:
        return None
    try:
        position = kwargs.get("position")
        if isinstance(position, dict) and "x" in position and "y" in position:
            # The caller aimed explicitly. Honour it exactly and override
            # nothing: choosing our own point here would silently move a click
            # the caller had every reason to think was pinned.
            return _Aim(box, (box["x"] + float(position["x"]),
                              box["y"] + float(position["y"])), None)

        landing = _behaviour.landing_point(
            (box["x"], box["y"], box["width"], box["height"]),
            cursor.rng("cursor:landing"),
            spread=_LANDING_SPREAD, keep=_LANDING_KEEP,
        )
        if (
            landing_enabled()
            and _may_override(args, kwargs)
            and box["width"] >= _LANDING_MIN_BOX_PX
            and box["height"] >= _LANDING_MIN_BOX_PX
            and await _hits(handle, landing[0], landing[1])
        ):
            return _Aim(box, landing, _landing_override(box, landing))
        # The off-centre point missed the element (it is not a rectangle, or it
        # is too small for the spread to be safe). Fall back to the centre and
        # do NOT override: an override naming a point that misses would turn a
        # working click into a failing one.
        return _Aim(box, (box["x"] + box["width"] / 2.0,
                          box["y"] + box["height"] / 2.0), None)
    finally:
        await _dispose(handle)


async def _walk_onto(cursor: Any, page: Any, aim: _Aim, move: Any,
                     mouse: Any, timer: Any) -> None:
    """Spend the time budget getting the pointer to ``aim``."""

    async def raw(x: float, y: float) -> None:
        await move(mouse, x, y)

    bounds = _bounds(page)
    budget = cursor.max_seconds

    fidget = _plan_fidget(cursor, page, timer)
    if fidget:
        # The fidget spends its own share of the budget and hands the rest to
        # the approach, which is the part that has to work. The floor keeps a
        # pathological share from leaving the approach no time.
        spent = min(sum(s.delay_ms for s in fidget) / 1000.0,
                    budget * _IDLE_BUDGET_FRAC)
        await _run_steps(cursor, fidget, raw, budget_s=spent, timer=timer)
        budget = max(budget - spent, 0.2)

    steps = _behaviour.plan_approach(
        cursor.seed, cursor.persona, cursor.here(page), aim.rect, bounds,
        nonce=cursor.action, landing=aim.landing,
        render=cursor.renderer(bounds),
    )
    if steps:
        # ``emit_last=False``: the final hop onto the landing point is left to
        # the action itself. That is the whole hover fix restated - the only
        # event inside the hit-target window is the automation layer's own
        # move, and it is on target - and it also removes what would otherwise
        # be a zero-displacement event immediately before every mousedown,
        # since the action moves to the point we just landed on.
        await _run_steps(cursor, steps, raw, budget_s=budget, timer=timer,
                         emit_last=False)
    else:
        # Sub-pixel approach: the pointer is already standing on the target.
        # Emitting a move to where it already is would be a zero-displacement
        # event, so the action's own move is left to do the whole of it.
        cursor.x, cursor.y = aim.landing


def _wrap_mouse_move(original: Callable[..., Any]) -> Callable[..., Any]:
    async def wrapper(self: Any, x: float, y: float, steps: int = None) -> Any:  # type: ignore[assignment]
        # ``steps=`` is the caller doing their own interpolation. Respect it.
        if steps is None or steps <= 1:
            try:
                page = _page_of_mouse(self)
                cursor = _cursor_for_page(page) if page is not None else None
                if cursor is not None and not cursor.busy:
                    cursor.busy = True
                    cursor.action += 1
                    try:
                        async def raw(px: float, py: float) -> None:
                            await original(self, px, py)

                        await _travel(cursor, page, x, y, raw)
                    finally:
                        cursor.busy = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _warn_once(
                    "move-failed",
                    "the cursor path raised during mouse.move (%s: %s); the "
                    "move itself is unaffected. Please report this."
                    % (type(exc).__name__, exc),
                )
        # The exact endpoint always goes through untouched, unclamped, so
        # mouse.move keeps its documented semantics - including the off-viewport
        # coordinates callers legitimately use to park the pointer.
        return await original(self, x, y, steps=steps)

    setattr(wrapper, _MARKER, True)
    wrapper.__name__ = getattr(original, "__name__", "move")
    wrapper.__doc__ = original.__doc__
    return wrapper


def _scroll_plan(cursor: _PageCursor, page: Any, delta_x: float, delta_y: float
                 ) -> List[Any]:
    """A wheel burst carrying exactly (delta_x, delta_y), with the hand on it.

    One protocol-level wheel event of 900 px is not a scroll a person performs;
    a notched wheel produces detents. The number of detents is bounded by the
    movement budget, and the deltas are rescaled so the burst sums EXACTLY to
    what the caller asked for - a scroll that lands somewhere else is a broken
    call, not a subtle one.
    """
    total = abs(delta_x) + abs(delta_y)
    if total < 1.0:
        return []
    notch = 100.0
    max_ticks = max(1, int(cursor.max_seconds * 1000.0 / 90.0))
    ticks = int(max(1, min(round(total / notch), max_ticks, 24)))
    if ticks < 2:
        return []
    bounds = _bounds(page)
    steps = _behaviour.plan_scroll(
        cursor.seed, cursor.persona, cursor.here(page), bounds,
        ticks=ticks, tick_dy=1.0, nonce=cursor.action,
        render=cursor.renderer(bounds),
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
        out.append(_behaviour.Step(x=s.x, y=s.y, delay_ms=s.delay_ms,
                                   kind="wheel", dx=dx, dy=dy))
    return out


def _wrap_mouse_wheel(original: Callable[..., Any]) -> Callable[..., Any]:
    # The parameter NAMES matter: both generated API layers call the
    # implementation entirely by keyword (``wheel(deltaX=..., deltaY=...)``,
    # ``move(x=..., y=..., steps=...)``), so a wrapper that renames an argument
    # does not shadow the method, it breaks every call to it.
    async def wrapper(self: Any, deltaX: float, deltaY: float) -> Any:  # noqa: N803
        delta_x, delta_y = deltaX, deltaY
        try:
            page = _page_of_mouse(self)
            cursor = _cursor_for_page(page) if page is not None else None
            if cursor is not None and not cursor.busy:
                cursor.busy = True
                cursor.action += 1
                try:
                    plan = _scroll_plan(cursor, page, float(delta_x), float(delta_y))
                    if plan:
                        move = _ORIGINAL_MOUSE_MOVE
                        mouse = getattr(page, "mouse", None)

                        async def emit_move(x: float, y: float) -> None:
                            if move is not None and mouse is not None:
                                await move(mouse, x, y)

                        async def emit_wheel(dx: float, dy: float) -> None:
                            await original(self, dx, dy)

                        await _run_steps(cursor, plan, emit_move, emit_wheel,
                                         budget_s=cursor.max_seconds)
                        return None
                finally:
                    cursor.busy = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _warn_once(
                "wheel-failed",
                "the scroll planner raised (%s: %s); the scroll itself is "
                "unaffected. Please report this." % (type(exc).__name__, exc),
            )
        return await original(self, deltaX, deltaY)

    setattr(wrapper, _MARKER, True)
    wrapper.__name__ = getattr(original, "__name__", "wheel")
    wrapper.__doc__ = original.__doc__
    return wrapper


def _ensure_patched() -> bool:
    """Install the wrappers once per process. Idempotent, and honest on failure."""
    global _PATCH_STATE, _ORIGINAL_MOUSE_MOVE, _ORIGINAL_MOUSE_WHEEL
    if _PATCH_STATE is not None:
        return _PATCH_STATE
    try:
        from playwright._impl._frame import Frame
        from playwright._impl._input import Mouse

        for name in _FRAME_ACTIONS:
            original = getattr(Frame, name, None)
            if original is None or getattr(original, _MARKER, False):
                continue
            _ORIGINAL_FRAME_ACTIONS[name] = original
            setattr(Frame, name, _wrap_frame_action(original))

        move = Mouse.move
        if not getattr(move, _MARKER, False):
            _ORIGINAL_MOUSE_MOVE = move
            Mouse.move = _wrap_mouse_move(move)  # type: ignore[method-assign]
        wheel = getattr(Mouse, "wheel", None)
        if wheel is not None and not getattr(wheel, _MARKER, False):
            _ORIGINAL_MOUSE_WHEEL = wheel
            Mouse.wheel = _wrap_mouse_wheel(wheel)  # type: ignore[method-assign]
        _PATCH_STATE = True
    except Exception as exc:  # noqa: BLE001 - an unknown bindings layout is not fatal
        _warn_once(
            "bindings",
            "the Playwright bindings could not be wrapped (%s: %s), so pointer "
            "movement is not humanised in this process. The installed "
            "Playwright version is probably outside the supported range."
            % (type(exc).__name__, exc),
        )
        _PATCH_STATE = False
    return _PATCH_STATE


__all__ = [
    "ENGINE_ENV",
    "ENGINE_BINARY",
    "ENGINE_OFF",
    "ENGINE_PYTHON",
    "LANDING_ENV",
    "MIN_EVENT_INTERVAL_MS",
    "enable_for",
    "humanize_prefs",
    "landing_enabled",
    "max_seconds_for",
    "motion_available",
    "page_motion_seed",
    "resolve_cursor_engine",
]
