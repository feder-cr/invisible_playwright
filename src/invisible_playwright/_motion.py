"""Cursor motion planning - per-session mouse trajectories, in pure Python.

Why this lives here and not in the engine
-----------------------------------------
The trajectory the cursor follows between two points is a *shape*, and a shape
built out of hardcoded constants is an invariant: it is byte-for-byte the same
curve family for every movement, of every session, of every install of this
package. Anything that can be measured once can then be recognised forever, and
worse, it links every account a single user runs. So every number that decides
what a path looks like is drawn from the session seed instead of being written
in the source: the arm geometry, the number of control knots, the easing, the
duration law, the sampling interval, the tremor and its anisotropy, how often a
movement overshoots and how often two events land on the same device pixel.

Two seeds therefore produce measurably different *families* of paths, not just
different samples from one family. The same seed reproduces byte-identical
paths, because seed-reproducibility is a documented property of this package
and callers rely on it (``InvisiblePlaywright(seed=42)``).

Design notes worth keeping
--------------------------
* **Noise is zero-mean.** Tremor is drawn from ``gauss(0, sigma)``. A non-zero
  mean puts a systematic drift on every waypoint of every path ever produced,
  which is both a bug and a signature.
* **Noise is genuinely two-dimensional.** Tremor has an *along-travel* and an
  *across-travel* component with independent draws and their own scales, so the
  displacement covariance of a movement has two non-negligible eigenvalues. A
  tremor applied on a single direction is rank-1: every displacement in the
  movement is parallel to one vector, which is a one-line test to detect. It
  also leaves a whole screen axis noiseless whenever the movement happens to be
  horizontal or vertical, and an exactly noiseless axis lies on an analytic
  curve that can be solved from a handful of samples and used to predict the
  rest of the stream.
* **Every interior waypoint is displaced.** Not a random subset: a subset means
  a knowable fraction of the emitted points sit *exactly* on the underlying
  analytic curve, which is the same break in a slower form.
* **The speed profile is bell-shaped and drawn per movement.** Speed is zero at
  both ends, peaks somewhere near the middle, and the peak position is random
  per movement inside a per-seed sub-range. A single fixed easing is an
  invariant even when the positions differ; an easing that starts at maximum
  speed also implies infinite initial acceleration, which no hand does.
* **Duration follows a Fitts-style law**, so a long movement takes longer than a
  short one. A constant duration makes implied speed scale with distance, which
  is backwards.
* **Aiming overshoots, sometimes.** A ballistic reach lands past its target and
  is pulled back by a corrective submovement. A generator whose axial progress
  is monotone in 100.000% of its paths is as recognisable as one that always
  drifts the same way: "exactly zero, always" is a signature too. The overshoot
  here is bounded and its rate is per-seed.
* **Two consecutive events may share a device pixel.** Reported coordinates are
  integers, so a pointer crawling through the slow ends of a movement really
  does repeat one. A duplicate rate of exactly zero on every path is, again, an
  exact invariant. The rate is per-seed and bounded, and never becomes the long
  run of identical tail events that a fixed ease-out produces.
* **The per-seed difference lives in the SHAPE.** Fine timing does not survive
  dispatch (a scheduler quantises short sleeps), so a generator whose sessions
  differ only in tempo differs in nothing observable. The arm geometry below -
  a per-seed pivot direction that decides which way a stroke bows, given its
  direction on screen - is what makes two seeds trace visibly different curves
  between the same two points.

The module is pure: no Playwright, no network, no filesystem, no clock. Every
property it claims is verifiable with arithmetic alone - which is exactly what
lets ``tests/test_motion.py`` check its statistics without launching a browser.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left
from dataclasses import dataclass

__all__ = [
    "MotionStyle",
    "Waypoint",
    "CursorMotion",
    "style_for_seed",
    "MIN_STEPS",
    "MAX_STEPS",
    "MIN_DURATION_MS",
    "MAX_DURATION_MS",
    "SAMPLE_FLOOR_MS",
]


# ───────────────────────── hard bounds ─────────────────────────
# These are deliberately NOT per-seed: they are the envelope inside which every
# seed must land, and the tests assert them. A generator that is unpredictable
# but implausible is worse than a constant one.
MIN_STEPS = 2
MAX_STEPS = 160
MIN_DURATION_MS = 40.0
MAX_DURATION_MS = 2000.0

#: The fastest two pointer events can legitimately be apart. A 125 Hz mouse
#: reports every 8 ms and the browser coalesces on top of that; below this is
#: a rate no hardware produces.
SAMPLE_FLOOR_MS = 8.0

# Below this the movement is a sub-pixel nudge and gets a single waypoint.
_EPS = 1e-9


def _mix(seed: int, tag: str) -> int:
    """FNV-1a mix -> independent PRNG streams per logical bucket from one seed.

    Same convention used elsewhere in the package for deriving sub-streams; kept
    local so this module stays importable on its own with nothing but stdlib.
    """
    h = 0xCBF29CE484222325 ^ (int(seed) & 0xFFFFFFFFFFFFFFFF)
    for ch in tag.encode("utf-8"):
        h ^= ch
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h & 0x7FFFFFFF


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


# ───────────────────────── per-session style ─────────────────────────


@dataclass(frozen=True)
class MotionStyle:
    """The shape parameters of one session. All of it is drawn from the seed.

    Every field below replaces something that used to be a literal. The ranges
    are picked so that two installs are very unlikely to share a shape, while
    every single sample still reads as a hand moving a mouse.
    """

    # ── control polygon ────────────────────────────────────────────────
    # Number of interior control knots -> Bezier degree 2..4. 1 = a flick that
    # barely curves, 3 = a lazy arcing sweep. Above 3 the curve starts folding
    # back on itself and no hand does that.
    knots: int
    # Where those knots sit along the travel, as fractions. This is a per-SEED
    # base position, not a fresh uniform draw per movement: a session that
    # re-rolled the whole control polygon every time would have all its
    # between-movement variation and no between-session variation, which is the
    # opposite of what is wanted. ``knot_wobble`` is the per-movement spread
    # around the base.
    knot_base: tuple[float, ...]
    knot_wobble: float
    # Per-knot bow magnitude, order 1, per-seed. Signs may differ between knots,
    # which is what turns a plain arc into a gentle S.
    bow_base: tuple[float, ...]
    # Sideways bow scale: a fraction of the travel, plus a small absolute term
    # that keeps short movements from collapsing onto the straight line (if the
    # bow is purely proportional then every session draws the same near-straight
    # 40 px stroke and short movements carry no per-session information at all).
    # A padding measured purely *in pixels* instead makes short moves detour
    # several times their own distance, which is the giveaway it looks like; the
    # knee below fades the absolute term out under ~1 knee-length.
    bow_frac: float
    bow_abs_px: float
    bow_knee_px: float
    # Arm geometry. A stroke pivots about a joint, so which way it bows is not a
    # free coin flip per movement - it follows from the direction of travel
    # relative to the pivot. ``pivot_angle`` is where that joint sits for this
    # session; ``pivot_strength`` is how much of the bow it dictates, the rest
    # being drawn per movement. This is the field that makes two seeds trace
    # different curves between the SAME two points.
    pivot_angle: float
    pivot_strength: float
    # Handedness of the leftover random part (signed mean, in sd units). Kept
    # well under 1 sd so the sign still flips regularly.
    bow_bias: float
    # Absolute clamp on the bow, so a full-screen sweep cannot sail off-screen.
    # Also gives a provable bound on the path box via the Bezier convex hull.
    bow_cap_px: float
    # Below this distance the movement is a flick and uses a single knot.
    short_move_px: float
    # ── timing ─────────────────────────────────────────────────────────
    # Duration law  T = a + b * log2(dist / w + 1)  (Fitts). The classic mouse
    # figures are an intercept of roughly 100..250 ms and a slope of roughly
    # 100..200 ms per bit of index of difficulty; the ranges below sit at the
    # brisk end of that, since someone driving a browser is not aiming carefully
    # at every target, but they are not several times faster than a person.
    target_w_px: float
    fitts_a_ms: float
    fitts_b_ms: float
    # Per-movement multiplicative spread on that duration - nobody moves at a
    # constant tempo all session.
    dur_jitter: float
    # Nominal sampling interval. 8..18 ms is roughly 55..125 Hz of observed
    # mousemove, which is what a browser coalesces a real mouse down to.
    step_ms: float
    # Relative sd of each individual interval.
    step_jitter: float
    # Velocity profile family. Speed density is t^(a-1) * (1-t)^(b-1) with
    # a drawn in [ease_a_lo, ease_a_hi] and b = a * r, r in [ease_r_lo,
    # ease_r_hi], both redrawn per movement. Keeping a,b > 1 forces zero speed
    # at both ends (no infinite initial acceleration); the ranges keep the
    # speed peak, which sits at (a-1)/(a+b-2), between roughly 0.22 and 0.68 of
    # the movement - never at the very first step, never at the last.
    ease_a_lo: float
    ease_a_hi: float
    ease_r_lo: float
    ease_r_hi: float
    # ── tremor ─────────────────────────────────────────────────────────
    # sd of the across-travel component in px, and the along/across ratio. Both
    # components are drawn independently for every interior waypoint, so the
    # displacement cloud of one movement is an ellipse rather than a line.
    tremor_across_px: float
    tremor_aniso: float
    # Distance at which the tremor reaches full amplitude. A short nudge is a
    # placement, not a reach: it carries less tremor in absolute terms. Without
    # this the tremor is a fixed number of pixels, so a 10 px move ends up
    # travelling half as far again as the straight line - the same "detour
    # several times the distance" tell that a pixel-sized bow produces.
    tremor_full_px: float
    # Occasional larger flicker on top of the base tremor: probability and the
    # multiple of the base sd it adds. This is what gives the residual the
    # heavy tail a hand has, without a subset of points being left noiseless.
    tremor_burst_p: float
    tremor_burst_mult: float
    # Exponent of the sin() window that scales the tremor along the movement.
    # The window vanishes at both ends, which is both realistic (you are precise
    # where you aim) and what keeps the endpoints exact.
    tremor_shape: float
    # ── overshoot ──────────────────────────────────────────────────────
    # Probability a movement overshoots at all, how far past the target it goes
    # (fraction of the travel), the absolute cap on that, and the shortest
    # movement that bothers. Under the min distance a hand is placing, not
    # reaching, and does not overshoot.
    overshoot_p: float
    overshoot_frac: float
    overshoot_cap_px: float
    overshoot_min_px: float
    # Where in the movement the corrective phase begins, drawn per movement in
    # [lo, hi], and the exponent of its hump.
    over_start_lo: float
    over_start_hi: float
    over_shape: float
    # ── reporting ──────────────────────────────────────────────────────
    # Probability that a waypoint landing on the pixel already reported is kept
    # rather than dropped, and the longest run of identical positions allowed.
    # A long run is the legacy tail signature; zero duplicates is an invariant.
    dup_keep_p: float
    dup_run_max: int


def style_for_seed(seed: int) -> MotionStyle:
    """Draw one session's shape parameters from the session seed."""
    r = random.Random(_mix(seed, "motion:style"))

    # Three base knot positions, one per possible knot, kept apart so a 3-knot
    # session gets an early/middle/late polygon rather than three coincident
    # points. Sorted, and strictly inside (0, 1).
    knot_base = (
        r.uniform(0.12, 0.38),
        r.uniform(0.38, 0.62),
        r.uniform(0.62, 0.88),
    )
    # First knot always bows one way; the later ones may bow back, which is what
    # produces a gentle S instead of a plain arc for some sessions.
    bow_base = (
        r.uniform(0.55, 1.45),
        r.uniform(-0.70, 1.45),
        r.uniform(-0.70, 1.45),
    )

    # Velocity-profile family: a sub-window of the plausible range, so two
    # sessions do not merely draw different samples, they draw from different
    # sub-ranges. Width is itself random.
    a_lo = r.uniform(1.70, 2.60)
    a_hi = min(a_lo + r.uniform(0.60, 1.40), 3.60)
    if a_hi <= a_lo:  # only reachable if a_lo pinned at the top of its range
        a_hi = a_lo + 0.20
    r_lo = r.uniform(0.78, 1.20)
    r_hi = min(r_lo + r.uniform(0.25, 0.65), 1.85)

    over_lo = r.uniform(0.48, 0.62)
    over_hi = min(over_lo + r.uniform(0.12, 0.26), 0.88)

    return MotionStyle(
        knots=r.randint(1, 3),
        knot_base=knot_base,
        knot_wobble=r.uniform(0.02, 0.07),
        bow_base=bow_base,
        bow_frac=r.uniform(0.008, 0.042),
        bow_abs_px=r.uniform(1.0, 4.2),
        bow_knee_px=r.uniform(30.0, 90.0),
        pivot_angle=r.uniform(0.0, 2.0 * math.pi),
        pivot_strength=r.uniform(0.62, 0.96),
        bow_bias=r.uniform(-0.35, 0.35),
        bow_cap_px=r.uniform(40.0, 110.0),
        short_move_px=r.uniform(40.0, 90.0),
        target_w_px=r.uniform(28.0, 56.0),
        fitts_a_ms=r.uniform(90.0, 190.0),
        fitts_b_ms=r.uniform(90.0, 160.0),
        dur_jitter=r.uniform(0.10, 0.28),
        step_ms=r.uniform(8.0, 18.0),
        step_jitter=r.uniform(0.10, 0.35),
        ease_a_lo=a_lo,
        ease_a_hi=a_hi,
        ease_r_lo=r_lo,
        ease_r_hi=r_hi,
        tremor_across_px=r.uniform(0.16, 0.58),
        tremor_aniso=r.uniform(0.35, 0.95),
        tremor_full_px=r.uniform(70.0, 190.0),
        tremor_burst_p=r.uniform(0.05, 0.20),
        tremor_burst_mult=r.uniform(1.5, 2.8),
        tremor_shape=r.uniform(0.60, 1.60),
        overshoot_p=r.uniform(0.14, 0.48),
        overshoot_frac=r.uniform(0.010, 0.040),
        overshoot_cap_px=r.uniform(18.0, 45.0),
        overshoot_min_px=r.uniform(10.0, 34.0),
        over_start_lo=over_lo,
        over_start_hi=over_hi,
        over_shape=r.uniform(0.80, 1.60),
        dup_keep_p=r.uniform(0.10, 0.40),
        dup_run_max=r.randint(1, 3),
    )


# ───────────────────────── geometry helpers ─────────────────────────


def _bezier_point(ctrl: list[tuple[float, float]], t: float) -> tuple[float, float]:
    """De Casteljau. Degree is 2..4 here, so the O(n^2) form is free."""
    pts = ctrl
    while len(pts) > 1:
        pts = [
            (
                pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t,
            )
            for i in range(len(pts) - 1)
        ]
    return pts[0]


def _arc_table(
    ctrl: list[tuple[float, float]], n: int
) -> tuple[list[float], list[float]]:
    """Sample the curve uniformly in t and return (t values, cumulative length)."""
    ts = [i / (n - 1) for i in range(n)]
    pts = [_bezier_point(ctrl, t) for t in ts]
    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    return ts, cum


def _t_at_arclen(ts: list[float], cum: list[float], s: float) -> float:
    """Invert the arc-length table: linear interpolation between samples."""
    total = cum[-1]
    if total <= _EPS:
        return 0.0
    s = _clamp(s, 0.0, total)
    j = bisect_left(cum, s)
    if j <= 0:
        return ts[0]
    if j >= len(cum):
        return ts[-1]
    span = cum[j] - cum[j - 1]
    f = 0.0 if span <= _EPS else (s - cum[j - 1]) / span
    return ts[j - 1] + (ts[j] - ts[j - 1]) * f


def _profile_table(a: float, b: float, n: int = 65) -> list[float]:
    """Cumulative (normalised) distance profile for a Beta-shaped speed density.

    Speed density  v(t) = t^(a-1) * (1-t)^(b-1)  with a, b > 1: zero at both
    ends, single interior peak at (a-1)/(a+b-2). Integrating gives a monotone
    S(t) with S(0)=0 and S(1)=1 exactly.
    """
    ts = [i / (n - 1) for i in range(n)]
    dens = [(t ** (a - 1.0)) * ((1.0 - t) ** (b - 1.0)) for t in ts]
    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + 0.5 * (dens[i] + dens[i - 1]) * (ts[i] - ts[i - 1]))
    total = cum[-1]
    if total <= _EPS:  # unreachable for a,b > 1, but never divide by zero
        return [i / (n - 1) for i in range(n)]
    out = [c / total for c in cum]
    out[-1] = 1.0
    return out


def _profile_at(table: list[float], u: float) -> float:
    """Interpolate the cumulative profile at u in [0, 1]."""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    x = u * (len(table) - 1)
    i = int(x)
    if i >= len(table) - 1:
        return table[-1]
    return table[i] + (table[i + 1] - table[i]) * (x - i)


# ───────────────────────── the path ─────────────────────────


@dataclass(frozen=True)
class Waypoint:
    """One synthetic mousemove.

    ``dt_ms`` is how long to wait *before* dispatching this point (0 for the
    first); ``t_ms`` is the same schedule expressed as elapsed time from the
    start of the movement, for callers that prefer to correct their own drift.
    """

    x: float
    y: float
    dt_ms: float
    t_ms: float


@dataclass(frozen=True)
class _Axis:
    """The straight line from start to end, and the frame built on it.

    Every stage below places points as "so far along the travel, so far to the
    side of it", so the unit tangent and normal are computed once here rather
    than being threaded through six signatures as four loose floats.
    """

    from_x: float
    from_y: float
    to_x: float
    to_y: float
    dist: float
    ux: float
    uy: float
    nx: float
    ny: float

    @classmethod
    def between(cls, from_x: float, from_y: float,
                to_x: float, to_y: float) -> "_Axis":
        dx, dy = to_x - from_x, to_y - from_y
        dist = math.hypot(dx, dy)
        ux, uy = dx / dist, dy / dist
        return cls(float(from_x), float(from_y), float(to_x), float(to_y),
                   dist, ux, uy, -uy, ux)

    def at(self, along: float, across: float) -> tuple[float, float]:
        return (self.from_x + self.ux * along + self.nx * across,
                self.from_y + self.uy * along + self.ny * across)


@dataclass(frozen=True)
class _Overshoot:
    """A reach that lands past its target and is pulled back.

    A hump added to the position, mostly along the travel: it rises after
    ``u0``, peaks past the target, and returns to zero at u = 1, so the
    endpoint stays exact while the axial coordinate genuinely goes backwards on
    the way in. ``amp == 0`` means this movement did not overshoot.
    """

    amp: float = 0.0
    perp: float = 0.0
    u0: float = 1.0
    shape: float = 1.0

    def weight_at(self, u: float) -> float:
        """How much of the hump applies at fraction ``u``; 0 outside it.

        The WEIGHT, not the displacement. The caller multiplies in the same
        grouping the original expression used - see the note in
        ``_control_polygon`` about float multiplication not being associative;
        returning ``amp * w`` here and multiplying by the unit vector there
        regroups it and shifts the result in the last bit.
        """
        if self.amp <= 0.0 or u <= self.u0:
            return 0.0
        return math.sin(math.pi * (u - self.u0) / (1.0 - self.u0)) ** self.shape


# ── the stages, in the order _plan calls them ─────────────────────────────
#
# THE ORDER IS PART OF THE CONTRACT. Every stage draws from the same rng, so
# moving one past another changes every path for every seed - and `with_jitter`
# differencing depends specifically on the tremor being LAST among the drawing
# stages, so that switching it off leaves the rest of the stream untouched and
# the two runs can be subtracted point by point. Extracting these was checked
# against 576 recorded cases and 16135 waypoints for bit-identical output.

def _control_polygon(rng: random.Random, axis: _Axis,
                     st: MotionStyle) -> list[tuple[float, float]]:
    """Knots near their per-seed axial positions, displaced across the travel.

    How far, and on which side, is mostly decided by the session's arm
    geometry: a stroke swinging about a joint at ``pivot_angle`` bows away from
    that joint, so the same two endpoints give one session a left-hand arc and
    another a right-hand one. That is deterministic given the direction of
    travel, which is exactly why it separates sessions instead of averaging out
    over movements.
    """
    swing = -(math.cos(st.pivot_angle) * axis.nx + math.sin(st.pivot_angle) * axis.ny)
    amp_scale = (st.bow_frac * axis.dist
                 + st.bow_abs_px * axis.dist / (axis.dist + st.bow_knee_px))

    n_knots = 1 if axis.dist < st.short_move_px else st.knots
    axial = sorted(
        _clamp(st.knot_base[j] + rng.gauss(0.0, st.knot_wobble), 0.04, 0.96)
        for j in range(n_knots)
    )
    ctrl = [(axis.from_x, axis.from_y)]
    for j, u in enumerate(axial):
        mag = st.bow_base[j]
        amp = amp_scale * (
            st.pivot_strength * swing * mag
            + (1.0 - st.pivot_strength) * rng.gauss(st.bow_bias, 1.0) * mag
        )
        amp = _clamp(amp, -st.bow_cap_px, st.bow_cap_px)
        # Written as (ux * dist) * u, NOT ux * (dist * u), and not via
        # ``axis.at``. Floating-point multiplication is not associative, so the
        # two group differently in the last bit - which is enough to change a
        # rounded pixel and, through it, every downstream draw. Caught by a
        # 576-case golden comparison during the extraction of these stages,
        # having looked exactly like a safe tidy-up.
        ctrl.append((axis.from_x + axis.ux * axis.dist * u + axis.nx * amp,
                     axis.from_y + axis.uy * axis.dist * u + axis.ny * amp))
    ctrl.append((axis.to_x, axis.to_y))
    return ctrl


def _movement_duration(rng: random.Random, axis: _Axis, st: MotionStyle,
                       target_w: float | None) -> float:
    """Fitts, fed the WIDTH of the thing being hit.

    The generator cannot derive that width from two points. Until 2026-07-26 it
    always used the per-seed default, so a stroke to a 20 px control and one to
    a 200 px control took the same time - the law was implemented and then fed
    a constant. The planner knows the box; passing it is giving the model a
    fact, not taking over its pacing.
    """
    w = float(target_w) if target_w and target_w > 0 else st.target_w_px
    bits = math.log2(axis.dist / w + 1.0)
    # Log-normal spread, not Gaussian-with-a-floor: strictly positive so it
    # needs no clamp (a clamp piles probability mass on one exact value), and
    # measured human movement times are right-skewed anyway.
    duration = (st.fitts_a_ms + st.fitts_b_ms * bits) * math.exp(
        rng.gauss(0.0, st.dur_jitter))
    return _clamp(duration, MIN_DURATION_MS, MAX_DURATION_MS)


def _sample_times(rng: random.Random, duration: float,
                  st: MotionStyle) -> list[float]:
    """When each sample is taken, never closer together than a device can report.

    PHYSICAL FLOOR. The increments are log-normal with no lower bound, so the
    tail lands under any real sampling interval: measured 2026-07-26, 8.64% of
    emitted gaps were below 8 ms and the smallest was 3.52 ms, i.e. 284 Hz,
    while the per-seed nominal interval was a sane 9.7-17.9 ms. A mouse reports
    at 125 Hz (8 ms) and the browser coalesces on top of that, so a gap under
    that is not fast sampling - it is a rate no device produces, and it is
    measurable on the wire without any statistics.

    DROPPED, not compressed. Squeezing the gap back up would move every later
    point and change the movement's duration; dropping the offending sample
    keeps the schedule and the endpoint exactly as drawn. Same choice the
    dispatcher makes for the time cap: fewer events, never faster ones.
    """
    n_steps = int(round(duration / st.step_ms))
    n_steps = max(MIN_STEPS, min(MAX_STEPS, n_steps))

    # Strictly positive increments -> strictly increasing times.
    incs = [math.exp(rng.gauss(0.0, st.step_jitter)) for _ in range(n_steps)]
    total_inc = sum(incs)
    times = [0.0]
    acc = 0.0
    for c in incs:
        acc += c
        times.append(duration * acc / total_inc)
    times[-1] = duration

    if len(times) <= 2:
        return times
    kept = [times[0]]
    for tm in times[1:-1]:
        if tm - kept[-1] >= SAMPLE_FLOOR_MS:
            kept.append(tm)
    # The endpoint always survives; if the drop left it too close to its new
    # predecessor, that predecessor goes instead - a movement that stops short
    # is a worse defect than one sample fewer.
    if len(kept) > 1 and duration - kept[-1] < SAMPLE_FLOOR_MS:
        kept.pop()
    kept.append(duration)
    return kept


def _draw_overshoot(rng: random.Random, axis: _Axis,
                    st: MotionStyle) -> _Overshoot:
    if axis.dist < st.overshoot_min_px or rng.random() >= st.overshoot_p:
        return _Overshoot()
    amp = _clamp(st.overshoot_frac * axis.dist * math.exp(rng.gauss(0.0, 0.35)),
                 0.0, st.overshoot_cap_px)
    return _Overshoot(amp=amp,
                      perp=amp * rng.gauss(0.0, 0.40),
                      u0=rng.uniform(st.over_start_lo, st.over_start_hi),
                      shape=st.over_shape)


def _sample_curve(ctrl: list[tuple[float, float]], times: list[float],
                  duration: float, axis: _Axis, prof: list[float],
                  over: _Overshoot) -> list[tuple[float, float, float]]:
    """Walk the curve by ARC LENGTH, not by parameter.

    Sampling a Bezier at even parameter steps clusters points where the curve
    is tightly wound; the velocity profile is only a velocity profile if the
    distance covered follows it.
    """
    n_arc = max(24, min(240, int(axis.dist / 3.0) + 8))
    ts, cum = _arc_table(ctrl, n_arc)
    length = cum[-1]

    raw: list[tuple[float, float, float]] = []
    last = len(times) - 1
    for i, tm in enumerate(times):
        if i == 0:
            raw.append((axis.from_x, axis.from_y, 0.0))
            continue
        if i == last:
            raw.append((axis.to_x, axis.to_y, tm))
            continue
        u = tm / duration
        px, py = _bezier_point(ctrl, _t_at_arclen(ts, cum,
                                                  _profile_at(prof, u) * length))
        w = over.weight_at(u)
        if w:
            px += axis.ux * over.amp * w + axis.nx * over.perp * w
            py += axis.uy * over.amp * w + axis.ny * over.perp * w
        raw.append((px, py, tm))
    return raw


def _apply_tremor(rng: random.Random, raw: list[tuple[float, float, float]],
                  axis: _Axis, st: MotionStyle, duration: float) -> None:
    """Two independent components on every interior waypoint, in place.

    LAST among the drawing stages, deliberately: `with_jitter=False` skips only
    this, so both runs share an rng stream up to here and can be differenced
    point by point. That is how the zero-mean property and the tremor
    covariance are measured at all.
    """
    gain = min(1.0, math.sqrt(axis.dist / st.tremor_full_px))
    across_px = st.tremor_across_px * gain
    along_px = across_px * st.tremor_aniso
    for i in range(1, len(raw) - 1):
        px, py, tm = raw[i]
        w = math.sin(math.pi * (tm / duration)) ** st.tremor_shape
        a_off = rng.gauss(0.0, along_px)
        c_off = rng.gauss(0.0, across_px)
        if rng.random() < st.tremor_burst_p:
            a_off += rng.gauss(0.0, along_px * st.tremor_burst_mult)
            c_off += rng.gauss(0.0, across_px * st.tremor_burst_mult)
        a_off *= w
        c_off *= w
        raw[i] = (px + axis.ux * a_off + axis.nx * c_off,
                  py + axis.uy * a_off + axis.ny * c_off, tm)


def _collapse_duplicate_pixels(rng: random.Random,
                               raw: list[tuple[float, float, float]],
                               st: MotionStyle) -> list[Waypoint]:
    """Bounded runs of repeated device pixels.

    Keeping a repeat sometimes is the point (see the module docstring); what
    must not happen is the long run of identical tail events a fixed ease-out
    produces, so a run is cut at ``dup_run_max``. The final waypoint always
    carries the exact endpoint, whether it repeats a pixel or replaces the
    point that would have.
    """
    out: list[Waypoint] = []
    keys: list[tuple[int, int]] = []
    prev_t = 0.0
    run = 0
    for i, (px, py, tm) in enumerate(raw):
        key = (round(px), round(py))
        last = i == len(raw) - 1
        if keys and key == keys[-1]:
            run += 1
            if not (run <= st.dup_run_max and rng.random() < st.dup_keep_p):
                if not last:
                    continue
                # The endpoint is not negotiable: drop the point it repeats.
                out.pop()
                keys.pop()
                prev_t = out[-1].t_ms if out else 0.0
                run = 0
        else:
            run = 0
        out.append(Waypoint(px, py, tm - prev_t, tm))
        keys.append(key)
        prev_t = tm
    return out


def _plan(
    rng: random.Random,
    from_x: float,
    from_y: float,
    to_x: float,
    to_y: float,
    st: MotionStyle,
    *,
    with_jitter: bool = True,
    target_w: float | None = None,
) -> list[Waypoint]:
    """Build one path, as a pipeline of the stages above.

    ``rng`` supplies every per-movement draw, and the stage order is the draw
    order - see the note above the stages before reordering anything.

    ``with_jitter=False`` returns the same path with the tremor switched off
    and nothing else changed: same control polygon, same schedule, same
    overshoot.
    """
    # Sub-pixel move: a single event at the target. Anything else would emit
    # several mousemoves that all land on the same device pixel.
    if (math.hypot(to_x - from_x, to_y - from_y) < _EPS
            or (round(from_x) == round(to_x) and round(from_y) == round(to_y))):
        return [Waypoint(float(to_x), float(to_y), 0.0, 0.0)]

    axis = _Axis.between(from_x, from_y, to_x, to_y)

    ctrl = _control_polygon(rng, axis, st)
    duration = _movement_duration(rng, axis, st, target_w)
    times = _sample_times(rng, duration, st)
    ease_a = rng.uniform(st.ease_a_lo, st.ease_a_hi)
    prof = _profile_table(ease_a, ease_a * rng.uniform(st.ease_r_lo, st.ease_r_hi))
    over = _draw_overshoot(rng, axis, st)

    raw = _sample_curve(ctrl, times, duration, axis, prof, over)
    if with_jitter:
        _apply_tremor(rng, raw, axis, st, duration)
    return _collapse_duplicate_pixels(rng, raw, st)


class CursorMotion:
    """Per-session cursor path generator.

    ``CursorMotion(seed)`` fixes the session's shape family; each call to
    :meth:`path` draws one movement from it. The n-th movement of a given seed
    is always the same path, and each movement has its own independent PRNG
    stream, so inserting or removing a movement does not perturb the others.
    """

    __slots__ = ("_seed", "_style", "_count")

    def __init__(self, seed: int, *, style: MotionStyle | None = None) -> None:
        self._seed = int(seed)
        self._style = style if style is not None else style_for_seed(self._seed)
        self._count = 0

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def style(self) -> MotionStyle:
        return self._style

    @property
    def count(self) -> int:
        """How many movements have been generated on this instance."""
        return self._count

    def reset(self) -> None:
        """Rewind the movement counter (a fresh page/context starts over)."""
        self._count = 0

    def path(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        *,
        index: int | None = None,
        target_w: float | None = None,
    ) -> list[Waypoint]:
        """Waypoints from (from_x, from_y) to (to_x, to_y), inclusive of both.

        Pass ``index`` to address a specific movement of the session without
        advancing the counter (used by the tests; also handy for replay).
        """
        if index is None:
            index = self._count
            self._count += 1
        rng = random.Random(_mix(self._seed, "motion:move:%d" % int(index)))
        return _plan(rng, from_x, from_y, to_x, to_y, self._style,
                     target_w=target_w)


def total_ms(path: list[Waypoint]) -> float:
    """Wall time the whole movement is scheduled to take."""
    return path[-1].t_ms if path else 0.0
