"""Macro-scale pointer behaviour planning (pure, no I/O, no browser).

WHY THIS LAYER EXISTS
---------------------
How a single move from A to B is *shaped* is only half of what a pointer
stream shows. The other half is what the pointer does between the moves,
and where the moves end:

  * a pointer that is perfectly still except for the second it takes to
    slide from one control to the next is still, measurably, a pointer
    that exists only in order to click. Measured on the current path, the
    cursor is motionless for ~96% of a session;
  * if every movement terminates on a clickable element then the SET OF
    ENDPOINTS is itself a signature, and no amount of per-path polish
    removes it. A human parks the cursor on empty page area, drags it out
    of the text being read, and fidgets while a page scrolls.

So the interesting statistics here are not "is this curve pretty" but
"how often does the pointer move at all", "how long are the silences",
"where do the movements end" and "does the shape of all of that differ
between two sessions".

DESIGN: PLAN, DO NOT PERFORM
----------------------------
Every function below is pure: it takes a seed and geometry and returns a
timeline of :class:`Step` objects. Something else - the caller - walks
that timeline and dispatches it. Nothing here imports Playwright, opens a
socket or reads a clock. That is what makes the behaviour testable with
plain arithmetic instead of a live page, and it is why the numbers in the
tests are assertions rather than screenshots.

The walker is ``_cursor``: it turns a timeline into pointer events against
absolute deadlines. What is planned here and what actually reaches the wire
therefore differ, and deliberately so - see ``_cursor`` for the rule that
decides which planned events survive a machine that cannot deliver them all.

DESIGN: THIS MODULE PLANS *WHERE AND WHEN*, NOT *HOW A STROKE IS SHAPED*
------------------------------------------------------------------------
There are two scales, and exactly one owner each. ``_motion`` owns the shape
and the pacing of a single stroke from A to B. This module owns everything
above that: how many strokes an action is made of, where they aim, how long
the hand waits between them, and what it does when it is not aiming at
anything.

The seam is the ``render`` argument threaded through every planner below. It
takes a start, an end and a kind, and returns the steps of that one stroke.
It is REQUIRED, not defaulted. This module used to carry its own stroke model
as a fallback, which meant two intra-move laws could ship in one package and a
detector would see a different one depending on which call site ran. That model
was deleted on 2026-07-26 and the seam made mandatory, so a call site that
forgets the renderer is a TypeError rather than a silent second law. ``_cursor``
injects one backed by ``_motion``, and it is the only stroke model in force
everywhere. Two stroke models running side by side in one product would be
two measurably different families of movement inside one session.

DESIGN: EVERY PARAMETER COMES FROM THE SESSION SEED
---------------------------------------------------
Pause length, drift amplitude, pointing speed, where the speed peak sits,
how readily the hand overshoots, how often it moves for no reason: all of
it is drawn per session from the seed (:class:`PointerPersona`). A number
compiled into the package is the same number in every install and every
account, and a fixed shape is an invariant across every session of every
user - which is exactly the property a behavioural classifier wants. Two
seeds must look like two people; one seed must reproduce exactly.

PROVENANCE OF THE NUMBERS
-------------------------
Each constant below carries a comment saying where it comes from. Two
labels are used and they are not interchangeable:

  ``[lit]``   grounded in the human motor-control / HCI literature
              (Fitts' law and its Shannon form; the Meyer et al. 1988
              optimised-submovement account of overshoot-and-correct;
              Flash & Hogan 1985 minimum-jerk velocity profiles; the
              8-12 Hz physiological tremor band; heavy-tailed, roughly
              log-normal dwell times in reading and browsing).
  ``[judg]``  chosen by judgement. Plausible, bounded, and deliberately
              randomised per session so that being wrong about the centre
              of a range costs less - but not measured. Do not quote a
              ``[judg]`` number as if it were evidence.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Step",
    "PointerPersona",
    "PlanStats",
    "initial_pointer",
    "landing_point",
    "plan_idle",
    "plan_scroll",
    "plan_approach",
    "plan_aimless_move",
    "steps_from_waypoints",
    "tail_within",
    "summarize",
    "BURST_GAP_MS",
]

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]  # x, y, w, h - top-left origin

# Two pointer events separated by more than this are two different bursts of
# motion, not one continuous movement. Used only by summarize() to split a
# timeline into "the pointer was moving" vs "the pointer was still".
# [judg] 200 ms is comfortably above any within-burst sampling gap (~8-25 ms)
# and comfortably below the shortest deliberate pause we ever emit (180 ms).
BURST_GAP_MS = 200.0

# Kinds whose arrival means the burst ended on a control the caller named.
_ON_CONTROL_KINDS = frozenset({"approach", "correct"})

# Kinds that are not pointer motion: a wheel notch, and pure elapsed time.
_WHEEL_KIND = "wheel"
_WAIT_KIND = "wait"
_NON_MOTION_KINDS = frozenset({_WHEEL_KIND, _WAIT_KIND})


# ──────────────────────────────────────────────────────────────────────
# Seed plumbing
# ──────────────────────────────────────────────────────────────────────

def _sub_seed(seed: int, tag: str) -> int:
    """FNV-1a mix - independent PRNG streams per logical bucket from one seed.

    The same idiom is used elsewhere in the package. Two different tags give
    two streams that advance independently, so adding a planner later cannot
    shift the numbers an existing planner produces for the same seed.
    """
    h = 0xCBF29CE484222325 ^ (seed & 0xFFFFFFFF)
    for c in tag.encode("ascii"):
        h ^= c
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h or 0xDEADBEEF


def _rng(seed: int, tag: str, nonce: int = 0) -> random.Random:
    return random.Random(_sub_seed(seed, f"{tag}:{nonce}"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


# ──────────────────────────────────────────────────────────────────────
# Step
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Step:
    """One entry on the planned timeline.

    ``delay_ms`` is the wall time to wait BEFORE acting on this step, so a
    plan is dispatched as::

        for s in steps:
            sleep(s.delay_ms / 1000)
            if s.kind == "wheel":
                mouse.wheel(s.dx, s.dy)
            else:
                mouse.move(s.x, s.y)

    ``x``/``y`` are always the pointer position after the step - a wheel step,
    and a ``"wait"`` step which does nothing at all beyond consuming time,
    repeat the position they are dispatched at, so a consumer can track the
    cursor by reading the last step of any kind.

    A ``"wait"`` step exists so a plan accounts for ALL of the wall time it
    covers, including the stretches where the right answer is "the pointer
    does nothing". Without it the timeline would silently under-report its own
    duration and every occupancy figure computed from it would be inflated.

    ``kind`` is descriptive, never load-bearing for dispatch beyond the wheel
    case. It exists so tests (and the caller's logging) can ask questions like
    "what fraction of motion bursts ended on a control".
    """

    x: float
    y: float
    delay_ms: float
    kind: str
    dx: float = 0.0
    dy: float = 0.0


#: What a `render` argument is: it plans ONE leg and hands back its steps.
#: Called as `render(start, end, kind=..., lead_ms=..., bounds=..., target_w=...)`
#: at line 346, which is the only place it is invoked.
#:
#: This name was used as an annotation six times in this module and DEFINED
#: NOWHERE - found 2026-07-28 by the first F821 run this repo has ever had. It
#: was harmless only because `from __future__ import annotations` never evaluates
#: an annotation, so the six were strings that happened to look like a type.
#: Anything calling `typing.get_type_hints()` on those functions would have
#: raised, and a reader had no way to find out what the parameter accepts.
Renderer = Callable[..., List["Step"]]


# ──────────────────────────────────────────────────────────────────────
# Persona - the per-session parameter draw
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PointerPersona:
    """The parameters of one session's hand. Drawn from the session seed.

    Every field is a range, and the range is the honest statement of what we
    know: the centre is an estimate, the width is the admission that people
    differ. A single hardcoded value would be a constant shared by every
    install, which is the failure mode this module exists to remove.
    """

    seed: int
    # Sampling: mean gap between pointer events inside one burst of motion.
    # [lit] A mouse reports at 125 Hz by default (8 ms) and the browser
    # coalesces mousemove to the frame clock (~16.7 ms at 60 Hz), so real
    # event spacing sits between those two. Drawn per session because polling
    # rate and refresh rate genuinely differ between machines.
    sample_ms: float
    # Fitts' law MT = a + b * ID, ID = log2(D/W + 1)  (Shannon form).
    # [lit] Mouse pointing regressions typically land near a = 0.1-0.2 s and
    # b = 0.1-0.2 s/bit. The point of using Fitts at all: movement time then
    # depends on distance AND target size, instead of every movement taking
    # about one second regardless.
    fitts_a_ms: float
    fitts_b_ms: float
    # Within-person variability of movement time for a repeated pointing task,
    # as the sigma of a log-normal multiplier. [judg] informed by the fact that
    # repeated-trial MT coefficients of variation in pointing studies sit in
    # the 15-25% band.
    mt_sigma: float
    # Where the speed peak sits, as a fraction of the movement.
    # [lit] Minimum-jerk reaching gives a symmetric bell peaking at 0.50;
    # measured reaches peak slightly early, ~0.40-0.47. Anything that peaks in
    # the first few percent and decays is not a hand.
    peak_frac: float
    # Lateral bow of a movement as a fraction of its straight-line distance
    # (sigma of a zero-mean draw, so direction of the bow is not fixed either).
    # [judg] A human path is gently curved, a few percent off the chord.
    curvature: float
    # Amplitude of the correlated micro-noise riding on a movement, px.
    # [lit] Physiological tremor is 8-12 Hz and sub-millimetre at the
    # fingertip; mouse friction absorbs most of it, so what reaches the cursor
    # is on the order of a pixel. [judg] the px figure itself.
    tremor_px: float
    # Typical amplitude of an idle drift - the unconscious small adjustment of
    # a hand resting on the mouse. [judg]
    drift_px: float
    # Typical amplitude of an idle reposition - deliberately parking the
    # cursor somewhere else, e.g. out of the text being read. [judg]
    reposition_px: float
    # How long an idle adjustment runs, as a multiplier. [judg] People differ
    # a great deal in how much they fiddle with the mouse while reading: some
    # hands are effectively parked, others creep continuously. This is the
    # single largest driver of what fraction of a session the pointer is in
    # motion, so it is deliberately a wide, per-session range rather than a
    # figure baked into the package.
    fidget_scale: float
    # Idle pause distribution: log-normal, median and sigma in log space.
    # [lit] Inter-action gaps while reading/browsing are heavy-tailed and
    # roughly log-normal, not uniform and not Gaussian: mostly a couple of
    # seconds, with a real tail of long stillness. [judg] the exact median.
    pause_median_ms: float
    pause_sigma: float
    # Multiplier on the distance/size-derived overshoot probability. [judg]
    # People differ a lot in how ballistic their pointing is.
    overshoot_bias: float
    # Probability that a movement in a session script goes nowhere in
    # particular instead of to a control. [judg] - the evidence here is
    # negative (100% of movements ending on a control is not a hand), not a
    # measured human rate.
    aimless_rate: float

    @classmethod
    def from_seed(cls, seed: int) -> "PointerPersona":
        r = _rng(seed, "pointer-persona")
        return cls(
            seed=seed,
            sample_ms=r.uniform(9.0, 21.0),
            fitts_a_ms=r.uniform(90.0, 210.0),
            fitts_b_ms=r.uniform(105.0, 195.0),
            mt_sigma=r.uniform(0.14, 0.26),
            peak_frac=r.uniform(0.36, 0.50),
            curvature=r.uniform(0.010, 0.038),
            tremor_px=r.uniform(0.35, 1.60),
            drift_px=r.uniform(4.0, 26.0),
            reposition_px=r.uniform(70.0, 380.0),
            fidget_scale=r.uniform(0.7, 2.6),
            pause_median_ms=r.uniform(1400.0, 4800.0),
            pause_sigma=r.uniform(0.75, 1.25),
            overshoot_bias=r.uniform(0.55, 1.45),
            aimless_rate=r.uniform(0.22, 0.48),
        )


# ──────────────────────────────────────────────────────────────────────
# Movement primitives
# ──────────────────────────────────────────────────────────────────────

def _fitts_ms(persona: PointerPersona, rng: random.Random,
              distance: float, target_w: float) -> float:
    """Movement time for a pointing motion. [lit] Fitts, Shannon form.

    ``target_w`` is the effective width of what is being aimed at along the
    direction of travel. A movement that aims at nothing in particular passes
    a large width, which is why parking the cursor is fast and hitting a small
    button is slow - the property a fixed ~1 s duration cannot express.
    """
    w = max(4.0, target_w)
    idx = math.log2(max(distance, 1.0) / w + 1.0)
    mt = persona.fitts_a_ms + persona.fitts_b_ms * idx
    mt *= math.exp(rng.gauss(0.0, persona.mt_sigma))
    # Floor: no aimed hand movement completes in under ~60 ms. [lit] simple
    # reaction time alone is ~150-200 ms; 60 ms is a deliberately loose floor
    # that only guards against a pathological log-normal draw.
    return _clamp(mt, 60.0, 6000.0)


def _leg(
    persona: PointerPersona,
    rng: random.Random,
    start: Point,
    end: Point,
    *,
    kind: str,
    bounds: Point,
    render: Renderer,
    duration_ms: Optional[float] = None,
    target_w: float = 220.0,
    noise_scale: float = 1.0,
    lead_ms: float = 0.0,
) -> List[Step]:
    """One stroke, drawn by whoever owns stroke shape in this configuration.

    ``target_w`` IS forwarded: Fitts' law needs the width of the thing being
    hit, and two points do not contain it. The renderer implements the law and
    was being fed a per-seed constant, so a stroke to a 20 px control and one
    to a 200 px control took the same time. Handing it the box is giving the
    model a fact, not overriding its pacing.

    ``duration_ms``/``noise_scale`` are NOT forwarded. Those are pacing, and a
    renderer that owns pacing owns it completely; a caller that set half of it
    would produce a stroke that is neither model's. That distinction survived
    the deletion of the built-in model - it is about which layer knows what,
    not about which model wins.
    """
    # No fallback. This module plans WHERE and WHEN a movement happens; the
    # stroke itself is always drawn by the injected renderer, which is backed
    # by _motion. A default model here would mean two intra-move laws could
    # ship in one package, and a detector would see a different one depending
    # on which call site ran - a signature we would be creating ourselves.
    # Required rather than defaulted, so a call site that forgets it is a
    # TypeError at import-time-of-use, not a silent second law.
    return render(start, end, kind=kind, lead_ms=lead_ms, bounds=bounds,
                  target_w=target_w)


def steps_from_waypoints(
    raw: Iterable[Any],
    *,
    kind: str,
    lead_ms: float = 0.0,
    bounds: Optional[Point] = None,
    skip_first: bool = True,
) -> List[Step]:
    """Adapt a foreign waypoint stream to this module's :class:`Step`.

    Accepts either objects carrying ``x`` / ``y`` / ``dt_ms`` or plain
    ``(x, y, dt_ms)`` triples, which is what lets a renderer be plugged in
    without this module importing it.

    ``skip_first`` drops the leading waypoint: a generator that returns a path
    inclusive of both endpoints starts on the pointer's current position, and
    dispatching that would be an event with zero displacement.

    Two adjacent waypoints that round to the same device pixel *and* are closer
    together than a device could report them are merged. A duplicate that is
    genuinely separated in time is left alone, because "no two consecutive
    events ever share a pixel" is as much an invariant as "every event is
    0.5 px off" - a hand holding still really does report the same pixel twice.

    The threshold is one millisecond: two points scheduled in the same instant
    are one sample written twice, while 40 ms apart is a hand holding still.

    Raising it to the 8 ms sampling floor was TRIED on 2026-07-26 and reverted,
    because it did not do what it was meant to: the duplicate rate through the
    macro path only moved from 0.098 to 0.095 (the in-binary generator is
    0.081). The duplicates are not merge-threshold artefacts - they come from
    the small reading-and-drifting movements the macro layer plans, whose
    displacements round to the same device pixel while being genuinely
    separated in time. Fixing them means changing those movements, not this
    merge. Recorded so the next person does not re-try the same lever.
    """
    out: List[Step] = []
    pending = float(lead_ms)
    last_key: Optional[Tuple[int, int]] = None
    for i, p in enumerate(raw):
        x = getattr(p, "x", None)
        if x is not None and hasattr(p, "y"):
            px, py = float(x), float(p.y)
            dt = float(getattr(p, "dt_ms", 0.0) or 0.0)
        else:
            seq = tuple(p)
            px, py = float(seq[0]), float(seq[1])
            dt = float(seq[2]) if len(seq) > 2 else 0.0
        if i == 0 and skip_first:
            pending += max(dt, 0.0)
            last_key = (round(px), round(py))
            continue
        pending += max(dt, 0.0)
        if bounds is not None:
            px = _clamp(px, 0.0, bounds[0])
            py = _clamp(py, 0.0, bounds[1])
        key = (round(px), round(py))
        if key == last_key and pending < 1.0:
            continue
        out.append(Step(x=px, y=py, delay_ms=pending, kind=kind))
        pending = 0.0
        last_key = key
    return out


def tail_within(steps: Sequence[Step], budget_ms: float,
                anchor: Point, viewport: Point) -> List[Step]:
    """The last ``budget_ms`` of a plan, re-anchored on the current position.

    A caller cannot travel back in time: when a plan covers a stretch of wall
    clock that has ALREADY passed, the only honest thing it can dispatch is the
    end of it. Taking the tail keeps the plan's own distribution of episode
    kinds and gaps instead of inventing a compressed one, and re-anchoring
    keeps the displacements while moving them to wherever the pointer actually
    is - the plan's absolute coordinates belong to a history that did not
    happen.
    """
    if budget_ms <= 0.0 or not steps:
        return []
    motion = [i for i, s in enumerate(steps) if s.kind not in _NON_MOTION_KINDS]
    if not motion:
        return []
    start = len(motion) - 1
    used = 0.0
    for j in range(len(motion) - 1, -1, -1):
        cost = steps[motion[j]].delay_ms
        if used + cost > budget_ms and j != len(motion) - 1:
            break
        used += cost
        start = j
        if used >= budget_ms:
            break
    kept = [steps[i] for i in motion[start:]]
    # The tail's first step is a DESTINATION, so the translation has to put the
    # point the pointer travels FROM onto the anchor - not the first
    # destination, which would make the movement start where it ends.
    prev = motion[start] - 1
    origin = (steps[prev].x, steps[prev].y) if prev >= 0 else (
        kept[0].x - 1.0, kept[0].y
    )
    dx, dy = anchor[0] - origin[0], anchor[1] - origin[1]
    out: List[Step] = []
    # Deduplicated only against the tail's own points, not against the anchor:
    # the tail is separated from whatever the pointer last did by a pause, and
    # an event that happens to land back on the current pixel after a pause is
    # a hand that did not go far, not a wire artefact.
    last_key: Optional[Tuple[int, int]] = None
    for s in kept:
        p = _in_viewport((s.x + dx, s.y + dy), viewport)
        key = (round(p[0]), round(p[1]))
        if key == last_key:
            continue
        out.append(Step(x=p[0], y=p[1], delay_ms=s.delay_ms, kind=s.kind))
        last_key = key
    return out


# ──────────────────────────────────────────────────────────────────────
# Where the pointer is when a page opens
# ──────────────────────────────────────────────────────────────────────

def initial_pointer(seed: int, viewport: Point) -> Point:
    """A plausible seeded resting position for the cursor at page load.

    Never the viewport origin. A pointer whose first movement of every
    session departs from (0, 0) says the session began with no pointer state
    at all, which no real navigation produces: the cursor is wherever the
    previous interaction left it.

    [judg] Two cases, with weights chosen by reasoning rather than
    measurement: the user arrived by clicking a link on the previous page (so
    the cursor sits somewhere in the content area), or by using browser chrome
    - address bar, a bookmark, a tab - which leaves it near the top edge.
    """
    w, h = viewport
    r = _rng(seed, "pointer-origin")
    if r.random() < 0.58:  # [judg] arrived via an in-page link
        return (r.uniform(0.18 * w, 0.82 * w), r.uniform(0.12 * h, 0.78 * h))
    return (r.uniform(0.08 * w, 0.72 * w), r.uniform(0.005 * h, 0.09 * h))


# ──────────────────────────────────────────────────────────────────────
# Idle motion - what a pointer does while a person reads
# ──────────────────────────────────────────────────────────────────────

def _pause_ms(persona: PointerPersona, rng: random.Random) -> float:
    """One idle pause. [lit] log-normal: mostly short, with a genuine tail.

    Clipped to [180 ms, 75 s]. The floor keeps a pause distinguishable from a
    within-burst sampling gap; the ceiling stops a single draw from swallowing
    a whole session. Both are [judg].
    """
    mu = math.log(persona.pause_median_ms)
    return _clamp(math.exp(rng.gauss(mu, persona.pause_sigma)), 180.0, 75000.0)


def _in_viewport(p: Point, viewport: Point, margin: float = 3.0) -> Point:
    w, h = viewport
    return (_clamp(p[0], margin, w - margin), _clamp(p[1], margin, h - margin))



# ── idle episodes ─────────────────────────────────────────────────────────
#
# What the pointer does during a "read". These were an if/elif chain inside
# plan_idle with the weights written as bare cumulative literals in the branch
# conditions - 0.46, 0.80, 0.94 - so the split a detector could measure was
# spread across four places and readable in none of them. As a table the
# weights are data, the ordering is checked below rather than assumed, and each
# episode is small enough to read whole.
#
# THE DRAW ORDER IS THE CONTRACT: plan_idle draws the pause, then the roll, then
# the chosen episode's own values. Reordering any of it changes every idle plan
# for every seed. Checked against 80 recorded cases and 603 steps.


@dataclass(frozen=True)
class _IdleContext:
    """Everything an episode needs, and the only way it changes the plan.

    ``get_pos`` rather than a position: a settle emits several twitches and each
    one starts where the last ended, so the episode has to see the cursor move
    as it emits.
    """

    persona: PointerPersona
    rng: random.Random
    viewport: Point
    render: Renderer
    pause_ms: float
    get_pos: Callable[[], Point]
    emit: Callable[[List[Step]], None]

    def nudge(self, amp: float) -> Point:
        """A destination ``amp`` px away in a uniformly random direction."""
        ang = self.rng.uniform(0.0, 2.0 * math.pi)
        pos = self.get_pos()
        return _in_viewport(
            (pos[0] + amp * math.cos(ang), pos[1] + amp * math.sin(ang)),
            self.viewport,
        )


def _idle_settle(c: _IdleContext) -> None:
    """1-3 near-invisible adjustments, a pixel or two.

    The hand is resting on the mouse; [lit] 8-12 Hz tremor plus the fact that
    mouse friction swallows most of it.
    """
    lead = c.pause_ms
    for _ in range(c.rng.randint(1, 3)):
        dest = c.nudge(c.persona.tremor_px * c.rng.uniform(1.0, 3.5))
        # A settle is a single small twitch, ~40-120 ms. [judg]
        seg = _leg(c.persona, c.rng, c.get_pos(), dest, kind="settle",
                   render=c.render,
                   duration_ms=c.rng.uniform(40.0, 120.0) * c.persona.fidget_scale,
                   bounds=c.viewport, lead_ms=lead)
        lead = c.rng.uniform(60.0, 400.0)  # [judg] gap between twitches
        c.emit(seg)


def _idle_drift(c: _IdleContext) -> None:
    """A slow, small displacement of a few to a few tens of px.

    Not an aimed movement - a hand relaxing - so it is slow for its amplitude.
    [judg] 200-900 ms, persona-scaled and capped so a very fidgety persona
    still cannot creep for seconds.
    """
    dest = c.nudge(c.persona.drift_px * c.rng.uniform(0.4, 2.2))
    c.emit(_leg(c.persona, c.rng, c.get_pos(), dest, kind="drift",
                render=c.render,
                duration_ms=min(c.rng.uniform(200.0, 900.0)
                                * c.persona.fidget_scale, 1600.0),
                bounds=c.viewport, lead_ms=c.pause_ms))


def _idle_park(c: _IdleContext) -> None:
    """Parking the cursor somewhere else entirely - out of the paragraph being
    read, towards a scrollbar, off to one side. Ends NOWHERE by construction.

    ``target_w`` huge: parking is not aimed at anything, so it is fast for its
    distance - the opposite end of Fitts from a click.
    """
    dest = c.nudge(c.persona.reposition_px * c.rng.uniform(0.35, 1.6))
    c.emit(_leg(c.persona, c.rng, c.get_pos(), dest, kind="park",
                render=c.render, target_w=420.0, bounds=c.viewport,
                lead_ms=c.pause_ms))


def _idle_trace(c: _IdleContext) -> None:
    """A slow mostly-horizontal sweep, the cursor loosely following a line."""
    span = c.rng.uniform(60.0, 340.0) * (1 if c.rng.random() < 0.5 else -1)
    pos = c.get_pos()
    dest = _in_viewport((pos[0] + span, pos[1] + c.rng.gauss(0.0, 8.0)),
                        c.viewport)
    c.emit(_leg(c.persona, c.rng, pos, dest, kind="trace", render=c.render,
                duration_ms=abs(span) * c.rng.uniform(2.2, 5.0),
                target_w=420.0, bounds=c.viewport, lead_ms=c.pause_ms))


@dataclass(frozen=True)
class _IdleEpisode:
    name: str
    upto: float          # cumulative weight; the roll picks the first one under it
    plan: Callable[[_IdleContext], None]


#: [judg] small fidgets dominate, deliberate repositioning is occasional. The
#: point is the shape, not the exact split - and every amplitude scales with the
#: session's persona, so the split a detector could measure is not the same
#: split in the next session.
_IDLE_EPISODES: tuple[_IdleEpisode, ...] = (
    _IdleEpisode("settle", 0.46, _idle_settle),
    _IdleEpisode("drift", 0.80, _idle_drift),
    _IdleEpisode("park", 0.94, _idle_park),
    _IdleEpisode("trace", 1.01, _idle_trace),   # > 1: the roll cannot fall past it
)

assert all(a.upto < b.upto for a, b in zip(_IDLE_EPISODES, _IDLE_EPISODES[1:])), (
    "cumulative weights must increase, or an episode is unreachable")
assert _IDLE_EPISODES[-1].upto > 1.0, (
    "the last episode must catch every roll, or plan_idle can emit nothing")


def plan_idle(
    seed: int,
    persona: PointerPersona,
    origin: Point,
    viewport: Point,
    duration_ms: float,
    *,
    nonce: int = 0,
    render: Renderer,
) -> List[Step]:
    """Plan what the pointer does during ``duration_ms`` of "reading".

    This is the piece that has no counterpart today: between two clicks the
    current behaviour is exactly nothing. The plan here is a sequence of
    (pause, small action) episodes:

      settle      1-3 near-invisible adjustments, a pixel or two. The hand is
                  resting on the mouse; [lit] 8-12 Hz tremor plus the fact
                  that mouse friction swallows most of it.
      drift       a slow, small displacement of a few to a few tens of px.
      reposition  parking the cursor somewhere else entirely - out of the
                  paragraph being read, towards a scrollbar, off to one side.
                  This one ends NOWHERE by construction.
      trace       a slow mostly-horizontal sweep, the cursor loosely following
                  a line of text.

    Weights are [judg]: small fidgets dominate, deliberate repositioning is
    occasional. The point is the shape, not the exact split - and every
    amplitude scales with the session's persona, so the split a detector could
    measure is not the same split in the next session.

    The plan covers exactly ``duration_ms``: when the next pause would overrun
    the budget the episodes stop and the leftover time is emitted as a single
    ``"wait"`` step. Silence is a legitimate outcome of an idle period - it
    just has to be accounted for rather than dropped, or every activity
    fraction computed from the timeline comes out too high.
    """
    r = _rng(seed, "idle", nonce)
    steps: List[Step] = []
    pos = origin
    elapsed = 0.0

    def emit(seg: List[Step]) -> None:
        """Append a planned segment and advance the clock and the cursor."""
        nonlocal pos, elapsed
        if not seg:
            return
        steps.extend(seg)
        pos = (seg[-1].x, seg[-1].y)
        # The pause is carried as the lead delay of the segment's first step,
        # so summing the segment counts it exactly once.
        elapsed += sum(s.delay_ms for s in seg)

    while True:
        pause = _pause_ms(persona, r)
        if elapsed + pause >= duration_ms:
            break
        before = elapsed

        roll = r.random()
        ctx = _IdleContext(persona=persona, rng=r, viewport=viewport,
                           render=render, pause_ms=pause,
                           get_pos=lambda: pos, emit=emit)
        for episode in _IDLE_EPISODES:
            if roll < episode.upto:
                episode.plan(ctx)
                break

        if elapsed == before:
            # The episode produced no event at all (a sub-pixel destination).
            # The pause still happened, so the clock has to move.
            elapsed += pause
        if elapsed >= duration_ms:
            break

    # Account for the rest of the read as elapsed time. Without this the
    # timeline would claim to cover only the part of the period in which
    # something happened, and every occupancy statistic taken from it would be
    # measuring the wrong denominator.
    # Measured against what the steps actually carry, not against `elapsed`:
    # an episode that produced no event still consumed its pause, and that
    # time has to end up somewhere or the plan under-reports its duration.
    residual = duration_ms - sum(s.delay_ms for s in steps)
    if residual >= 5.0:
        steps.append(Step(x=pos[0], y=pos[1], delay_ms=residual,
                          kind=_WAIT_KIND))
    return steps


# ──────────────────────────────────────────────────────────────────────
# Motion during scrolling
# ──────────────────────────────────────────────────────────────────────

def plan_scroll(
    seed: int,
    persona: PointerPersona,
    origin: Point,
    viewport: Point,
    *,
    ticks: int,
    tick_dy: float = 100.0,
    nonce: int = 0,
    render: Renderer,
) -> List[Step]:
    """Plan a scroll burst with the pointer motion that goes with it.

    A hand that is turning a wheel is a hand resting on a mouse, and a mouse
    under a moving finger does not hold perfectly still: the wheel finger
    flexes, the palm shifts, and the cursor creeps by a pixel or several.
    A wheel event stream with a byte-identical pointer position throughout is
    a hand that is not attached to the mouse.

    Returns a single interleaved timeline: ``kind == "wheel"`` steps carry the
    scroll delta in ``dy`` and repeat the current pointer position, everything
    else is a pointer move.

    [lit] a notched wheel produces discrete detents, so ticks are discrete and
    the inter-tick gap is short. [judg] 55-115 ms between detents while
    spinning, a creep of a pixel or two per tick, and an occasional larger
    settle when the burst ends.
    """
    r = _rng(seed, "scroll", nonce)
    steps: List[Step] = []
    pos = origin
    for i in range(max(ticks, 0)):
        gap = r.uniform(55.0, 115.0) if i else r.uniform(90.0, 260.0)
        steps.append(Step(x=pos[0], y=pos[1], delay_ms=gap,
                          kind=_WHEEL_KIND,
                          dy=tick_dy * r.uniform(0.85, 1.15)))
        # [judg] the pointer creeps on roughly half the detents.
        if r.random() < 0.5:
            amp = persona.tremor_px * r.uniform(1.0, 4.0)
            ang = r.uniform(0.0, 2.0 * math.pi)
            dest = _in_viewport(
                (pos[0] + amp * math.cos(ang), pos[1] + amp * math.sin(ang)),
                viewport,
            )
            seg = _leg(persona, r, pos, dest, kind="scroll_drift", render=render,
                       duration_ms=r.uniform(30.0, 90.0),
                       bounds=viewport, lead_ms=r.uniform(8.0, 40.0))
            if seg:
                steps.extend(seg)
                pos = (seg[-1].x, seg[-1].y)
    # [judg] a third of scroll bursts end with the hand shifting the mouse
    # properly, not just creeping.
    if ticks and r.random() < 0.33:
        amp = persona.drift_px * r.uniform(0.8, 2.5)
        ang = r.uniform(0.0, 2.0 * math.pi)
        dest = _in_viewport(
            (pos[0] + amp * math.cos(ang), pos[1] + amp * math.sin(ang)),
            viewport,
        )
        seg = _leg(persona, r, pos, dest, kind="drift", render=render,
                   duration_ms=r.uniform(120.0, 380.0),
                   bounds=viewport, lead_ms=r.uniform(60.0, 320.0))
        steps.extend(seg)
    return steps


# ──────────────────────────────────────────────────────────────────────
# Approach: overshoot and correction
# ──────────────────────────────────────────────────────────────────────

def _support_width(box: Box, ux: float, uy: float) -> float:
    """Extent of a rectangle along the direction of travel - the W in Fitts."""
    _, _, w, h = box
    return abs(w * ux) + abs(h * uy)


def landing_point(box: Box, rng: random.Random, *, spread: float = 0.20,
                  keep: float = 0.40) -> Point:
    """Where inside a control the pointer actually stops.

    [judg] Gaussian about the centre with sigma = ``spread`` of the box side,
    clipped to the middle ``2 * keep`` of it, so the landing is inside, biased
    to the middle, and essentially never the exact geometric centre. Landing on
    the centre pixel of every control is a stronger signal than any curve
    shape: it is one exact number, it is the same number for every install,
    and any handler can read it off a single event.

    The default clip is tight on purpose. This point is also handed to the
    automation layer as the click position, and a point far out towards a
    border is a point that a rounded corner, a rotated element or an inline box
    that does not fill its bounding rectangle can fail to contain.
    """
    bx, by, bw, bh = box
    cx, cy = bx + bw / 2.0, by + bh / 2.0
    x = _clamp(rng.gauss(cx, bw * spread), cx - keep * bw, cx + keep * bw)
    y = _clamp(rng.gauss(cy, bh * spread), cy - keep * bh, cy + keep * bh)
    return (x, y)


def overshoot_probability(persona: PointerPersona, distance: float,
                          width: float) -> float:
    """Probability that this movement overshoots and needs a correction.

    [lit] Meyer et al.'s optimised-submovement account: pointing is a fast
    ballistic primary submovement plus, when the primary misses, one or more
    slower corrective submovements. Corrections get more likely as the index
    of difficulty rises - far targets and small targets. [judg] the intercept
    and slope below, and the persona multiplier; they are set so that an easy
    move (ID ~ 1) corrects rarely and a hard one (ID ~ 6) corrects most of the
    time.
    """
    idx = math.log2(max(distance, 1.0) / max(width, 4.0) + 1.0)
    p = (0.10 + 0.085 * idx) * persona.overshoot_bias
    return _clamp(p, 0.03, 0.88)


def plan_approach(
    seed: int,
    persona: PointerPersona,
    origin: Point,
    target: Box,
    viewport: Point,
    *,
    nonce: int = 0,
    landing: Optional[Point] = None,
    render: Renderer,
) -> List[Step]:
    """Plan a movement that ends on a control, the way a hand ends on one.

    ``landing`` overrides where inside the control the pointer stops. The
    caller passes it when it also has to tell the automation layer where to
    click: the two must be the same point, or the pointer lands on one pixel
    and the click is dispatched at another.

    Three outcomes, in decreasing order of frequency for hard targets:

      overshoot + correct   the primary submovement carries past the target,
                            the eye catches it, a short corrective submovement
                            comes back. [lit] Meyer et al. 1988.
      undershoot + settle   the primary stops short and the hand creeps in.
      straight in           one submovement, lands, done.

    The dwell between the primary and the correction is not padding: [lit]
    visually-guided correction cannot start before roughly 100-150 ms of
    feedback delay, so the gap exists in real pointing and its absence is
    itself informative.
    """
    r = _rng(seed, "approach", nonce)
    drawn = landing_point(target, r)
    landing = drawn if landing is None else landing
    dx, dy = landing[0] - origin[0], landing[1] - origin[1]
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return []
    ux, uy = dx / dist, dy / dist
    width = max(_support_width(target, ux, uy), 4.0)

    steps: List[Step] = []
    if r.random() < overshoot_probability(persona, dist, width):
        # [lit] Primary-submovement endpoint scatter is roughly proportional
        # to the distance travelled; ~4-5% of D is the usual order. [judg] the
        # exact sigma and the lateral share.
        over = abs(r.gauss(0.0, 0.045 * dist)) + r.uniform(1.5, 4.0)
        over = _clamp(over, 2.0, max(8.0, 0.16 * dist))
        lateral = r.gauss(0.0, 0.35 * over)
        past = _in_viewport(
            (landing[0] + ux * over - uy * lateral,
             landing[1] + uy * over + ux * lateral),
            viewport,
        )
        # The primary submovement is ballistic: it is not aiming at the
        # control's width, so it is fast for its distance.
        steps += _leg(persona, r, origin, past, kind="approach", render=render,
                      target_w=max(width * 3.0, 160.0), bounds=viewport)
        pos = (steps[-1].x, steps[-1].y) if steps else origin
        # [lit] visual feedback latency before a correction can begin.
        dwell = _clamp(r.gauss(125.0, 40.0), 45.0, 280.0)
        corr = _leg(persona, r, pos, landing, kind="correct", render=render,
                    target_w=width, bounds=viewport, lead_ms=dwell)
        steps += corr
        # [judg] a second, tiny correction on a minority of trials - the hand
        # that nudges once more before clicking.
        if corr and r.random() < 0.22:
            micro = _in_viewport(
                (landing[0] + r.gauss(0.0, 1.6), landing[1] + r.gauss(0.0, 1.6)),
                viewport,
            )
            steps += _leg(persona, r, landing, micro, kind="correct",
                          render=render, duration_ms=r.uniform(50.0, 130.0),
                          bounds=viewport,
                          lead_ms=_clamp(r.gauss(110.0, 40.0), 40.0, 260.0))
    elif r.random() < 0.35:  # [judg] undershoot then creep in
        short = _clamp(abs(r.gauss(0.0, 0.035 * dist)) + 1.5, 1.5,
                       max(3.0, 0.10 * dist))
        stop = (landing[0] - ux * short, landing[1] - uy * short)
        steps += _leg(persona, r, origin, stop, kind="approach", render=render,
                      target_w=max(width * 2.0, 120.0), bounds=viewport)
        pos = (steps[-1].x, steps[-1].y) if steps else origin
        steps += _leg(persona, r, pos, landing, kind="correct", render=render,
                      duration_ms=r.uniform(60.0, 190.0), bounds=viewport,
                      lead_ms=_clamp(r.gauss(115.0, 40.0), 40.0, 260.0))
    else:
        steps += _leg(persona, r, origin, landing, kind="approach",
                      render=render, target_w=width, bounds=viewport)
    return steps


# ──────────────────────────────────────────────────────────────────────
# Movements that end nowhere
# ──────────────────────────────────────────────────────────────────────

def plan_aimless_move(
    seed: int,
    persona: PointerPersona,
    origin: Point,
    viewport: Point,
    *,
    avoid: Optional[Sequence[Box]] = None,
    nonce: int = 0,
    render: Renderer,
) -> List[Step]:
    """Plan a movement whose endpoint is not on anything.

    If every movement in a session terminates on a clickable element, the
    endpoints alone identify the session regardless of how good the paths
    between them are. ``avoid`` is the list of control boxes the caller knows
    about; the destination is redrawn until it falls outside all of them
    (bounded retries - if the viewport really is wall-to-wall controls we take
    the last draw rather than loop).
    """
    r = _rng(seed, "aimless", nonce)
    w, h = viewport
    boxes = list(avoid or ())
    dest = (w / 2.0, h / 2.0)
    for _ in range(16):
        cand = (r.uniform(0.04 * w, 0.96 * w), r.uniform(0.04 * h, 0.96 * h))
        dest = cand
        if not any(bx <= cand[0] <= bx + bw and by <= cand[1] <= by + bh
                   for bx, by, bw, bh in boxes):
            break
    # target_w large: nothing is being aimed at.
    return _leg(persona, r, origin, _in_viewport(dest, viewport),
                kind="park", render=render, target_w=380.0, bounds=viewport)


# NO SESSION-LEVEL PLANNER LIVES HERE
# -----------------------------------
# There used to be a ``plan_session(seed, viewport, script)`` that composed a
# whole session from a task script known in advance. It was deleted rather than
# wired, because nothing can call it: this package is driven one action at a
# time by somebody else's script, and the future of that script is not
# knowable when the first action arrives. Composition therefore belongs to the
# dispatcher, which does it incrementally - it is the thing that knows how much
# wall clock really elapsed between two actions, which is the input the
# composition actually needs. The tests keep a local composer for the
# whole-session statistics, where a fixed script is legitimate.


# ──────────────────────────────────────────────────────────────────────
# Statistics over a plan
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlanStats:
    """Descriptive statistics of a planned timeline. No I/O, pure arithmetic."""

    total_ms: float
    active_ms: float
    active_fraction: float
    n_steps: int
    n_moves: int
    n_wheel: int
    n_bursts: int
    n_duplicate_positions: int
    bursts_on_control: int
    on_control_fraction: float
    longest_pause_ms: float


def summarize(steps: Sequence[Step]) -> PlanStats:
    """Reduce a timeline to the numbers worth asserting on.

    ``active_ms`` is wall time spent inside a burst of motion: the delay of a
    move step counts as active when it is short enough to be part of a
    continuous movement (``BURST_GAP_MS``). The long delay that opens a burst
    is the pause before it and counts as still. Applied to the behaviour this
    module replaces - one ~1 s movement every twenty-odd seconds and nothing
    in between - this measure yields the ~4% that motivated the work, so the
    "after" number is comparable to the "before" number.
    """
    total = 0.0
    active = 0.0
    moves = wheel = bursts = dups = on_ctrl = 0
    longest_pause = 0.0
    prev_pos: Optional[Tuple[int, int]] = None
    in_burst = False
    last_move_kind = ""

    for s in steps:
        total += s.delay_ms
        if s.kind in _NON_MOTION_KINDS:
            if s.kind == _WHEEL_KIND:
                wheel += 1
            # Neither a wheel notch nor a wait is pointer motion; both close
            # any open burst and both count as time the pointer stood still.
            if in_burst:
                bursts += 1
                if last_move_kind in _ON_CONTROL_KINDS:
                    on_ctrl += 1
                in_burst = False
            longest_pause = max(longest_pause, s.delay_ms)
            # Two moves separated by a wait or a wheel notch are not duplicate
            # events even if they share a pixel, so the run is broken here.
            prev_pos = None
            continue
        moves += 1
        if s.delay_ms <= BURST_GAP_MS and in_burst:
            active += s.delay_ms
        else:
            if in_burst:
                bursts += 1
                if last_move_kind in _ON_CONTROL_KINDS:
                    on_ctrl += 1
            longest_pause = max(longest_pause, s.delay_ms)
            in_burst = True
        last_move_kind = s.kind
        pos = (round(s.x), round(s.y))
        if prev_pos is not None and pos == prev_pos:
            dups += 1
        prev_pos = pos
    if in_burst:
        bursts += 1
        if last_move_kind in _ON_CONTROL_KINDS:
            on_ctrl += 1

    return PlanStats(
        total_ms=total,
        active_ms=active,
        active_fraction=(active / total) if total > 0 else 0.0,
        n_steps=len(steps),
        n_moves=moves,
        n_wheel=wheel,
        n_bursts=bursts,
        n_duplicate_positions=dups,
        bursts_on_control=on_ctrl,
        on_control_fraction=(on_ctrl / bursts) if bursts else 0.0,
        longest_pause_ms=longest_pause,
    )
