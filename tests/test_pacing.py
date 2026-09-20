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


def _steps(out):
    pts = [(ev.x, ev.y) for ev, _ in out]
    return [((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            for (x0, y0), (x1, y1) in zip(pts, pts[1:])]


def test_a_hopelessly_late_machine_slows_the_hand_down_instead_of_teleporting_it():
    """⛔ THE KNOWN-BAD INPUT OF [B214]. Dropping overtaken points had no
    ceiling in SPACE: on a loaded machine nearly every point was overtaken, the
    destination survived, and one event carried 89% of an 820 px journey with
    a timestamp exactly where the plan put it. The plan's timing was honoured
    and the pointer teleported.

    The reach rule: an event may be dropped only while the one after it lies
    within twice the plan's largest step of the last position sent. Past that
    the event goes out LATE, and the plan overruns - which is asserted too,
    because it is the price and a test that hid it would be lying about the
    trade. To watch this fail, make `_droppable` ignore `_reach`.
    """
    evs = [Ev(10.0 * i, float(i), 0.0) for i in range(40)]   # steps of 1 px
    clock = Clock(drift=0.2)                                  # 200 ms late, every time
    out = run(evs, clock, min_gap_ms=0.0)
    assert out[-1][0] is evs[-1]
    assert max(_steps(out)) <= 2.0 + 1e-9, (
        "one event carried %.1f px of a plan whose largest step is 1 px"
        % max(_steps(out)))
    assert len(out) >= 20, "fewer than every other point survived: %d" % len(out)
    assert clock.now() > evs[-1].t_ms / 1000.0, (
        "the plan did not overrun, so nothing was sent late - the reach rule "
        "did not engage and the ceiling above held by accident")


def test_one_overslept_sample_is_still_dropped_and_the_plan_still_ends_on_time():
    """⛔ THE OTHER HALF, so the reach rule cannot be 'never drop'. The timer
    case - one sample overslept by the system tick - merges two steps into one,
    and that is within reach: it is dropped as before and the plan keeps its
    absolute deadlines. To watch this fail, set the reach to the plan's largest
    step instead of twice it: every drop then becomes a late send."""
    # 25 ms late on a 10 ms plan: every sleep overshoots the NEXT deadline
    # too, so every other event is overtaken - the same input as the
    # supersession test above, which must keep dropping.
    evs = [Ev(10.0 * i, float(i), 0.0) for i in range(7)]
    clock = Clock(drift=0.025)
    out = run(evs, clock, min_gap_ms=0.0)
    assert len(out) < 7, "nothing was dropped"
    assert max(_steps(out)) <= 2.0 + 1e-9
    last_planned = evs[-1].t_ms / 1000.0
    assert clock.now() - last_planned <= 0.025 * 2, (
        "the plan overran: %.3f s past its last deadline" % (clock.now() - last_planned))


def test_the_reach_is_measured_from_where_the_pointer_actually_is():
    """The first drops happen before anything has been sent, and the pointer
    is not at the plan's first point: it is at the ORIGIN the driver knows.
    Measured from the first point instead, the first jump is one step longer
    than the rule allows. To watch this fail, ignore `origin`."""
    # Three points already due when the pacer takes its first step - a driver
    # pre-empted before it could start - at 1, 2, 3 px from an origin at 0.
    # A point that has been WAITED for is committed and never dropped, so
    # the origin can only matter for points that are due on arrival.
    evs = [Ev(0.0, 1.0, 0.0), Ev(0.0, 2.0, 0.0), Ev(0.0, 3.0, 0.0),
           Ev(10.0, 4.0, 0.0), Ev(20.0, 5.0, 0.0)]
    with_origin = Pacer(evs, min_gap_ms=0.0, origin=(0.0, 0.0))
    what, first = with_origin.step(0.0)
    assert what == EMIT
    # From the origin, 2 px is within reach (two steps) and 3 px is not: the
    # first point is dropped, the second goes. Measured from the first point
    # instead, 3 px is within reach too and the pointer's first report would
    # be a 3 px jump from where it really is.
    assert (first.x, first.y) == (2.0, 0.0), (
        "the first event sent is %r: measured from the plan's first point, "
        "not from the origin" % ((first.x, first.y),))


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
