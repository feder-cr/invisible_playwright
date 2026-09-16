"""The delivery discipline, driven by a clock that does what the test says.

⛔ WHY THIS FILE EXISTS. These rules used to be a loop inside `_cursor._dispatch`
and were only ever exercised through it. They now live in `_pacing` so the
server's drag can obey them too, and an extraction is exactly the kind of change
that keeps every existing test green while quietly altering a decision. So the
decisions are asserted here directly, against a clock the test owns.

Every test below drives the pacer by hand, which is what the two real drivers do
- one with `await`, one without - so a rule proved here is proved for both.
"""
from __future__ import annotations

import pytest

from invisible_playwright._pacing import (
    DONE, EMIT, SLEEP, Ev, Pacer, drive,
)

pytestmark = pytest.mark.unit


class Clock:
    """A clock that only moves when something asks it to.

    ``drift`` is how much LONGER than requested every sleep takes, which is the
    real behaviour this discipline exists for: Windows quantises a short sleep
    to the system tick, so a 12 ms request comes back at 16.7.
    """

    def __init__(self, drift: float = 0.0) -> None:
        self.t = 0.0
        self.drift = drift

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds + self.drift


def run(evs, clock, *, emit_last=True, min_gap_ms=8.0):
    """Walk a pacer to the end, returning what was emitted and when."""
    pacer = Pacer(evs, emit_last=emit_last, min_gap_ms=min_gap_ms)
    out = []
    while True:
        what, arg = pacer.step(clock.now())
        if what == DONE:
            break
        if what == SLEEP:
            clock.sleep(arg)
            continue
        assert what == EMIT
        out.append((arg, clock.now()))
        pacer.emitted(clock.now())
    assert pacer.delivered == len(out)
    return out


def plan(*t_ms):
    return [Ev(t, float(i), float(i)) for i, t in enumerate(t_ms)]


# ── on a machine that keeps up ──────────────────────────────────────────────

def test_a_machine_that_keeps_up_delivers_the_whole_plan():
    evs = plan(0, 20, 40, 60, 80)
    out = run(evs, Clock())
    assert [ev.t_ms for ev, _ in out] == [0, 20, 40, 60, 80]


def test_the_events_arrive_when_the_plan_said_they_would():
    out = run(plan(0, 20, 40), Clock())
    assert [round(at * 1000.0, 3) for _, at in out] == [0.0, 20.0, 40.0]


def test_an_empty_plan_asks_for_nothing():
    assert run([], Clock()) == []
    assert drive([], lambda x, y: None) == 0


# ── on a machine that does not ──────────────────────────────────────────────

def test_a_late_machine_drops_the_overtaken_points_rather_than_sending_them_late():
    """⛔ ASSERTED WITH NO MINIMUM GAP, so that this rule is the only one that
    can do the dropping.

    Its first version left the gap at its real value and stayed green when the
    supersession test was removed: on a late machine the min-gap rule drops the
    same events for its own reason, so the two are indistinguishable unless one
    of them is switched off. A mutation survived and said so.
    """
    out = run(plan(0, 10, 20, 30, 40, 50, 60), Clock(drift=0.025), min_gap_ms=0.0)
    assert len(out) < 7, "nothing was dropped, so the drift did not reach it"
    assert [ev.t_ms for ev, _ in out] == sorted(ev.t_ms for ev, _ in out)


def test_a_late_machine_still_keeps_the_stream_in_order_under_the_real_floor():
    out = run(plan(0, 10, 20, 30, 40, 50, 60), Clock(drift=0.025))
    assert [ev.t_ms for ev, _ in out] == sorted(ev.t_ms for ev, _ in out)


def test_the_destination_is_never_dropped():
    """⛔ A movement that stops short of where it was asked to go is a worse
    defect than one sample fewer - and on an element-targeted action it is the
    difference between clicking the thing and clicking past it."""
    evs = plan(0, 10, 20, 30, 40)
    out = run(evs, Clock(drift=0.5))
    assert out[-1][0] is evs[-1]


def test_lateness_does_not_accumulate_because_the_deadlines_are_absolute():
    """⛔ THE KNOWN-BAD INPUT FOR THE DEADLINE RULE. To watch it fail, make the
    deadline relative - `t0 = now` at the top of each step instead of once -
    and every overslept event then pushes the next one out by the same amount,
    so a plan of 10 events lands minutes from where it was drawn."""
    clock = Clock(drift=0.004)
    out = run(plan(0, 50, 100, 150, 200), clock)
    last_planned = out[-1][0].t_ms / 1000.0
    # One drift at the end is the cost of the last sleep; a per-event pile-up
    # would be five of them.
    assert clock.now() - last_planned <= 0.0045 * 2


def test_two_events_are_never_sent_at_one_instant():
    """That is an event rate no device reports, and it is as visible as being
    late was. The plan below asks for a rate no machine can serve."""
    out = run(plan(0, 1, 2, 3, 4, 5, 6, 40), Clock(drift=0.012))
    times = [at for _, at in out]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.008 - 1e-9 for g in gaps), gaps


def test_waiting_for_an_instant_commits_to_it():
    """⛔ THE KNOWN-BAD INPUT OF THE EXTRACTION ITSELF, and the one a careless
    split loses. Having slept until an event's deadline, the pacer must not then
    decide that event has been overtaken: the wait has already been paid, and
    dropping it now spends the time and sends nothing.

    To watch it fail, delete the `self._phase = self._GAP` assignment before the
    SLEEP return in the deadline branch, so the next step re-runs the
    supersession test on an event it has already waited for.
    """
    # The sleep overshoots past the NEXT event's deadline, so on re-entry a
    # phase-less pacer would find this one superseded and drop it.
    clock = Clock(drift=0.030)
    out = run(plan(10, 12, 400), clock)
    assert out[0][0].t_ms == 10, (
        "the event we waited for was then dropped as superseded: %r"
        % [ev.t_ms for ev, _ in out])


# ── what is never droppable ─────────────────────────────────────────────────

def test_a_wheel_notch_survives_a_machine_that_drops_everything_else():
    """It carries a delta nobody else will send: drop it and the page is
    scrolled to the wrong place, not merely scrolled less smoothly."""
    evs = [Ev(0, 0, 0), Ev(5, 1, 1), Ev(6, 0, 0, "wheel", 0.0, -120.0),
           Ev(7, 2, 2), Ev(200, 3, 3)]
    out = run(evs, Clock(drift=0.05))
    assert any(ev.is_wheel for ev, _ in out)


def test_a_notch_nobody_can_carry_is_reported_dropped_and_does_not_space_the_next():
    """A driver with no wheel emitter must not leave the pacer believing an
    event went out at that instant."""
    evs = [Ev(0, 0, 0, "wheel", 0.0, -120.0), Ev(1, 1.0, 1.0)]
    pacer = Pacer(evs, min_gap_ms=8.0)
    clock = Clock()
    what, arg = pacer.step(clock.now())
    while what == SLEEP:
        clock.sleep(arg)
        what, arg = pacer.step(clock.now())
    assert arg.is_wheel
    pacer.dropped()
    moved = []
    while True:
        what, arg = pacer.step(clock.now())
        if what == DONE:
            break
        if what == SLEEP:
            clock.sleep(arg)
            continue
        moved.append(arg)
        pacer.emitted(clock.now())
    assert [ev.x for ev in moved] == [1.0], (
        "the move was spaced away from an event that never went out")
    assert pacer.delivered == 1


def test_the_last_event_can_be_withheld_on_request():
    """`emit_last=False` is how an approach stops one sample short and lets the
    action itself place the final event on the point it computed."""
    out = run(plan(0, 20, 40), Clock(), emit_last=False)
    assert [ev.t_ms for ev, _ in out] == [0, 20]


# ── the synchronous driver ──────────────────────────────────────────────────

def test_the_synchronous_driver_obeys_the_same_rules(monkeypatch):
    """It is the server's, and it has to be the pacer's decisions it carries
    out, not its own."""
    clock = Clock()

    sent = []
    delivered = drive(plan(0, 20, 40, 60), lambda x, y: sent.append((x, y)),
                      now=clock.now, sleep=clock.sleep)
    assert delivered == len(sent) == 4
    assert round(clock.now() * 1000.0, 3) == 60.0


def test_the_synchronous_driver_drops_what_the_pacer_drops():
    clock = Clock(drift=0.025)
    sent = []
    delivered = drive(plan(0, 10, 20, 30, 40, 50, 60),
                      lambda x, y: sent.append((x, y)),
                      now=clock.now, sleep=clock.sleep)
    assert delivered == len(sent) < 7
