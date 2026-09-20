"""The delivery discipline of a timed pointer plan, apart from any clock.

⛔ WHY THIS IS ITS OWN MODULE. The rules below - absolute deadlines from one
``t0``, drop rather than send late, never two events in one instant - used to
live welded into an ``async`` loop in :mod:`._cursor`, which is the only place
that could run them. That made them unavailable to the one other place that
moves a pointer: the drag, inside the in-process Juggler server, which is
synchronous. A drag therefore moved with no discipline at all - two events, both
at the destination - and the alternative was to write the rules a second time
in synchronous form.

So the rules live here, as a state machine with no clock of its own, and the two
callers supply the clock and the sleeping. :class:`Pacer` decides WHAT happens
next; a driver decides HOW to wait. There is one definition of the discipline
and two ways of obeying it, rather than two definitions that agree until one of
them is edited.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Iterable, List, Optional, Tuple

try:  # the path generator is optional; the floor it declares is not
    from . import _motion as _motion_mod
except Exception:  # noqa: BLE001 - an unimportable generator is not fatal here
    _motion_mod = None  # type: ignore[assignment]

#: Two pointer events closer together than this describe a device nobody sells.
#: Firefox coalesces mouse moves to the refresh rate, so a page on a real 60 Hz
#: display cannot see two of them 2 ms apart.
#:
#: The fallback is only reached when _motion failed to import, in which case the
#: session is already falling back to the browser's own expansion; keeping a
#: floor here rather than None means a timeline still refuses to emit a rate no
#: device produces.
MIN_EVENT_INTERVAL_MS = (
    float(getattr(_motion_mod, "SAMPLE_FLOOR_MS", 8.0))
    if _motion_mod is not None else 8.0
)

#: Windows quantises a waited-on timer to the system tick, which is 15.6 ms by
#: default: a 12 ms sleep comes back at 16.7 ms (measured, p50), and there is no
#: scheduling policy that can recover a resolution the platform will not give.
#: Asking for a 1 ms period brings the same sleep back at 12.5 ms. It is
#: reference-counted by the OS and released as soon as the movement is over, and
#: it is the difference between dispatching the plan and dispatching a rounded
#: copy of it - which matters here because per-seed differentiation is carried
#: largely by timing. Set it to "off" to leave the system tick alone.
TIMER_ENV = "INVPW_CURSOR_TIMER"

#: What :meth:`Pacer.step` can ask for.
SLEEP = "sleep"
EMIT = "emit"
DONE = "done"


# ── the system timer ────────────────────────────────────────────────────────

_winmm: Any = None
_winmm_tried = False
_timer_period_depth = 0


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


class fine_timer:
    """Raise the system timer resolution for the length of one burst.

    The depth counter is module-global on purpose: the client's cursor and the
    server's drag can be inside one at the same time, and the resolution must
    come back down once, when the last of them leaves.
    """

    def __enter__(self) -> "fine_timer":
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


# ── one dispatchable instant ────────────────────────────────────────────────

class Ev:
    """One dispatchable instant of a plan."""

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
        return "Ev(%.1f, %.1f, %.1f, %s)" % (self.t_ms, self.x, self.y, self.kind)


# ── the discipline ──────────────────────────────────────────────────────────

class Pacer:
    """Walk a timeline against ABSOLUTE deadlines, deciding only WHAT is next.

    Every deadline is measured from one ``t0`` - the clock reading handed to the
    first :meth:`step` - so oversleeping on one event does not push the next one
    out: lateness cannot accumulate.

    When the platform cannot deliver an event on time - Windows quantises a short
    sleep to the system timer, so a 12 ms request routinely returns after 16 -
    the event is DROPPED rather than sent late: an event is skipped when the
    NEXT event is already due, because sending it then would be sending two
    events at one instant and would push the whole rest of the movement
    backwards. What survives is a stream whose timestamps still land where the
    plan put them.

    ⛔ BUT ONLY WHILE THE DROP STAYS SMALL IN SPACE. A dropped event hands its
    distance to the next one that goes out, and that rule alone had no ceiling:
    on a loaded machine nearly every point was overtaken, the destination
    survived, and one event carried 89% of an 820 px journey with a timestamp
    exactly where the plan put it - a plausible rhythm wrapped around a jump
    no device makes. So an event may be dropped only if the event AFTER it lies
    within REACH of the last position actually sent, where reach is twice the
    plan's largest step: the one overslept sample the timer case produces
    merges two steps into one, and that is what is allowed. Beyond it the event
    is sent late instead, still no closer to the previous one than the floor,
    and the plan overruns its budget by however much the machine is behind. A
    hand that is slowed down is a hand; a hand that teleports is not. [B214]

    The last event is never dropped (it is where the movement ends) and neither
    is a wheel notch (it carries a delta nobody else will send). ``origin`` is
    where the pointer is before the first event: the reach of the first drops
    is measured from there, and without it from the first event of the plan.

    Usage, from either a synchronous or an asynchronous driver::

        pacer = Pacer(evs)
        while True:
            what, arg = pacer.step(clock())
            if what == DONE:
                break
            if what == SLEEP:
                sleep(arg)
            elif emitter_for(arg) is None:
                pacer.dropped()          # nothing can carry this one
            else:
                emit(arg)
                pacer.emitted(clock())
    """

    #: Where the current event is in its own little sequence. A phase is what
    #: keeps a wait from being re-decided: once the deadline wait has been
    #: taken, the event is committed and cannot then be dropped as superseded.
    _DEADLINE = "deadline"
    _GAP = "gap"
    _EMIT = "emit"

    __slots__ = ("_evs", "_emit_last", "_min_gap", "_i", "_phase", "_t0",
                 "_last_emit", "_ref", "_reach", "delivered")

    def __init__(self, evs: Iterable[Ev], *, emit_last: bool = True,
                 min_gap_ms: float = MIN_EVENT_INTERVAL_MS,
                 origin: Optional[Tuple[float, float]] = None) -> None:
        self._evs: List[Ev] = list(evs)
        self._emit_last = emit_last
        self._min_gap = float(min_gap_ms) / 1000.0
        self._i = 0
        self._phase = self._DEADLINE
        self._t0: Optional[float] = None
        self._last_emit: Optional[float] = None
        #: The last position that actually went out - the origin until then.
        self._ref: Optional[Tuple[float, float]] = (
            (float(origin[0]), float(origin[1])) if origin is not None
            else ((self._evs[0].x, self._evs[0].y) if self._evs else None))
        #: How far from `_ref` the event after a dropped one may lie: twice the
        #: plan's own largest step, i.e. one skipped sample at the plan's peak
        #: speed. Not a pixel constant - a plan drawn for a short hop and one
        #: drawn across the screen each get the ceiling their own shape sets.
        self._reach = 2.0 * max(
            (_distance(a.x, a.y, b.x, b.y)
             for a, b in zip(self._evs, self._evs[1:])), default=0.0)
        #: How many events have been reported emitted.
        self.delivered = 0

    def _droppable(self, i: int) -> bool:
        """May event *i* be skipped, handing its distance to the next one?"""
        ev = self._evs[i]
        if i == len(self._evs) - 1 or ev.is_wheel:
            return False
        nxt = self._evs[i + 1]
        if self._ref is None:
            return True
        return _distance(self._ref[0], self._ref[1], nxt.x, nxt.y) \
            <= self._reach + 1e-9

    def step(self, now: float) -> Tuple[str, Any]:
        """What to do at *now*: ``(SLEEP, seconds)``, ``(EMIT, ev)`` or
        ``(DONE, None)``.

        After ``(EMIT, ev)`` the driver must report back with :meth:`emitted` or
        :meth:`dropped` before stepping again.
        """
        if self._t0 is None:
            self._t0 = now
        n = len(self._evs)
        while self._i < n:
            ev = self._evs[self._i]
            last = self._i == n - 1
            droppable = self._droppable(self._i)

            if self._phase == self._DEADLINE:
                wait = self._t0 + ev.t_ms / 1000.0 - now
                if wait > 0:
                    # Committed: having waited for this instant, we do not then
                    # decide it has been overtaken.
                    self._phase = self._GAP
                    return (SLEEP, wait)
                if (droppable and self._i + 1 < n
                        and now >= self._t0 + self._evs[self._i + 1].t_ms / 1000.0):
                    self._advance()  # superseded: the next point is already due
                    continue
                self._phase = self._GAP

            if self._phase == self._GAP:
                # Catching up after an overslept deadline must not produce two
                # events in the same instant: that is an event rate no device
                # reports, and it is as visible as being late was. An event
                # that cannot be dropped - the last, a notch, or one whose
                # successor is out of reach - waits out the floor and goes.
                if self._last_emit is not None:
                    behind = self._min_gap - (now - self._last_emit)
                    if behind > 0:
                        if droppable:
                            self._advance()
                            continue
                        self._phase = self._EMIT
                        return (SLEEP, behind)
                self._phase = self._EMIT

            if last and not self._emit_last:
                self._i = n
                break
            return (EMIT, ev)
        return (DONE, None)

    def emitted(self, now: float) -> None:
        """The event :meth:`step` handed out went out at *now*."""
        self.delivered += 1
        self._last_emit = now
        ev = self._evs[self._i]
        self._ref = (ev.x, ev.y)
        self._advance()

    def dropped(self) -> None:
        """The event :meth:`step` handed out could not be carried.

        It leaves ``_last_emit`` alone deliberately: nothing was sent, so
        nothing has to be spaced away from it.
        """
        self._advance()

    def _advance(self) -> None:
        self._i += 1
        self._phase = self._DEADLINE


def _distance(x0: float, y0: float, x1: float, y1: float) -> float:
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def clamp_to_viewport(x: float, y: float, w: Optional[float],
                      h: Optional[float]) -> Tuple[float, float]:
    """Keep a waypoint somewhere an event can actually land.

    ⛔ NOT A TIDINESS MEASURE. A pointer event outside the viewport is not
    merely ignored: the browser parks the cursor at the origin, so one waypoint
    off the edge teleports the pointer to (0, 0) in the middle of a movement. A
    curved path near an edge goes outside on its own, without the caller aiming
    anywhere strange.

    Unknown size means unknown, so nothing is clamped: guessing a viewport would
    move points that were fine.

    The endpoint is the caller's to emit unclamped - it is where the movement
    has to end - so callers apply this to the waypoints and not to the
    destination.
    """
    if not w or not h:
        return x, y
    return (
        min(max(x, 0.0), float(w) - 1.0),
        min(max(y, 0.0), float(h) - 1.0),
    )


def fit_timeline(evs: List[Ev], budget_s: float) -> List[Ev]:
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
    out: List[Ev] = []
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


def drive(evs: Iterable[Ev], emit_move: Any, *, emit_wheel: Any = None,
          emit_last: bool = True, now: Any = None, sleep: Any = None,
          origin: Optional[Tuple[float, float]] = None) -> int:
    """The SYNCHRONOUS driver of :class:`Pacer`. Returns events sent.

    The asynchronous one lives in :mod:`._cursor`, because it has to ``await``
    both the sleep and the emit. This one exists for the in-process Juggler
    server, whose ops are ordinary blocking calls; the decisions are the pacer's
    in both cases.

    ``now`` and ``sleep`` are injectable so a test can drive a whole timeline
    without waiting for it. ``origin`` is where the pointer is before the plan
    starts, the reference for the pacer's reach until something is sent.
    """
    evs = list(evs)
    if not evs:
        return 0
    clock = now if now is not None else time.perf_counter
    wait = sleep if sleep is not None else time.sleep
    pacer = Pacer(evs, emit_last=emit_last, origin=origin)
    with fine_timer():
        while True:
            what, arg = pacer.step(clock())
            if what == DONE:
                break
            if what == SLEEP:
                wait(arg)
                continue
            if arg.is_wheel:
                if emit_wheel is None:
                    pacer.dropped()  # nothing here can carry a notch
                    continue
                emit_wheel(arg.dx, arg.dy)
            else:
                emit_move(arg.x, arg.y)
            pacer.emitted(clock())
    return pacer.delivered


__all__ = [
    "DONE",
    "EMIT",
    "Ev",
    "MIN_EVENT_INTERVAL_MS",
    "Pacer",
    "SLEEP",
    "TIMER_ENV",
    "clamp_to_viewport",
    "drive",
    "fit_timeline",
    "fine_timer",
]
