"""Statistical tests for the cursor motion planner.

Everything here runs offline, on the trajectory function itself, with plain
arithmetic - no browser, no network, no filesystem. That is the whole point of
having the generator in Python: its statistics can be asserted in CI.

The file also carries a faithful copy of the *legacy in-engine* generator (the
one that lives in the browser and expands a single mousemove into a Bezier path
of its own). It is here for one reason: a gate that has only ever printed PASS
is not a gate.

TWO CONVENTIONS THIS FILE ENFORCES STRUCTURALLY
-----------------------------------------------
**One assertion, used twice.** Every check that a known-bad generator can be
*fed* is factored into a named ``check_*`` function, and that same function is
called both by the forward test and by a ``..._rejects_...`` test that requires
it to raise. Writing the assertion once for each arm is how the two arms drift
apart and how a check quietly stops discriminating.

**A rate that must not be exactly zero is asserted as a BAND, and the band is
proved at both ends.** "This never happens" is itself a signature. A generator
whose paths backtrack in 0.000% of cases, or repeat a device pixel in 0.000% of
cases, is exactly as recognisable as one that always drifts +0.5 px: both are an
exact invariant shared by every install. So those checks assert a plausible
interval and have TWO known-bad arms - one that never does the thing, one that
does it far too much.

WHICH CHECKS DISCRIMINATE, AND AGAINST WHAT
-------------------------------------------
There are two known-bad generators in play and they are not the same thing. The
LEGACY one is the in-engine expander transcribed below. The PREVIOUS one is the
immediately preceding revision of ``_motion`` itself - the state an independent
verification measured, which had fixed the legacy's drift and introduced its own
defects. Where a check only rejects one of the two, this table says which.

    assert_zero_mean                  legacy jitter is drawn with mu = 1, so
                                      its mean residual is ~ +0.50 px
    check_both_axes_carry_noise       legacy jitters y only; sd(x) is exactly 0.
                                      Rejects PREVIOUS on axis-ALIGNED
                                      movements, where its travel-normal tremor
                                      left sd(x) at exactly 0 - and passes it on
                                      a diagonal, correctly, because there the
                                      tremor did reach both axes. The check is
                                      run on all three orientations for exactly
                                      that reason.
    check_tremor_is_two_dimensional   legacy displacements are all parallel to
                                      one screen axis, so the cloud has one
                                      eigenvalue. Rejects PREVIOUS on every
                                      orientation: sd(along)/sd(across) was
                                      7.1e-14.
    check_not_predictable             legacy x is a cubic at a uniform
                                      parameter, so a cubic through four
                                      neighbours recovers the fifth exactly.
                                      Rejects PREVIOUS per-axis on axis-aligned
                                      movements (median x error 0.025 px on a
                                      horizontal move, against 0.24 px now).
    check_every_waypoint_is_displaced rejects PREVIOUS, which displaced a random
                                      SUBSET and so left 36% of the interior
                                      waypoints sitting exactly on the analytic
                                      curve.
    check_peak_distribution           legacy's median peak sits at ~2% of the
                                      movement; also rejects a peak position
                                      that is the same number every time. Does
                                      NOT reject PREVIOUS: its profile was
                                      already bell-shaped and already per
                                      movement. This check guards that, it did
                                      not repair it.
    check_detour_is_a_few_percent     legacy travels ~4.3x the straight-line
                                      distance on a sub-50 px move
    check_duration_grows_with_distance
                                      legacy takes 1003 ms for 25 px and
                                      1003 ms for 1200 px
    check_backtrack_rate              BOTH ends: a strictly monotone planner -
                                      which PREVIOUS was, 0.000000 on every one
                                      of 12000 paths - and the legacy generator
                                      (mean 0.045, p95 0.22)
    check_duplicate_rate              BOTH ends: a planner that filters every
                                      repeat, which PREVIOUS did, and the legacy
                                      generator (8% of events, and a 3+ run on
                                      EVERY movement)
    check_shape_diverges              rejects PREVIOUS at 1.08 / 1.05 / 1.05 for
                                      50 / 200 / 600 px, and rejects the
                                      synthetic case of handing every seed the
                                      same style

Two checks do NOT discriminate against either generator, and naming them is the
point of this list:

  * the implied-speed envelope. Legacy's median mean-speed is ~495 px/s and its
    maximum ~1270 px/s - both comfortably inside a human envelope. It passes.
    That check is a plausibility floor and ceiling, not a discriminator, and it
    has no known-bad arm.
  * the velocity-profile spread. Measured as an arc-length profile, the legacy
    generator varies a great deal (interior sd 0.03-0.14) because its control
    knots are random, so it passes that check too. What is actually frozen in
    the legacy generator is its time MAP, and that is a different quantity;
    ``test_legacy_velocity_profile_is_effectively_frozen`` pins the time map
    directly and is the known-bad evidence for this property.

Everything else here is structural - endpoints exact, provable bounding box,
step-count envelope, module purity, seed reproducibility, style ranges. The
legacy generator has no seed, no style and no per-session anything, so there is
nothing to feed those; they are absent from the list above and claim no
known-bad arm.
"""

from __future__ import annotations

import ast
import dataclasses
import math
import random
from pathlib import Path

import pytest

from invisible_playwright._motion import (
    MAX_DURATION_MS,
    MAX_STEPS,
    MIN_DURATION_MS,
    CursorMotion,
    MotionStyle,
    Waypoint,
    _mix,
    _plan,
    style_for_seed,
    total_ms,
)

pytestmark = pytest.mark.unit


# ════════════════════════════════════════════════════════════════════
# helpers
# ════════════════════════════════════════════════════════════════════


def _mean(vals) -> float:
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0


def _sd(vals) -> float:
    vals = list(vals)
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _pct(vals, q: float) -> float:
    s = sorted(vals)
    assert s
    return s[min(len(s) - 1, int(q * len(s)))]


def _mean_se(vals: list[float]) -> tuple[float, float]:
    """Sample mean and standard error of the mean."""
    n = len(vals)
    assert n > 1
    return _mean(vals), _sd(vals) / math.sqrt(n)


def _corr(a: list[float], b: list[float]) -> float:
    sa, sb = _sd(a), _sd(b)
    if sa <= 1e-12 or sb <= 1e-12:
        # One of the two directions carries no spread at all: the cloud is a
        # line, which is the most rank-1 a cloud can be.
        return 1.0
    cov = _mean(x * y for x, y in zip(a, b)) - _mean(a) * _mean(b)
    return cov / (sa * sb)


# ════════════════════════════════════════════════════════════════════
# the checks, as functions
# ════════════════════════════════════════════════════════════════════
#
# Each of these is the ONE definition of a property. The forward test calls it
# on our generator's output; the matching known-bad test calls the very same
# function on the legacy generator's output inside ``pytest.raises``. Writing
# the assertion twice - once for each arm - is how the two arms drift apart and
# how a check quietly stops discriminating, so it is written once.


def assert_zero_mean(vals: list[float], tol: float, label: str) -> None:
    """The noise must be centred on zero.

    Two conditions, both must hold:
      * |mean| below an absolute tolerance in pixels (``tol``), and
      * |mean| below 5 standard errors, so the test tightens automatically as
        the sample grows instead of going quiet.
    """
    m, se = _mean_se(vals)
    assert abs(m) < tol, f"{label}: mean {m:+.4f} px exceeds tolerance {tol} px (n={len(vals)})"
    assert abs(m) < 5.0 * se, (
        f"{label}: mean {m:+.4f} px is {abs(m) / se:.1f} standard errors from zero (n={len(vals)})"
    )


def assert_in_band(value: float, lo: float, hi: float, label: str) -> None:
    """A rate that must be neither exactly zero nor implausibly large.

    Both ends are load-bearing. Below ``lo`` the behaviour never happens, which
    is an exact invariant a detector can key on; above ``hi`` it happens so
    often that the pointer stops reading as a hand.
    """
    assert lo <= value <= hi, f"{label}: {value:.6f} outside the plausible band [{lo}, {hi}]"


AXIS_NOISE_SD_MIN = 0.05


def check_both_axes_carry_noise(dx: list[float], dy: list[float]) -> None:
    """Noise confined to one screen axis leaves the other on an exact curve.

    A path whose x is analytic can have its control polygon solved from a
    handful of samples, so "there is noise" is not enough: it has to reach both
    screen axes. Note this is fed axis-ALIGNED movements too. A tremor applied
    along the travel normal passes on a diagonal and fails on a horizontal one,
    because there the normal *is* the y axis - the legacy break, restricted.
    """
    assert _sd(dx) > AXIS_NOISE_SD_MIN, f"x carries no noise at all (sd={_sd(dx):.6f})"
    assert _sd(dy) > AXIS_NOISE_SD_MIN, f"y carries no noise at all (sd={_sd(dy):.6f})"
    assert len({round(v, 12) for v in dx}) > 200, "x takes almost no distinct values"
    assert len({round(v, 12) for v in dy}) > 200, "y takes almost no distinct values"
    assert_zero_mean(dx, 0.05, "x noise")
    assert_zero_mean(dy, 0.05, "y noise")


def check_tremor_is_two_dimensional(movements: list[list[tuple[float, float]]]) -> None:
    """The displacement cloud of one movement must be an ellipse, not a line.

    ``movements`` is one list of ``(along, across)`` displacement pairs per
    movement, in travel coordinates. Noise injected on a single direction makes
    every displacement in a movement parallel to one vector: the covariance has
    one non-negligible eigenvalue. Two ways that shows up, and both are checked
    because they catch different bugs -

      * the spread ratio collapses (a tremor applied only across the travel has
        sd(along) = 0, so the ratio is floating-point dust), and
      * the correlation goes to 1 (a tremor applied along one fixed SCREEN axis
        has spread on both travel components, but they are the same number
        twice, scaled).
    """
    ratios: list[float] = []
    corrs: list[float] = []
    for res in movements:
        if len(res) < 10:
            continue
        along = [p[0] for p in res]
        across = [p[1] for p in res]
        sa, sc = _sd(along), _sd(across)
        ratios.append(sa / sc if sc > 1e-12 else 0.0)
        corrs.append(abs(_corr(along, across)))
    assert len(ratios) > 50, f"sample too small: {len(ratios)} movements"
    assert 0.20 < _pct(ratios, 0.5) < 2.5, f"median sd(along)/sd(across) = {_pct(ratios, 0.5):.4e}"
    assert _pct(ratios, 0.05) > 0.15, f"p05 of the spread ratio {_pct(ratios, 0.05):.4e}"
    assert _pct(ratios, 0.95) < 3.0, f"p95 of the spread ratio {_pct(ratios, 0.95):.4e}"
    assert _sd(ratios) > 0.05, "the anisotropy is one fixed constant"
    assert _pct(corrs, 0.5) < 0.35, f"median |corr(along, across)| = {_pct(corrs, 0.5):.4f}"
    assert _pct(corrs, 0.95) < 0.80, f"p95 |corr(along, across)| = {_pct(corrs, 0.95):.4f}"


PREDICTION_ERROR_MIN_PX = 0.05


def check_not_predictable(errors_by_axis: dict[str, list[float]]) -> None:
    """A cubic through four waypoints must not recover the fifth.

    ``errors_by_axis`` maps "x" / "y" to |predicted - observed| in px, one entry
    per predictable point. Checked per AXIS and not pooled, which is the whole
    point: a tremor applied along the travel normal leaves the *other* screen
    axis analytic whenever the movement is axis-aligned, and pooling the two
    axes hides that behind the noisy one. Measured on the previous generator,
    a horizontal movement gave a median x error of 0.025 px against 0.265 px on
    y - the pooled median stayed comfortably above any threshold that the x
    figure alone would have failed.
    """
    for axis, errors in errors_by_axis.items():
        assert len(errors) > 200, f"{axis}: sample too small ({len(errors)})"
        median = _pct(errors, 0.5)
        assert median > PREDICTION_ERROR_MIN_PX, (
            f"{axis}: median prediction error {median:.3e} px - that axis is a formula"
        )
        precise = sum(1 for v in errors if v < 1e-6) / len(errors)
        assert precise < 0.01, (
            f"{axis}: {precise:.1%} of points predicted to floating-point precision"
        )


def check_every_waypoint_is_displaced(clean: int, total: int) -> None:
    """Displacing a random SUBSET is the same break in a slower form.

    If only a fraction p of the waypoints are moved then 1-p of the emitted
    points lie exactly on the analytic curve. Four of those solve it, and every
    other clean point then confirms the solution for free - the displaced ones
    can simply be discarded as outliers.
    """
    assert total > 2_000, f"sample too small: {total}"
    assert clean == 0, f"{clean} of {total} interior waypoints carry exactly zero noise"


def check_peak_distribution(peaks: list[float], per_session: list[float]) -> None:
    """Where the speed peaks: the whole distribution, not just its centre.

    A hand accelerates and then decelerates; it does not start at full speed. A
    profile whose maximum is the first step implies infinite initial
    acceleration, which is what an ease-out easing does. But a generator that
    peaks at exactly 0.44 every single time is a constant with extra steps, so
    the spread is asserted too - within a session and between sessions.
    """
    assert len(peaks) > 200, f"sample too small: {len(peaks)}"
    median = _pct(peaks, 0.5)
    assert 0.25 < median < 0.65, f"median peak-speed position {median:.3f}"
    early = sum(1 for p in peaks if p < 0.10) / len(peaks)
    assert early < 0.05, f"{early:.1%} of movements peak in the first 10%"
    late = sum(1 for p in peaks if p > 0.90) / len(peaks)
    assert late < 0.05, f"{late:.1%} of movements peak in the last 10%"
    assert _sd(peaks) > 0.03, f"peak position sd {_sd(peaks):.4f} - effectively fixed"
    assert _pct(peaks, 0.95) - _pct(peaks, 0.05) > 0.10, "the peak position has no spread"
    assert _sd(per_session) > 0.015, (
        f"per-session mean peak sd {_sd(per_session):.4f} - the profile is not per-seed"
    )


def check_detour_is_a_few_percent(buckets: dict[str, list[float]]) -> None:
    """Nobody travels several times the distance in order to move a little.

    Padding measured in fixed pixels does exactly that on short movements; a bow
    measured mostly as a fraction of the travel does not. The small absolute
    term that keeps short strokes from collapsing onto the straight line, and
    the tremor, are the two things that could bring this back, so both are
    fenced here.
    """
    for name, vals in buckets.items():
        assert vals, f"{name} bucket is empty - the check would pass vacuously"
        worst = max(vals)
        assert worst < 1.45, f"{name}: detour {worst:.2f}x the straight line"
        assert 1.0 <= _mean(vals) < 1.10, f"{name} bucket mean detour {_mean(vals):.3f}x"


def check_duration_grows_with_distance(means: list[float]) -> None:
    """Fitts, not a constant second.

    ``means`` is the mean duration at increasing distances. A constant makes
    implied speed scale with distance, which is backwards.
    """
    assert all(a < b for a, b in zip(means, means[1:])), means
    assert means[-1] > 1.8 * means[0], means


# The two rates that must be neither exactly zero nor large. Numbers, and the
# reasoning for each end, in the module docstring of ``_motion``.
#
#   backtracking: the legacy generator measures a mean of 0.045 with a p95 of
#     0.22 - a pointer that spends a fifth of its events retreating. The
#     previous version of our own planner measured exactly 0.000000 on all
#     12000 paths of a 300-seed sweep, because monotone progress was imposed by
#     construction. A real reach overshoots and corrects every few movements.
#   duplicates: the legacy generator repeats a device pixel on 8% of its events
#     and ends EVERY movement with a run of at least three identical ones, which
#     is what its flat ease-out tail produces. Ours filtered every repeat, so it
#     measured exactly 0 on every path - the other exact invariant.
BACKTRACK_BAND = (0.005, 0.040)
BACKTRACK_P95_MAX = 0.16
DUPLICATE_BAND = (0.004, 0.050)
DUPLICATE_RUN_MAX = 6


def check_backtrack_rate(fractions: list[float], per_session: list[float]) -> None:
    """Share of consecutive events that move away from the target."""
    assert len(fractions) > 1_000, f"sample too small: {len(fractions)}"
    assert_in_band(_mean(fractions), *BACKTRACK_BAND, label="mean backtracking fraction")
    assert _pct(fractions, 0.95) <= BACKTRACK_P95_MAX, (
        f"p95 backtracking {_pct(fractions, 0.95):.4f} - the pointer dithers"
    )
    share = sum(1 for b in fractions if b > 0) / len(fractions)
    assert_in_band(share, 0.05, 0.75, label="share of paths that backtrack at all")
    # No session may be the one that never does it: "this install never
    # overshoots" is a per-install invariant, which is the thing being fixed.
    assert min(per_session) > 0.0, "some session never backtracks on any movement"
    assert _sd(per_session) > 0.002, "the backtracking rate is not a per-session property"


def check_duplicate_rate(
    fractions: list[float], runs: list[int], per_session: list[float]
) -> None:
    """Share of consecutive events that report the same device pixel."""
    assert len(fractions) > 1_000, f"sample too small: {len(fractions)}"
    assert_in_band(_mean(fractions), *DUPLICATE_BAND, label="mean duplicate fraction")
    share = sum(1 for d in fractions if d > 0) / len(fractions)
    assert_in_band(share, 0.10, 0.85, label="share of paths with any duplicate")
    assert max(runs) <= DUPLICATE_RUN_MAX, f"a run of {max(runs)} events on one device pixel"
    # The legacy tail signature is a LONG run on EVERY movement.
    long_runs = sum(1 for r in runs if r >= 3) / len(runs)
    assert long_runs < 0.20, f"{long_runs:.1%} of movements stall on one pixel for 3+ events"
    assert min(per_session) > 0.0, "some session never repeats a pixel on any movement"
    assert _sd(per_session) > 0.001, "the duplicate rate is not a per-session property"


# Floor on (distance between two sessions) / (distance between two movements of
# one session), measured on the geometry alone. Before the arm-geometry model
# this sat at 1.06 at 50 px and 1.05 at 200 and 600 px: to a detector looking at
# shape, two installs were the same install. The floor is set well under what is
# measured so the test is about the mechanism, not about freezing a number.
DIVERGENCE_FLOOR = 1.35


def check_shape_diverges(ratio: float, dist: float) -> None:
    assert ratio > DIVERGENCE_FLOOR, (
        f"at {dist:.0f} px two seeds diverge only {ratio:.3f}x more than two "
        f"movements of one seed"
    )


# ════════════════════════════════════════════════════════════════════
# measurement helpers
# ════════════════════════════════════════════════════════════════════


def _endpoints(rng: random.Random, w: int = 1280, h: int = 720):
    return (
        rng.uniform(0, w),
        rng.uniform(0, h),
        rng.uniform(0, w),
        rng.uniform(0, h),
    )


def _radial(rng: random.Random, dist: float, w: int = 1280, h: int = 720):
    """A movement of a chosen length in a random direction."""
    x0, y0 = rng.uniform(0, w), rng.uniform(0, h)
    a = rng.uniform(0, 2 * math.pi)
    return x0, y0, x0 + math.cos(a) * dist, y0 + math.sin(a) * dist


def _axis(x0, y0, x1, y1):
    d = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / d, (y1 - y0) / d
    return ux, uy, -uy, ux


def _cumulative_length(path: list[Waypoint]) -> list[float]:
    cum = [0.0]
    for i in range(1, len(path)):
        cum.append(cum[-1] + math.hypot(path[i].x - path[i - 1].x, path[i].y - path[i - 1].y))
    return cum


def _arc_profile(path: list[Waypoint], fracs: list[float]) -> list[float]:
    """Fraction of the total travel completed at each fraction of the duration.

    This is the velocity profile in the only form a detector can see it: the
    cumulative distance curve resampled on a common time axis.
    """
    total_t = path[-1].t_ms
    cum = _cumulative_length(path)
    total_s = cum[-1]
    if total_t <= 0 or total_s <= 0:
        return [0.0 for _ in fracs]
    out = []
    for f in fracs:
        target = f * total_t
        j = 1
        while j < len(path) - 1 and path[j].t_ms < target:
            j += 1
        t0, t1 = path[j - 1].t_ms, path[j].t_ms
        w = 0.0 if t1 <= t0 else (target - t0) / (t1 - t0)
        out.append((cum[j - 1] + (cum[j] - cum[j - 1]) * w) / total_s)
    return out


def _peak_speed_fraction(path: list[Waypoint], n: int = 24) -> float:
    """Where in the movement the cursor is fastest, as a fraction of duration.

    Read off the arc-length profile resampled onto a uniform time grid rather
    than off raw consecutive events. A detector aggregating a movement does the
    same, and it is the honest thing to measure: the per-event ratio ``ds / dt``
    is dominated by the sampling jitter in ``dt``, so an argmax over raw events
    mostly locates the shortest interval and not the fastest instant.
    """
    profile = _arc_profile(path, [i / n for i in range(n + 1)])
    best, at = -1.0, 0.5
    for i in range(1, len(profile)):
        v = profile[i] - profile[i - 1]
        if v > best:
            best, at = v, (i - 0.5) / n
    return at


def _path_length(path: list[Waypoint]) -> float:
    return _cumulative_length(path)[-1]


def _max_bow_fraction(path: list[Waypoint]) -> float:
    """Largest perpendicular excursion off the straight line, over the distance."""
    x0, y0 = path[0].x, path[0].y
    x1, y1 = path[-1].x, path[-1].y
    d = math.hypot(x1 - x0, y1 - y0)
    if d <= 0:
        return 0.0
    nx, ny = -(y1 - y0) / d, (x1 - x0) / d
    return max(abs((w.x - x0) * nx + (w.y - y0) * ny) for w in path) / d


def backtrack_fraction(path, x0, y0, x1, y1) -> float:
    ux, uy, _, _ = _axis(x0, y0, x1, y1)
    proj = [(w.x - x0) * ux + (w.y - y0) * uy for w in path]
    if len(proj) < 2:
        return 0.0
    return sum(1 for a, b in zip(proj, proj[1:]) if b < a - 1e-9) / (len(proj) - 1)


def duplicate_fraction(keys) -> float:
    keys = list(keys)
    if len(keys) < 2:
        return 0.0
    return sum(1 for a, b in zip(keys, keys[1:]) if a == b) / (len(keys) - 1)


def longest_duplicate_run(keys) -> int:
    best = run = 1
    keys = list(keys)
    for a, b in zip(keys, keys[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best


def _tremor_residuals(seed, style, index, pts):
    """The tremor injected into one movement, as (along, across, dx, dy) px.

    Generated twice from the same PRNG stream, once with the tremor on and once
    off; the tremor is the last thing drawn, so the control polygon, the
    schedule and the overshoot are identical and the difference is exactly the
    injected noise.
    """
    x0, y0, x1, y1 = pts
    ux, uy, nx, ny = _axis(x0, y0, x1, y1)
    seeded = _mix(seed, "motion:move:%d" % index)
    a = _plan(random.Random(seeded), x0, y0, x1, y1, style, with_jitter=True)
    b = _plan(random.Random(seeded), x0, y0, x1, y1, style, with_jitter=False)
    # The duplicate filter can drop different points on the two runs; compare by
    # time stamp.
    by_t = {round(w.t_ms, 9): w for w in b}
    out = []
    for w in a:
        c = by_t.get(round(w.t_ms, 9))
        if c is None:
            continue
        dx, dy = w.x - c.x, w.y - c.y
        out.append((dx * ux + dy * uy, dx * nx + dy * ny, dx, dy))
    return out


def _unfiltered(style: MotionStyle) -> MotionStyle:
    """The same session with the duplicate filter switched off.

    Used when the *injected* noise is what is under test rather than the emitted
    stream. The filter drops a waypoint precisely when it landed back on the
    pixel already reported, which correlates with the sign of the along-travel
    component - so measuring on the emitted stream would measure the filter's
    selection on top of the noise itself.
    """
    return dataclasses.replace(style, dup_keep_p=1.0, dup_run_max=10**6)


def _neville(nodes: list[float], vals: list[float], at: float) -> float:
    """Value at ``at`` of the polynomial interpolating (nodes, vals)."""
    c = list(vals)
    n = len(nodes)
    for k in range(1, n):
        for i in range(n - k):
            c[i] = ((at - nodes[i + k]) * c[i] + (nodes[i] - at) * c[i + 1]) / (
                nodes[i] - nodes[i + k]
            )
    return c[0]


def centred_prediction_errors(points, axis: int) -> list[float]:
    """|predicted - observed| for every point predictable from 4 neighbours.

    ``points`` is a sequence of ``(x, y, parameter)``. For each interior point a
    cubic is interpolated through the two samples on either side - "a low-degree
    curve fitted to a handful of waypoints" - and used to predict the one in the
    middle. A detector needs nothing but the observed event stream to run it.
    """
    out = []
    for i in range(2, len(points) - 2):
        idx = (i - 2, i - 1, i + 1, i + 2)
        nodes = [points[j][2] for j in idx]
        if len(set(nodes)) < 4:
            continue
        out.append(
            abs(_neville(nodes, [points[j][axis] for j in idx], points[i][2]) - points[i][axis])
        )
    return out


_DIVERGENCE_FRACS = [i / 20.0 for i in range(1, 20)]


def _resample_by_arclength(path: list[Waypoint], fracs: list[float]):
    """Positions at fixed fractions of the travelled length.

    Divergence is measured on this rather than on a time axis on purpose. Fine
    timing does not survive dispatch - the scheduler quantises short sleeps - so
    a difference that lives only in the schedule is a difference a detector
    never sees. What it does see is the shape.
    """
    cum = _cumulative_length(path)
    total = cum[-1]
    if total <= 0:
        return [(path[0].x, path[0].y)] * len(fracs)
    out = []
    for f in fracs:
        target = f * total
        j = 1
        while j < len(path) - 1 and cum[j] < target:
            j += 1
        s0, s1 = cum[j - 1], cum[j]
        w = 0.0 if s1 <= s0 else (target - s0) / (s1 - s0)
        out.append(
            (
                path[j - 1].x + (path[j].x - path[j - 1].x) * w,
                path[j - 1].y + (path[j].y - path[j - 1].y) * w,
            )
        )
    return out


def divergence_ratio(style_for, dist: float, n_seeds: int = 16, n_moves: int = 10) -> float:
    """How much more two SESSIONS differ than two movements of one session.

    ``style_for`` maps a seed to the style it should use, which is how the
    known-bad is built: hand every seed the same style and the ratio collapses
    to 1, because then nothing about the shape depends on the session.
    """
    x0, y0 = 300.0, 300.0
    x1, y1 = 300.0 + dist * 0.8, 300.0 + dist * 0.6
    shapes = {}
    for s in range(n_seeds):
        m = CursorMotion(s, style=style_for(s))
        shapes[s] = [
            _resample_by_arclength(m.path(x0, y0, x1, y1, index=i), _DIVERGENCE_FRACS)
            for i in range(n_moves)
        ]

    def between(a, b):
        return _mean(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(a, b))

    within_pairs = [
        between(shapes[s][i], shapes[s][j])
        for s in shapes
        for i in range(n_moves)
        for j in range(i + 1, n_moves)
    ]
    cross_pairs = [
        between(shapes[a][i], shapes[b][i])
        for a in range(n_seeds)
        for b in range(a + 1, n_seeds)
        for i in range(n_moves)
    ]
    return _mean(cross_pairs) / _mean(within_pairs)


# ════════════════════════════════════════════════════════════════════
# the known-bad reference: the legacy in-engine generator
# ════════════════════════════════════════════════════════════════════


def _js_round(x: float) -> int:
    """JS Math.round: halves go toward +Infinity (Python's round() is banker's)."""
    return int(math.floor(x + 0.5))


def _legacy_bezier(ctrl, t):
    n = len(ctrl) - 1
    x = y = 0.0
    for i, (px, py) in enumerate(ctrl):
        c = math.comb(n, i) * (t**i) * ((1 - t) ** (n - i))
        x += px * c
        y += py * c
    return x, y


def legacy_curve(rng: random.Random, x0, y0, x1, y1):
    """Faithful transcription of the legacy in-engine trajectory generator.

    Returns ``(base, jittered)``: the clean Bezier samples and the same samples
    after the legacy jitter pass, so the noise can be differenced out exactly.

    Every literal below is a literal in the original: an 80 px knot box, exactly
    two knots, a curve resolution of Chebyshev(dx, dy), a 0.5 jitter probability
    and - the bug this file exists to catch - a Gaussian with mu = 1, applied to
    y and to nothing else.
    """
    left, right = min(x0, x1) - 80, max(x0, x1) + 80
    down, up = min(y0, y1) - 80, max(y0, y1) + 80
    knots = [
        (left + rng.random() * (right - left), down + rng.random() * (up - down))
        for _ in range(2)
    ]
    ctrl = [(x0, y0), *knots, (x1, y1)]
    n_pts = max(int(abs(x0 - x1)), int(abs(y0 - y1)), 2)
    base = [_legacy_bezier(ctrl, i / (n_pts - 1)) for i in range(n_pts)]
    jit = list(base)
    for i in range(1, len(jit) - 1):
        if rng.random() < 0.5:
            jit[i] = (jit[i][0], jit[i][1] + _js_round(rng.gauss(1.0, 1.0)))
    return base, jit


def legacy_step_indices(n_curve: int, total_len: float, max_time_s: float = 1.5):
    """The legacy time mapping: one fixed ease-out quadratic, no random input."""
    max_steps = max(4, int(max_time_s * 100))
    target = min(max_steps, max(4, int(total_len**0.25 * 20)))
    out = []
    for i in range(target):
        t = i / (target - 1)
        e = -t * (t - 2)  # ease-out quad, the same one for every movement ever
        out.append(min(n_curve - 1, int(e * (n_curve - 1))))
    return out


# Measured wall time of one legacy movement, of any length: 1.003 s. The legacy
# generator spends its budget on a fixed number of evenly-spaced sleeps, so the
# implied duration does not depend on how far the pointer went.
LEGACY_TOTAL_MS = 1003.0


def legacy_path(rng: random.Random, x0, y0, x1, y1, max_time_s: float = 1.5) -> list[Waypoint]:
    """The legacy generator's output in the same shape ours produces.

    Exists so the known-bad arms can feed the very same ``check_*`` functions
    the forward tests use, instead of re-deriving each property from the legacy
    data with a second copy of the assertion.
    """
    base, jit = legacy_curve(rng, x0, y0, x1, y1)
    total_len = sum(
        math.hypot(base[i][0] - base[i - 1][0], base[i][1] - base[i - 1][1])
        for i in range(1, len(base))
    )
    idx = legacy_step_indices(len(jit), total_len, max_time_s)
    dt = LEGACY_TOTAL_MS / max(len(idx) - 1, 1)
    return [
        Waypoint(jit[j][0], jit[j][1], 0.0 if i == 0 else dt, i * dt)
        for i, j in enumerate(idx)
    ]


# ════════════════════════════════════════════════════════════════════
# 1. per-seed, not constant
# ════════════════════════════════════════════════════════════════════


def test_same_seed_is_byte_identical():
    """Seed reproducibility is a documented property of this package."""
    a = CursorMotion(20260726)
    b = CursorMotion(20260726)
    for _ in range(25):
        pa = a.path(11.0, 23.0, 940.0, 512.0)
        pb = b.path(11.0, 23.0, 940.0, 512.0)
        assert pa == pb


def test_movement_streams_are_independent():
    """Movement n is the same path whether or not movements 0..n-1 happened."""
    m = CursorMotion(7)
    seq = [m.path(0.0, 0.0, 500.0, 400.0) for _ in range(5)]
    m2 = CursorMotion(7)
    assert m2.path(0.0, 0.0, 500.0, 400.0, index=3) == seq[3]
    assert m2.count == 0  # explicit index does not advance the counter


def test_different_seeds_draw_different_styles():
    styles = [style_for_seed(s) for s in range(200)]
    assert len({tuple(sorted(vars(s).items())) for s in styles}) == 200
    # No field may be degenerate: every one of them must actually vary.
    for field in MotionStyle.__dataclass_fields__:
        vals = {getattr(s, field) for s in styles}
        assert len(vals) > 1, f"{field} is constant across seeds"


def test_shape_family_differs_between_seeds():
    """Two installs must not share a shape.

    Each seed is summarised by five features a detector could estimate from an
    event stream: duration, event count, how far the path bows off the straight
    line, where the speed peaks, and how much sideways noise there is. Across
    seeds those features must have real spread - the whole point of drawing them
    from the seed.
    """
    fixed = [(120.0, 140.0, 900.0, 520.0), (900.0, 520.0, 300.0, 180.0)]
    feats: dict[str, list[float]] = {
        "duration": [],
        "events": [],
        "bow": [],
        "peak": [],
        "wiggle": [],
    }
    for seed in range(30):
        m = CursorMotion(seed)
        dur, ev, bow, peak, wig = [], [], [], [], []
        for i in range(30):
            x0, y0, x1, y1 = fixed[i % 2]
            p = m.path(x0, y0, x1, y1)
            dur.append(total_ms(p))
            ev.append(len(p))
            bow.append(_max_bow_fraction(p))
            peak.append(_peak_speed_fraction(p))
            wig.append(_sd([r[1] for r in _tremor_residuals(seed, m.style, i, (x0, y0, x1, y1))]))
        for k, v in zip(feats, (dur, ev, bow, peak, wig)):
            feats[k].append(_mean(v))

    def spread(v):
        return _sd(v) / abs(_mean(v))

    # Relative sd across seeds. These are lower bounds chosen well under the
    # observed values so the test is about "the family moves", not about
    # freezing today's exact numbers.
    assert spread(feats["duration"]) > 0.10
    assert spread(feats["events"]) > 0.15
    assert spread(feats["bow"]) > 0.25
    assert spread(feats["peak"]) > 0.03
    assert spread(feats["wiggle"]) > 0.20


# ════════════════════════════════════════════════════════════════════
# 2. the tremor: zero-mean, two-dimensional, and on every waypoint
# ════════════════════════════════════════════════════════════════════

# Tolerance: 0.01 px. At the sample size below the standard error of the mean is
# under 0.002 px, so 0.01 px is a comfortable ceiling and still 50x tighter than
# the legacy generator's systematic +0.50 px.
ZERO_MEAN_TOL_PX = 0.01


def _tremor_corpus(unfiltered: bool):
    rng = random.Random(4242)
    along: list[float] = []
    across: list[float] = []
    for seed in range(12):
        style = style_for_seed(seed)
        if unfiltered:
            style = _unfiltered(style)
        for i in range(220):
            pts = _endpoints(rng)
            if math.hypot(pts[2] - pts[0], pts[3] - pts[1]) < 30:
                continue
            for a, c, _dx, _dy in _tremor_residuals(seed, style, i, pts):
                along.append(a)
                across.append(c)
    return along, across


def test_tremor_is_zero_mean_on_both_components():
    """Neither component may carry a systematic drift.

    Measured with the duplicate filter off, so this is the mean of the noise the
    generator injects and not the mean of what survives a downstream filter -
    see ``_unfiltered``.
    """
    along, across = _tremor_corpus(unfiltered=True)
    assert len(across) > 100_000, f"sample too small: {len(across)}"
    assert_zero_mean(across, ZERO_MEAN_TOL_PX, "across-travel tremor")
    assert_zero_mean(along, ZERO_MEAN_TOL_PX, "along-travel tremor")


def test_the_emitted_stream_is_also_unbiased():
    """And the same must hold for what actually goes on the wire."""
    along, across = _tremor_corpus(unfiltered=False)
    for label, vals in (("across", across), ("along", along)):
        m, _ = _mean_se(vals)
        assert abs(m) < ZERO_MEAN_TOL_PX, f"emitted {label} mean {m:+.5f} px"


def test_zero_mean_check_rejects_the_legacy_generator():
    """The known-bad input. The legacy jitter uses mu = 1, not 0.

    Half the intermediate points get round(gauss(1, 1)) added to y, so the mean
    residual is about +0.5 px on every path it has ever produced. If this test
    ever stops failing, the check above has stopped checking anything.
    """
    rng = random.Random(20260726)
    resid: list[float] = []
    for _ in range(400):
        x0, y0, x1, y1 = _endpoints(rng)
        base, jit = legacy_curve(rng, x0, y0, x1, y1)
        resid.extend(j[1] - b[1] for j, b in zip(jit, base))
    assert len(resid) > 100_000
    assert _mean(resid) > 0.4, f"legacy y-bias should be ~+0.5 px, measured {_mean(resid):+.4f}"
    with pytest.raises(AssertionError):
        assert_zero_mean(resid, ZERO_MEAN_TOL_PX, "legacy jitter")


def _axis_noise(pts, seed: int = 3, n: int = 120):
    style = _unfiltered(style_for_seed(seed))
    dx, dy = [], []
    for i in range(n):
        for _a, _c, ddx, ddy in _tremor_residuals(seed, style, i, pts):
            dx.append(ddx)
            dy.append(ddy)
    return dx, dy


@pytest.mark.parametrize(
    "label,pts",
    [
        ("horizontal", (100.0, 400.0, 800.0, 400.0)),
        ("vertical", (400.0, 100.0, 400.0, 600.0)),
        ("diagonal", (100.0, 120.0, 800.0, 460.0)),
    ],
)
def test_jitter_reaches_both_screen_axes(label, pts):
    dx, dy = _axis_noise(pts)
    check_both_axes_carry_noise(dx, dy)


def test_both_axes_check_rejects_the_legacy_generator():
    """Known-bad for ``check_both_axes_carry_noise``.

    The legacy jitter only ever touches y, so its x residual is not merely
    small, it is the single value 0.0 - every x it emits lies on an analytic
    cubic. The check has to reject that, and it is the same function the
    forward test runs, so it cannot reject it there and pass here.
    """
    rng = random.Random(99)
    dx: list[float] = []
    dy: list[float] = []
    for _ in range(60):
        x0, y0, x1, y1 = _endpoints(rng)
        base, jit = legacy_curve(rng, x0, y0, x1, y1)
        dx.extend(j[0] - b[0] for j, b in zip(jit, base))
        dy.extend(j[1] - b[1] for j, b in zip(jit, base))
    assert {round(v, 12) for v in dx} == {0.0}
    with pytest.raises(AssertionError):
        check_both_axes_carry_noise(dx, dy)


def _tremor_movements(n_seeds: int = 20, per_seed: int = 30):
    rng = random.Random(1001)
    out = []
    for seed in range(n_seeds):
        style = style_for_seed(seed)
        for i in range(per_seed):
            pts = _radial(rng, rng.uniform(60, 900))
            out.append([(r[0], r[1]) for r in _tremor_residuals(seed, style, i, pts)])
    return out


def test_the_tremor_is_not_rank_one():
    check_tremor_is_two_dimensional(_tremor_movements())


def test_the_rank_check_rejects_a_tremor_on_a_single_direction():
    """Known-bad: the tremor as it was - drawn once and applied across travel.

    Every displacement in a movement is then a multiple of one vector, so
    sd(along) is 0 and the spread ratio is floating-point dust. Measured on the
    previous implementation: 9.53e-14 over 318 movements.
    """
    rng = random.Random(1001)
    movements = []
    for _ in range(80):
        n = rng.randint(20, 60)
        movements.append([(0.0, rng.gauss(0.0, 0.4)) for _ in range(n)])
    with pytest.raises(AssertionError):
        check_tremor_is_two_dimensional(movements)


def test_the_rank_check_rejects_the_legacy_generator():
    """Known-bad: the legacy tremor is applied to y and to nothing else.

    In travel coordinates that gives spread on both components - but they are
    the same number twice, scaled by the direction of travel, so the cloud is
    still a line and the correlation is 1 to floating point.
    """
    rng = random.Random(515)
    movements = []
    for _ in range(120):
        x0, y0, x1, y1 = _endpoints(rng)
        if math.hypot(x1 - x0, y1 - y0) < 60:
            continue
        _ux, _uy, nx, ny = _axis(x0, y0, x1, y1)
        ux, uy = _ux, _uy
        base, jit = legacy_curve(rng, x0, y0, x1, y1)
        movements.append(
            [
                ((j[0] - b[0]) * ux + (j[1] - b[1]) * uy, (j[0] - b[0]) * nx + (j[1] - b[1]) * ny)
                for b, j in zip(base, jit)
            ]
        )
    with pytest.raises(AssertionError):
        check_tremor_is_two_dimensional(movements)


@pytest.mark.parametrize(
    "label,pts",
    [
        ("horizontal", (100.0, 400.0, 800.0, 400.0)),
        ("vertical", (400.0, 100.0, 400.0, 600.0)),
        ("diagonal", (100.0, 120.0, 800.0, 460.0)),
    ],
)
def test_waypoints_are_not_predictable_from_their_neighbours(label, pts):
    style = style_for_seed(3)
    errors: dict[str, list[float]] = {"x": [], "y": []}
    for i in range(60):
        path = CursorMotion(3, style=style).path(*pts, index=i)
        points = [(w.x, w.y, w.t_ms) for w in path]
        for axis, name in ((0, "x"), (1, "y")):
            errors[name].extend(centred_prediction_errors(points, axis))
    check_not_predictable(errors)


def test_the_prediction_check_rejects_the_legacy_generator():
    """Known-bad: every legacy x is predicted to floating-point dust.

    x there is a cubic polynomial sampled at a uniform parameter, and the cubic
    through any four of those samples IS that polynomial - so four events give
    the control polygon and every remaining event for free.
    """
    rng = random.Random(77)
    errors: dict[str, list[float]] = {"x": [], "y": []}
    for _ in range(30):
        x0, y0, x1, y1 = _endpoints(rng)
        if abs(x1 - x0) < 200:
            continue
        base, jit = legacy_curve(rng, x0, y0, x1, y1)
        points = [(p[0], q[1], float(i)) for i, (p, q) in enumerate(zip(base, jit))]
        errors["x"].extend(centred_prediction_errors(points, 0))
        errors["y"].extend(centred_prediction_errors(points, 1))
    assert _pct(errors["x"], 0.5) < 1e-6, (
        f"legacy median x prediction error {_pct(errors['x'], 0.5):.3e} px"
    )
    with pytest.raises(AssertionError):
        check_not_predictable(errors)


def _clean_waypoint_counts(jitter_probability: float | None = None):
    """(clean, total) interior waypoints carrying exactly zero displacement.

    ``jitter_probability`` builds the known-bad: it simulates a tremor pass that
    only displaces a random subset, which is what the generator used to do.
    """
    rng = random.Random(808)
    clean = total = 0
    for seed in range(6):
        style = _unfiltered(style_for_seed(seed))
        for i in range(30):
            pts = _radial(rng, rng.uniform(50, 900))
            for _a, _c, dx, dy in _tremor_residuals(seed, style, i, pts)[1:-1]:
                total += 1
                if jitter_probability is not None and rng.random() >= jitter_probability:
                    clean += 1
                elif jitter_probability is None and dx == 0.0 and dy == 0.0:
                    clean += 1
    return clean, total


def test_no_interior_waypoint_sits_exactly_on_the_underlying_curve():
    check_every_waypoint_is_displaced(*_clean_waypoint_counts())


def test_the_displacement_coverage_check_rejects_a_random_subset():
    """Known-bad: displace only some waypoints, as the previous pass did.

    At the probability the generator used, 45% of every emitted path lay exactly
    on the analytic curve.
    """
    clean, total = _clean_waypoint_counts(jitter_probability=0.55)
    assert clean > 0.3 * total
    with pytest.raises(AssertionError):
        check_every_waypoint_is_displaced(clean, total)


# ════════════════════════════════════════════════════════════════════
# 3. a velocity profile that varies, and peaks in the middle
# ════════════════════════════════════════════════════════════════════

_PROFILE_FRACS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def test_velocity_profile_varies_between_movements_of_equal_length():
    """Two identical-length movements must not share a timing profile."""
    m = CursorMotion(5150)
    profs = [_arc_profile(m.path(200.0, 200.0, 700.0, 500.0), _PROFILE_FRACS) for _ in range(400)]
    # No two movements produce the same profile.
    assert len({tuple(round(v, 6) for v in p) for p in profs}) == len(profs)
    # And the spread is real, not floating-point dust. The legacy generator's
    # timing map has NO random input at all (see the known-bad test below); its
    # only variation is floor() quantisation, sd <= 0.0057.
    sds = [_sd([p[k] for p in profs]) for k in range(len(_PROFILE_FRACS))]
    # The first and last decile are pinned near 0 and 1 for any profile, so the
    # meaningful comparison is the interior.
    assert min(sds[1:-1]) > 0.008, f"profile barely varies: sds={['%.4f' % s for s in sds]}"
    assert max(sds) > 0.030, f"profile barely varies: sds={['%.4f' % s for s in sds]}"


def test_legacy_velocity_profile_is_effectively_frozen():
    """Known-bad: the legacy time mapping takes no random input whatsoever."""
    profs = []
    for n_curve in range(400, 460):  # the only thing that can change it
        idx = legacy_step_indices(n_curve, 900.0)
        profs.append(tuple(round(i / (n_curve - 1), 4) for i in idx))
    sds = [_sd([p[k] for p in profs]) for k in range(1, len(profs[0]) - 1)]
    assert max(sds) < 0.01, "legacy profile was supposed to be frozen"


def test_the_speed_peak_lands_in_the_middle_and_is_a_distribution():
    rng = random.Random(31337)
    peaks: list[float] = []
    per_session: list[float] = []
    for seed in range(16):
        m = CursorMotion(seed)
        local = [_peak_speed_fraction(m.path(*_radial(rng, rng.uniform(200, 900)))) for _ in range(45)]
        peaks.extend(local)
        per_session.append(_mean(local))
    check_peak_distribution(peaks, per_session)


def test_peak_position_check_rejects_the_legacy_generator():
    """Known-bad for ``check_peak_distribution``.

    The legacy ease-out quadratic has its maximum at t = 0 - infinite initial
    acceleration - so almost every movement is fastest in its first tenth.
    """
    rng = random.Random(31337)
    peaks: list[float] = []
    per_session: list[float] = []
    for _ in range(8):
        local = []
        for _ in range(50):
            x0, y0, x1, y1 = _endpoints(rng)
            if math.hypot(x1 - x0, y1 - y0) < 200:
                continue
            local.append(_peak_speed_fraction(legacy_path(rng, x0, y0, x1, y1)))
        peaks.extend(local)
        per_session.append(_mean(local))
    assert len(peaks) > 200
    with pytest.raises(AssertionError):
        check_peak_distribution(peaks, per_session)


def test_peak_position_check_rejects_a_frozen_peak():
    """Known-bad, the other way: a peak that is 0.44 every single time.

    Landing in the middle is necessary and not sufficient. An easing with no
    random input passes the median test and is still one constant.
    """
    peaks = [0.4375] * 400
    with pytest.raises(AssertionError):
        check_peak_distribution(peaks, [0.4375] * 16)


def test_profile_endpoints_are_exact():
    m = CursorMotion(11)
    for _ in range(50):
        p = m.path(50.0, 60.0, 640.0, 400.0)
        prof = _arc_profile(p, [0.0, 1.0])
        assert prof[0] == pytest.approx(0.0, abs=1e-9)
        assert prof[1] == pytest.approx(1.0, abs=1e-9)


# ════════════════════════════════════════════════════════════════════
# 4. the two rates that must not be exactly zero
# ════════════════════════════════════════════════════════════════════


def _rate_corpus(make_path, n_sessions: int = 24, per_session: int = 100):
    """(backtrack fractions, duplicate fractions, runs, per-session means).

    ``make_path`` takes ``(session, rng, x0, y0, x1, y1)`` so both our generator
    and the legacy one can be fed through the identical measurement.
    """
    rng = random.Random(6)
    back: list[float] = []
    dup: list[float] = []
    runs: list[int] = []
    by_session_back: list[float] = []
    by_session_dup: list[float] = []
    for session in range(n_sessions):
        lb, ld = [], []
        for _ in range(per_session):
            x0, y0, x1, y1 = _radial(rng, rng.uniform(20, 1000))
            p = make_path(session, rng, x0, y0, x1, y1)
            if len(p) < 3:
                continue
            keys = [(round(w.x), round(w.y)) for w in p]
            lb.append(backtrack_fraction(p, x0, y0, x1, y1))
            ld.append(duplicate_fraction(keys))
            runs.append(longest_duplicate_run(keys))
        back.extend(lb)
        dup.extend(ld)
        by_session_back.append(_mean(lb))
        by_session_dup.append(_mean(ld))
    return back, dup, runs, by_session_back, by_session_dup


_MOTIONS = {}


def _ours(session, _rng, x0, y0, x1, y1):
    m = _MOTIONS.setdefault(session, CursorMotion(session))
    return m.path(x0, y0, x1, y1)


def _legacy(_session, rng, x0, y0, x1, y1):
    return legacy_path(rng, x0, y0, x1, y1)


def _straight(_session, _rng, x0, y0, x1, y1):
    """A strictly monotone path whose every event is a fresh device pixel.

    This is what the planner used to emit: 12000 paths, backtracking fraction
    0.000000 on every one, duplicate fraction exactly 0 on every one. Points are
    spaced ~3 px apart so the reference really does report a fresh device pixel
    every time - otherwise the fixture would fail for its own arithmetic rather
    than demonstrating the property.
    """
    n = max(5, min(40, int(math.hypot(x1 - x0, y1 - y0) / 3.0)))
    return [
        Waypoint(
            x0 + (x1 - x0) * i / (n - 1),
            y0 + (y1 - y0) * i / (n - 1),
            10.0,
            10.0 * i,
        )
        for i in range(n)
    ]


def test_movements_overshoot_and_correct_sometimes_but_not_often():
    _MOTIONS.clear()
    back, _dup, _runs, by_back, _by_dup = _rate_corpus(_ours)
    check_backtrack_rate(back, by_back)


def test_consecutive_events_may_share_a_device_pixel_but_never_for_long():
    _MOTIONS.clear()
    _back, dup, runs, _by_back, by_dup = _rate_corpus(_ours)
    check_duplicate_rate(dup, runs, by_dup)


def test_the_rate_bands_reject_a_generator_that_never_does_either():
    """Known-bad, low end: monotone progress and an all-distinct pixel stream."""
    back, dup, runs, by_back, by_dup = _rate_corpus(_straight, n_sessions=6, per_session=60)
    assert max(back) == 0.0 and max(dup) == 0.0 and max(runs) == 1
    with pytest.raises(AssertionError):
        check_backtrack_rate(back, by_back)
    with pytest.raises(AssertionError):
        check_duplicate_rate(dup, runs, by_dup)


def test_the_rate_bands_reject_the_legacy_generator_at_the_high_end():
    """Known-bad, high end: the legacy generator dithers and then stalls."""
    back, dup, runs, by_back, by_dup = _rate_corpus(_legacy, n_sessions=6, per_session=60)
    assert _mean(back) > BACKTRACK_BAND[1], f"legacy mean backtracking {_mean(back):.4f}"
    assert _mean(dup) > DUPLICATE_BAND[1], f"legacy mean duplicate {_mean(dup):.4f}"
    assert min(runs) >= 3, "the legacy tail should stall on every single movement"
    with pytest.raises(AssertionError):
        check_backtrack_rate(back, by_back)
    with pytest.raises(AssertionError):
        check_duplicate_rate(dup, runs, by_dup)


# ════════════════════════════════════════════════════════════════════
# 5. the per-seed difference lives in the shape
# ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("dist", [50.0, 200.0, 600.0])
def test_two_sessions_trace_visibly_different_curves(dist):
    check_shape_diverges(divergence_ratio(lambda _s: None, dist), dist)


@pytest.mark.parametrize("dist", [50.0, 200.0, 600.0])
def test_the_divergence_check_rejects_a_seed_independent_shape(dist):
    """Known-bad: hand every seed the same style.

    Then the only thing differing between sessions is the per-movement PRNG
    stream - all the variation between movements, none between installs, which
    is the state the generator was in. The ratio must collapse to 1.
    """
    shared = style_for_seed(0)
    ratio = divergence_ratio(lambda _s: shared, dist)
    assert ratio < 1.15, f"shared style still diverges {ratio:.3f}x at {dist:.0f} px"
    with pytest.raises(AssertionError):
        check_shape_diverges(ratio, dist)


# ════════════════════════════════════════════════════════════════════
# 6. human-plausible output
# ════════════════════════════════════════════════════════════════════


def test_endpoints_are_exact():
    rng = random.Random(1)
    m = CursorMotion(123)
    for _ in range(300):
        x0, y0, x1, y1 = _endpoints(rng)
        p = m.path(x0, y0, x1, y1)
        assert (p[0].x, p[0].y) == (x0, y0)
        assert (p[-1].x, p[-1].y) == (x1, y1)


def test_the_path_stays_inside_a_corridor_around_the_travel():
    """The replacement for the old strict-monotonicity assertion.

    Progress may now go backwards - see ``check_backtrack_rate`` - but only
    inside a corrective submovement. No waypoint may sit meaningfully before the
    start or beyond the target, which is the property strict monotonicity was
    really there to give.
    """
    rng = random.Random(2)
    for seed in range(6):
        st = style_for_seed(seed)
        m = CursorMotion(seed, style=st)
        slack = st.overshoot_cap_px + 8.0 * st.tremor_across_px + 1.0
        for _ in range(80):
            x0, y0, x1, y1 = _radial(rng, rng.uniform(20, 1000))
            d = math.hypot(x1 - x0, y1 - y0)
            ux, uy, _, _ = _axis(x0, y0, x1, y1)
            for w in m.path(x0, y0, x1, y1):
                s = (w.x - x0) * ux + (w.y - y0) * uy
                assert -slack <= s <= d + slack, f"axial position {s:.2f} of {d:.2f}"


def test_waypoints_stay_inside_a_provable_box():
    """A Bezier lies in the convex hull of its control points.

    The knots are offset by at most ``bow_cap_px`` from the start->end segment,
    the corrective hump adds at most ``overshoot_cap_px``, and the tremor adds a
    Gaussian whose sd is at most ``tremor_across_px * (1 + tremor_burst_mult)``;
    8 sd is a generous but finite envelope. Nothing may leave that box.
    """
    rng = random.Random(3)
    for seed in range(10):
        st = style_for_seed(seed)
        m = CursorMotion(seed, style=st)
        slack = (
            st.bow_cap_px
            + st.overshoot_cap_px
            + 8.0 * st.tremor_across_px * (1.0 + st.tremor_burst_mult)
            + 1.0
        )
        for _ in range(60):
            x0, y0, x1, y1 = _endpoints(rng)
            p = m.path(x0, y0, x1, y1)
            lo_x, hi_x = min(x0, x1) - slack, max(x0, x1) + slack
            lo_y, hi_y = min(y0, y1) - slack, max(y0, y1) + slack
            for w in p:
                assert lo_x <= w.x <= hi_x
                assert lo_y <= w.y <= hi_y


def _detour_buckets(make_path) -> dict[str, list[float]]:
    """Path-length / straight-line ratio, bucketed by movement distance."""
    rng = random.Random(4)
    buckets: dict[str, list[float]] = {"short": [], "mid": [], "long": []}
    for _ in range(720):
        d = rng.choice([rng.uniform(8, 50), rng.uniform(50, 400), rng.uniform(400, 1200)])
        x0, y0, x1, y1 = _radial(rng, d)
        p = make_path(rng, x0, y0, x1, y1)
        if len(p) < 3:
            continue
        buckets["short" if d < 50 else ("mid" if d < 400 else "long")].append(
            _path_length(p) / d
        )
    return buckets


def test_detour_is_a_few_percent_not_a_few_times():
    """Nobody travels 3.9x the distance to move 40 px."""
    motions = [CursorMotion(seed) for seed in range(6)]
    state = {"i": 0}

    def ours(_rng, x0, y0, x1, y1):
        m = motions[state["i"] % len(motions)]
        state["i"] += 1
        return m.path(x0, y0, x1, y1)

    check_detour_is_a_few_percent(_detour_buckets(ours))


def test_detour_check_rejects_the_legacy_generator():
    """Known-bad for ``check_detour_is_a_few_percent``.

    The legacy knot box is padded by a fixed 80 px on every side, so a short
    movement is dominated by the padding: measured mean detour on sub-50 px
    moves is 4.3x, with individual paths over 11x.
    """
    with pytest.raises(AssertionError):
        check_detour_is_a_few_percent(_detour_buckets(legacy_path))


def test_step_count_and_duration_stay_inside_the_stated_envelope():
    rng = random.Random(5)
    for seed in range(8):
        m = CursorMotion(seed)
        for _ in range(100):
            x0, y0, x1, y1 = _endpoints(rng, 1920, 1080)
            p = m.path(x0, y0, x1, y1)
            assert 1 <= len(p) <= MAX_STEPS + 1
            if len(p) == 1:  # sub-pixel nudge
                assert total_ms(p) == 0.0
                continue
            assert MIN_DURATION_MS <= total_ms(p) <= MAX_DURATION_MS
            assert p[0].dt_ms == 0.0
            assert all(w.dt_ms > 0.0 for w in p[1:])
            assert all(w.t_ms >= 0.0 for w in p)


_DURATION_DISTANCES = (25.0, 100.0, 400.0, 1200.0)


def test_duration_grows_with_distance():
    """Fitts, not a constant second.

    The legacy generator takes ~1.0 s for a 3 px move and ~1.0 s for a 1338 px
    move, which makes implied speed scale with distance - backwards.
    """
    m = CursorMotion(77)
    means = [
        _mean([total_ms(m.path(100.0, 400.0, 100.0 + d, 400.0)) for _ in range(150)])
        for d in _DURATION_DISTANCES
    ]
    check_duration_grows_with_distance(means)


def test_duration_check_rejects_the_legacy_generator():
    """Known-bad for ``check_duration_grows_with_distance``.

    Measured on this transcription: 1003.0 ms at 25 px and 1003.0 ms at
    1200 px. Not "approximately constant" - identical.
    """
    rng = random.Random(77)
    means = [
        _mean([total_ms(legacy_path(rng, 100.0, 400.0, 100.0 + d, 400.0)) for _ in range(40)])
        for d in _DURATION_DISTANCES
    ]
    with pytest.raises(AssertionError):
        check_duration_grows_with_distance(means)


def test_implied_speed_stays_in_a_human_envelope():
    """Fast enough to be a person in a hurry, slow enough to be a person.

    Mean speed over a whole movement, in px/s. A hand peaks in the low
    thousands on a big sweep, so a mean in the low thousands is the ceiling and
    anything above it is a hand that does not exist.

    Deliberately has NO known-bad arm: the legacy generator passes this. It is a
    plausibility floor and ceiling, not a discriminator, and saying so is worth
    more than a test that pretends otherwise.
    """
    rng = random.Random(8)
    speeds: list[float] = []
    for seed in range(8):
        m = CursorMotion(seed)
        for _ in range(120):
            x0, y0, x1, y1 = _endpoints(rng)
            d = math.hypot(x1 - x0, y1 - y0)
            if d < 20:
                continue
            speeds.append(d / (total_ms(m.path(x0, y0, x1, y1)) / 1000.0))
    speeds.sort()
    assert speeds[-1] < 3500.0, f"top mean speed {speeds[-1]:.0f} px/s"
    assert 300.0 < speeds[len(speeds) // 2] < 1500.0, f"median {speeds[len(speeds) // 2]:.0f} px/s"


def test_degenerate_moves():
    m = CursorMotion(9)
    same = m.path(300.0, 300.0, 300.0, 300.0)
    assert same == [Waypoint(300.0, 300.0, 0.0, 0.0)]
    sub = m.path(300.0, 300.0, 300.2, 300.1)
    assert len(sub) == 1 and (sub[0].x, sub[0].y) == (300.2, 300.1)
    two = m.path(300.0, 300.0, 302.0, 301.0)
    assert len(two) >= 2
    assert (two[0].x, two[0].y) == (300.0, 300.0)
    assert (two[-1].x, two[-1].y) == (302.0, 301.0)


def test_negative_and_offscreen_coordinates_are_handled():
    m = CursorMotion(10)
    p = m.path(-40.0, 12.0, 2200.0, -300.0)
    assert (p[0].x, p[0].y) == (-40.0, 12.0)
    assert (p[-1].x, p[-1].y) == (2200.0, -300.0)
    assert MIN_DURATION_MS <= total_ms(p) <= MAX_DURATION_MS


# ────────────────────────────────────────────────────────────────────
# style ranges, derived from the source rather than restated
# ────────────────────────────────────────────────────────────────────
#
# An earlier version of this test copied every draw range out of
# ``style_for_seed`` and re-asserted it with ad-hoc padding (the module drew
# bow_frac from 0.008..0.045 and the test allowed 0.005..0.05). Written from
# the same head as the code, on the same day, it could not catch a range
# change: widen a range in the module and the padded copy still passes;
# narrow it and nothing notices either. It restated the numbers, it did not
# check them.
#
# What replaces it is two things that cannot be written from memory:
#
#   * the ranges are PARSED out of ``style_for_seed`` and each field is
#     checked against its own draw call. A range change in the module is a
#     range change in the test, automatically, with no second copy to update.
#     The draws must also FILL the parsed range, which is what catches a value
#     that is drawn wide and then silently clamped narrow somewhere else.
#   * the fields that are not a direct draw (they are computed from locals)
#     are checked as PROPERTIES - orderings, and the mathematical requirement
#     that the Beta speed density have both shape parameters above 1 so speed
#     is zero at both ends. No number from the module appears in them.
#
# Every dataclass field must fall into one of those two arms, so a new field
# cannot be added without a decision about how it is checked.


class _Unsupported(Exception):
    """style_for_seed grew an expression this derivation cannot evaluate."""


_MATH_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _iv(node, env):
    """The interval of values ``node`` can take. Raises on anything unknown.

    Deliberately total-or-loud: an expression this cannot evaluate is a
    failure, not a field that quietly drops out of the check. That is the
    whole failure mode being fixed here.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return (float(node.value), float(node.value))
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "math" and node.attr in _MATH_CONSTS):
        v = _MATH_CONSTS[node.attr]
        return (v, v)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise _Unsupported(f"unknown name {node.id!r}")
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        lo, hi = _iv(node.operand, env)
        return (-hi, -lo)
    if isinstance(node, ast.BinOp):
        a, b = _iv(node.left, env), _iv(node.right, env)
        if isinstance(node.op, ast.Add):
            return (a[0] + b[0], a[1] + b[1])
        if isinstance(node.op, ast.Sub):
            return (a[0] - b[1], a[1] - b[0])
        if isinstance(node.op, ast.Mult):
            prods = [x * y for x in a for y in b]
            return (min(prods), max(prods))
        raise _Unsupported(f"binary op {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in ("uniform", "randint"):
            return (_iv(node.args[0], env)[0], _iv(node.args[1], env)[1])
        if isinstance(fn, ast.Name) and fn.id in ("min", "max"):
            parts = [_iv(a, env) for a in node.args]
            pick = min if fn.id == "min" else max
            return (pick(p[0] for p in parts), pick(p[1] for p in parts))
        raise _Unsupported(f"call {ast.dump(fn)[:60]}")
    raise _Unsupported(ast.dump(node)[:80])


def _iv_or_tuple(node, env):
    if isinstance(node, ast.Tuple):
        return [_iv(e, env) for e in node.elts]
    return _iv(node, env)


def _scan_assignments(stmts, env, guarded=False):
    """Build the local environment, statement by statement.

    A reassignment inside an ``if`` is hulled with the value it may keep, so a
    guarded correction widens the interval rather than replacing it.
    """
    for st in stmts:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            name = st.targets[0].id
            try:
                value = _iv_or_tuple(st.value, env)
            except _Unsupported:
                # A local that is not a number - the Random instance itself,
                # say. Skipped rather than fatal, because a FIELD that depends
                # on it still fails loudly: the name will be missing from the
                # environment when the constructor call is evaluated.
                env.pop(name, None)
                continue
            prev = env.get(name)
            if guarded and isinstance(prev, tuple) and isinstance(value, tuple):
                value = (min(prev[0], value[0]), max(prev[1], value[1]))
            env[name] = value
        elif isinstance(st, ast.If):
            _scan_assignments(st.body, env, guarded=True)
            _scan_assignments(st.orelse, env, guarded=True)


def style_bounds():
    """(bounds, exact) derived from the source of ``style_for_seed``.

    ``bounds`` maps every constructor field to the interval it can take - a
    single ``(lo, hi)``, or a list of them for a tuple field. ``exact`` is the
    subset that is a single literal draw, where the interval is not merely
    sound but tight, so those may additionally be required to FILL it.

    Nothing here is a number copied out of the module. Change a range in
    ``style_for_seed`` and this changes with it; add a field and it is covered
    automatically; write an expression this cannot evaluate and the test says
    so by name instead of skipping the field.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "invisible_playwright" / "_motion.py"
    fn = next(
        n for n in ast.walk(ast.parse(src.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "style_for_seed"
    )
    env: dict[str, object] = {}
    _scan_assignments(fn.body, env)
    call = next(
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "MotionStyle"
    )
    bounds: dict[str, object] = {}
    exact: dict[str, str] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise _Unsupported("**kwargs in the MotionStyle call")
        bounds[kw.arg] = _iv_or_tuple(kw.value, env)
        if (isinstance(kw.value, ast.Call) and isinstance(kw.value.func, ast.Attribute)
                and kw.value.func.attr in ("uniform", "randint")):
            exact[kw.arg] = kw.value.func.attr
    return bounds, exact


_STYLE_SEEDS = list(range(400)) + [0, 2**31 - 1, -7]


def test_every_style_field_is_covered_by_a_derived_bound():
    """No field may slip past the range check, whoever adds it."""
    bounds, exact = style_bounds()
    assert set(bounds) == set(MotionStyle.__dataclass_fields__), (
        f"missing: {sorted(set(MotionStyle.__dataclass_fields__) - set(bounds))}; "
        f"stale: {sorted(set(bounds) - set(MotionStyle.__dataclass_fields__))}"
    )
    # If the parser ever stopped recognising the draw form, every field would
    # fall out of `exact` and the fill check below would pass vacuously.
    assert len(exact) > 0.6 * len(bounds), (
        f"only {len(exact)} of {len(bounds)} fields parsed as a direct draw"
    )


def test_every_style_draw_lands_inside_its_own_source_range():
    """Whatever a seed draws must land inside the range the module declares."""
    bounds, _ = style_bounds()
    for seed in _STYLE_SEEDS:
        s = style_for_seed(seed)
        for field, bound in bounds.items():
            value = getattr(s, field)
            pairs = zip(value, bound) if isinstance(bound, list) else [(value, bound)]
            for v, (lo, hi) in pairs:
                assert lo - 1e-9 <= v <= hi + 1e-9, (
                    f"seed {seed}: {field}={v!r} outside [{lo}, {hi}]"
                )


def test_every_direct_style_draw_actually_fills_its_range():
    """A range that is declared wide and used narrow is a fiction.

    Only the fields whose bound is exact - a single literal draw - are asked
    to fill it; a bound derived through ``min()`` and addition is sound but
    not tight, so demanding it be filled would assert something untrue.
    """
    bounds, exact = style_bounds()
    styles = [style_for_seed(seed) for seed in _STYLE_SEEDS]
    for field, kind in exact.items():
        lo, hi = bounds[field]
        values = [getattr(s, field) for s in styles]
        if kind == "randint":
            for v in values:
                assert isinstance(v, int), f"{field}={v!r} is not an int"
            assert min(values) == int(lo) and max(values) == int(hi), (
                f"{field} never draws its endpoints: {min(values)}..{max(values)}"
            )
            continue
        span = hi - lo
        assert min(values) < lo + 0.15 * span, f"{field} never approaches its floor"
        assert max(values) > hi - 0.15 * span, f"{field} never approaches its ceiling"


def test_the_style_bounds_are_derived_and_not_restated():
    """Guard on the guard: the derivation must read the module, not a copy."""
    bounds, _ = style_bounds()
    assert bounds, "no bounds derived at all"
    # Every bound is a real interval, and no field is bounded by (-inf, inf).
    for field, bound in bounds.items():
        for lo, hi in (bound if isinstance(bound, list) else [bound]):
            assert lo <= hi, (field, lo, hi)
            assert math.isfinite(lo) and math.isfinite(hi), (field, lo, hi)


def test_computed_style_fields_satisfy_their_properties():
    """The claims that are properties, stated without restating any number.

    ``ease_a_*`` and ``ease_r_*`` bound the Beta speed density drawn per
    movement, ``v(t) = t^(a-1) (1-t)^(b-1)`` with ``b = a * r``. Those
    requirements are mathematical, not stylistic: both shape parameters above
    1, so speed is zero at both ends instead of infinite acceleration at the
    start, and a speed peak that stays off both ends of the movement for every
    corner of the range a session can draw from.
    """
    lo_hi_pairs = sorted(
        name[:-3] for name in MotionStyle.__dataclass_fields__
        if name.endswith("_lo") and name[:-3] + "_hi" in MotionStyle.__dataclass_fields__
    )
    assert lo_hi_pairs, "no _lo/_hi ranges found - the naming convention moved"

    for seed in _STYLE_SEEDS:
        s = style_for_seed(seed)
        # Any range a session draws from must be a range, not a point or an
        # inversion. Derived from the field names, so a new pair is covered.
        for stem in lo_hi_pairs:
            lo, hi = getattr(s, stem + "_lo"), getattr(s, stem + "_hi")
            assert lo < hi, f"seed {seed}: {stem}_lo={lo} is not below {stem}_hi={hi}"
        for a in (s.ease_a_lo, s.ease_a_hi):
            for rr in (s.ease_r_lo, s.ease_r_hi):
                b = a * rr
                assert a > 1.0 and b > 1.0, (seed, a, b)
                peak = (a - 1.0) / (a + b - 2.0)
                assert 0.20 < peak < 0.70, (seed, a, rr, peak)
        # The knot base positions are the session's characteristic control
        # polygon: sorted, strictly interior, and one per possible knot.
        assert len(s.knot_base) == len(s.bow_base) == 3
        assert list(s.knot_base) == sorted(s.knot_base)
        assert all(0.0 < k < 1.0 for k in s.knot_base)
        assert s.knots <= len(s.knot_base)


# ════════════════════════════════════════════════════════════════════
# 7. pure and offline
# ════════════════════════════════════════════════════════════════════


def test_module_imports_nothing_but_stdlib_arithmetic():
    """No Playwright, no network, no filesystem, no clock.

    Checked on the source rather than at runtime, so a lazily imported module
    inside a function body is caught too.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "invisible_playwright" / "_motion.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pytest.fail(f"relative import of {node.module!r} - the module must stand alone")
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"__future__", "math", "random", "bisect", "dataclasses"}, mods
    forbidden = ("open(", "socket", "requests", "urllib", "time.", "os.", "subprocess")
    text = src.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("#", "*"))
    )
    for token in forbidden:
        assert token not in body, f"{token!r} has no business in a pure planner"


def test_generation_is_free_of_ambient_state():
    """Two interpreters' worth of global RNG churn must not change the output."""
    random.seed(1)
    a = [CursorMotion(555).path(0.0, 0.0, 800.0, 600.0) for _ in range(5)]
    for _ in range(1000):
        random.random()
    random.seed(999)
    b = [CursorMotion(555).path(0.0, 0.0, 800.0, 600.0) for _ in range(5)]
    assert a == b
