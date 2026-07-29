"""Unit tests for the macro pointer-behaviour planner (`_behaviour`).

Everything here is arithmetic on a planned timeline. No browser is launched,
no page is loaded, nothing sleeps: the planner is pure by design precisely so
that its statistical properties can be asserted instead of eyeballed.

The four properties under test map one-to-one onto the four things a
behavioural classifier can measure about a pointer stream:

  1. reproducibility  - one seed replays exactly, two seeds look like two
                        different hands (nothing is a package-wide constant).
  2. bounds           - no coordinate leaves the viewport, no delay is
                        physically impossible, no zero-displacement event.
  3. distribution     - pauses are heavy-tailed, movement time depends on
                        distance and target size, the speed peak is in the
                        middle of the movement and the jitter is zero-mean.
  4. occupancy        - the pointer is actually in motion for a materially
                        larger share of the session than the ~4% that
                        motivated this work, and its movements do not all
                        terminate on a control.
"""
from __future__ import annotations

import math
import random
import statistics

import pytest

from invisible_playwright._behaviour import (
    BURST_GAP_MS,
    PointerPersona,
    Step,
    initial_pointer,
    landing_point,
    overshoot_probability,
    plan_aimless_move,
    plan_approach,
    plan_idle,
    plan_scroll,
    steps_from_waypoints,
    summarize,
    tail_within,
)
from invisible_playwright import _behaviour

pytestmark = pytest.mark.unit

# ── the one stroke model ────────────────────────────────────────────────────
# `plan_move` is gone: this module no longer carries a stroke model of its own,
# because two of them in one package is a signature we would be creating. The
# properties those tests asserted are still worth asserting - they just have to
# be asserted against the model that actually ships. This builds the same
# renderer `_cursor.PageCursor.renderer` builds, from `_motion`.
def _renderer(seed=0):
    """The renderer `_cursor.PageCursor.renderer` builds, for tests.

    Every planner takes it as a REQUIRED argument now. Before 2026-07-26 it was
    optional and these tests silently exercised a second stroke model that also
    shipped; passing it here is what makes them test the code that runs.
    """
    from invisible_playwright import _motion
    # duration_ms/target_w/noise_scale were hints to the deleted built-in model.
    # A renderer owns its own pacing, so they are accepted and ignored rather
    # than half-applied - a stroke that is neither model is worse than either.
    profile = _motion.CursorMotion(seed)

    def render(start, end, *, kind, lead_ms, bounds=None, target_w=None):
        raw = list(profile.path(start[0], start[1], end[0], end[1], target_w=target_w))
        return steps_from_waypoints(raw, kind=kind, lead_ms=lead_ms,
                                    bounds=bounds if bounds is not None else VIEWPORT)
    return render


def _stroke(persona, rng, start, end, *, kind="move", bounds=None, lead_ms=0.0,
            duration_ms=None, target_w=None, noise_scale=None):
    from invisible_playwright import _motion
    bounds = VIEWPORT if bounds is None else bounds   # late-bound: VIEWPORT is defined below
    profile = _motion.CursorMotion(rng.randrange(1 << 30))
    raw = list(profile.path(start[0], start[1], end[0], end[1], target_w=target_w))
    return steps_from_waypoints(raw, kind=kind, lead_ms=lead_ms, bounds=bounds)



VIEWPORT = (1280.0, 720.0)

# A representative page: a nav bar, two links, a small button, a big CTA.
CONTROLS = [
    (40.0, 18.0, 90.0, 28.0),
    (620.0, 240.0, 180.0, 40.0),
    (300.0, 430.0, 42.0, 20.0),
    (900.0, 600.0, 260.0, 56.0),
    (150.0, 300.0, 120.0, 24.0),
]

# A ~3 minute browsing session: mostly reading and scrolling, four clicks.
# Deliberately read-heavy, because that is the regime the current behaviour
# scores ~4% in and the regime a plausible session actually spends its time in.
SCRIPT = [
    ("read", 12000),
    ("scroll", 6),
    ("read", 22000),
    ("scroll", 9),
    ("read", 18000),
    ("click", CONTROLS[1]),
    ("read", 9000),
    ("scroll", 12),
    ("read", 26000),
    ("click", CONTROLS[2]),
    ("read", 14000),
    ("move",),
    ("read", 20000),
    ("scroll", 7),
    ("read", 16000),
    ("click", CONTROLS[3]),
    ("read", 24000),
]

SEEDS = [1, 42, 777, 20260726, 99999, 31337, 8675309, 4, 5150, 271828]


def _plan(seed, render=None):
    # Default to the real renderer, never to None. `render` became a required
    # argument when the second stroke model was deleted, and a None default
    # here just moves the failure from the signature to the call - which is
    # exactly the silent-second-law shape the deletion removed.
    render = _renderer(seed) if render is None else render
    """Compose one session's timeline from the script above.

    This composer lives in the test, not in the package. A session-level
    planner needs the whole task list in advance, and nothing in the product
    ever has it: the package is driven one action at a time by somebody else's
    script. In here a fixed script is legitimate, because the point is to
    measure what the planners produce when they are strung together the way the
    dispatcher strings them.
    """
    persona = PointerPersona.from_seed(seed)
    pos = initial_pointer(seed, VIEWPORT)
    gate = random.Random(seed ^ 0x5E5510)
    steps = []
    nonce = 0

    def add(seg):
        nonlocal pos
        if seg:
            steps.extend(seg)
            pos = (seg[-1].x, seg[-1].y)

    for action in SCRIPT:
        nonce += 1
        name = action[0]
        if name == "read":
            add(plan_idle(seed, persona, pos, VIEWPORT, float(action[1]),
                          nonce=nonce, render=render))
        elif name == "click":
            add(plan_approach(seed, persona, pos, tuple(action[1]), VIEWPORT,
                              nonce=nonce, render=render))
        elif name == "scroll":
            add(plan_scroll(seed, persona, pos, VIEWPORT, ticks=int(action[1]),
                            nonce=nonce, render=render))
        else:
            add(plan_aimless_move(seed, persona, pos, VIEWPORT, avoid=CONTROLS,
                                  nonce=nonce, render=render))
        if gate.random() < persona.aimless_rate:
            nonce += 1
            extra = plan_aimless_move(seed, persona, pos, VIEWPORT,
                                      avoid=CONTROLS, nonce=nonce, render=render)
            if extra:
                first = extra[0]
                extra[0] = Step(first.x, first.y,
                                first.delay_ms + gate.uniform(220.0, 1800.0),
                                first.kind)
                add(extra)
    return steps


# ──────────────────────────────────────────────────────────────────────
# 1. Reproducibility, and per-session variation
# ──────────────────────────────────────────────────────────────────────

def test_same_seed_replays_exactly():
    for seed in SEEDS[:4]:
        assert _plan(seed) == _plan(seed)


def test_persona_is_a_pure_function_of_the_seed():
    assert PointerPersona.from_seed(42) == PointerPersona.from_seed(42)
    assert PointerPersona.from_seed(42) != PointerPersona.from_seed(43)


def test_different_seeds_give_different_timelines():
    plans = [tuple(_plan(s)) for s in SEEDS]
    assert len(set(plans)) == len(plans)


def test_every_persona_parameter_actually_varies_across_sessions():
    """No field may be a constant - a constant is shared by every install."""
    personas = [PointerPersona.from_seed(s) for s in range(400)]
    for field in (
        "sample_ms", "fitts_a_ms", "fitts_b_ms", "mt_sigma", "peak_frac",
        "curvature", "tremor_px", "drift_px", "reposition_px",
        "pause_median_ms", "pause_sigma", "overshoot_bias", "aimless_rate",
    ):
        values = [getattr(p, field) for p in personas]
        spread = statistics.pstdev(values) / statistics.fmean(values)
        assert spread > 0.05, f"{field} is effectively constant (cv={spread:.4f})"


def test_persona_fields_stay_in_their_documented_ranges():
    for seed in range(500):
        p = PointerPersona.from_seed(seed)
        assert 9.0 <= p.sample_ms <= 21.0
        assert 90.0 <= p.fitts_a_ms <= 210.0
        assert 105.0 <= p.fitts_b_ms <= 195.0
        assert 0.36 <= p.peak_frac <= 0.50
        assert 0.0 < p.curvature <= 0.038
        assert 0.35 <= p.tremor_px <= 1.60
        assert 1400.0 <= p.pause_median_ms <= 4800.0
        assert 0.0 < p.aimless_rate < 1.0


def test_initial_pointer_is_never_the_viewport_origin():
    """The first movement of a session must not depart from (0, 0)."""
    pts = [initial_pointer(s, VIEWPORT) for s in range(300)]
    for x, y in pts:
        assert 0.0 < x < VIEWPORT[0]
        assert 0.0 < y < VIEWPORT[1]
        assert math.hypot(x, y) > 20.0
    # and it is not always the same spot either
    assert len({(round(x), round(y)) for x, y in pts}) > 250


# ──────────────────────────────────────────────────────────────────────
# 2. Bounds and hygiene
# ──────────────────────────────────────────────────────────────────────

def test_all_coordinates_stay_inside_the_viewport():
    for seed in SEEDS:
        for s in _plan(seed):
            assert 0.0 <= s.x <= VIEWPORT[0]
            assert 0.0 <= s.y <= VIEWPORT[1]


def test_duplicate_positions_happen_but_stay_rare():
    """A band, not a zero. "Never" is a signature too.

    This asserted `== 0` until 2026-07-26, on the reasoning that a duplicate
    mousemove is useless. The measurement said the opposite: a real pointer
    moving slowly DOES report the same device pixel twice, and a generator that
    never does it holds an invariant no hand holds - it was exactly 0.000000 in
    12000 of 12000 paths across 300 seeds, which is as recognisable as being
    always +0.5 px off.

    The band's ends both matter. Zero fails, because that is the invariant we
    removed. The upper end is the in-binary generator, which emitted 8.07% of
    steps as duplicates with a run of 3 or more at the tail of EVERY movement;
    landing there would trade one tell for another.
    """
    fracs = []
    for seed in SEEDS:
        stats = summarize(_plan(seed))
        fracs.append(stats.n_duplicate_positions / max(1, stats.n_steps))
    mean_frac = statistics.fmean(fracs)
    assert mean_frac > 0.0, (
        "no session ever repeated a pixel; 'never' is the invariant this "
        "replaced, not the goal")
    # UPPER END IS A REGRESSION GUARD, NOT A CLEAN BILL. Measured 2026-07-26
    # through the full macro path: 0.073 mean here, 0.098 measuring plan_idle
    # directly (max 0.294). The in-binary generator was 0.081. So on THIS tell
    # the composed system is at parity with what it replaced, not better - the
    # micro-movements a reading pointer makes have displacements that round to
    # the same device pixel, and _motion's own deliberate 0.017 rate is a small
    # part of it. This bound stops it getting worse; it does not claim it is
    # good. Whether the dispatcher filters these before the wire is UNMEASURED,
    # and that measurement is the open question - see the objectives list.
    assert mean_frac < 0.12, (
        "duplicates at %.3f of steps, worse than the legacy generator's 0.081"
        % mean_frac)


def test_delays_are_physically_possible():
    for seed in SEEDS:
        for s in _plan(seed):
            # 5 ms floor: a 125 Hz mouse cannot report faster than every 8 ms.
            assert s.delay_ms >= 5.0
            assert s.delay_ms <= 75_000.0


def test_a_movement_terminates_exactly_on_its_requested_point():
    p = PointerPersona.from_seed(7)
    import random
    for i in range(200):
        r = random.Random(i)
        start = (r.uniform(0, 1280), r.uniform(0, 720))
        end = (r.uniform(0, 1280), r.uniform(0, 720))
        steps = _stroke(p, r, start, end, bounds=VIEWPORT)
        assert steps
        assert steps[-1].x == pytest.approx(end[0])
        assert steps[-1].y == pytest.approx(end[1])


def test_a_sub_pixel_move_produces_nothing():
    import random
    p = PointerPersona.from_seed(7)
    assert _stroke(p, random.Random(0), (300.0, 300.0), (300.2, 300.1)) == []


def test_an_approach_lands_inside_the_target_but_not_on_its_centre():
    box = (620.0, 240.0, 180.0, 40.0)
    bx, by, bw, bh = box
    centre_hits = 0
    landings = []
    for seed in range(300):
        p = PointerPersona.from_seed(seed)
        steps = plan_approach(seed, p, (100.0, 650.0), box, VIEWPORT, render=_renderer())
        assert steps
        x, y = steps[-1].x, steps[-1].y
        assert bx <= x <= bx + bw and by <= y <= by + bh
        landings.append((x, y))
        if abs(x - (bx + bw / 2)) < 0.5 and abs(y - (by + bh / 2)) < 0.5:
            centre_hits += 1
    assert centre_hits == 0
    # the landing scatter is real, not a fixed offset from the centre
    assert statistics.pstdev([x for x, _ in landings]) > 5.0


# ──────────────────────────────────────────────────────────────────────
# 3. Distributional shape
# ──────────────────────────────────────────────────────────────────────

def _pauses(steps):
    """Delays that open a burst of motion - the silences between movements.

    "wait" steps are excluded: their delay is leftover session time, not a
    drawn pause.
    """
    return [s.delay_ms for s in steps
            if s.kind not in ("wait", "wheel") and s.delay_ms > BURST_GAP_MS]


def test_pause_distribution_is_heavy_tailed_not_uniform():
    """A log-normal has a long right tail; a uniform or Gaussian does not."""
    import random
    for seed in (1, 2026, 31337):
        p = PointerPersona.from_seed(seed)
        r = random.Random(seed)
        pauses = sorted(_behaviour._pause_ms(p, r) for _ in range(20_000))
        p50 = statistics.median(pauses)
        p95 = pauses[int(0.95 * len(pauses))]
        # A uniform distribution has p95/p50 < 2 and a Gaussian with any
        # plausible cv is under 2 as well. Only a heavy tail clears 3.
        assert p95 / p50 > 3.0, f"seed {seed}: p95/p50 = {p95 / p50:.2f}"
        # real stillness must happen, not just "a slightly longer gap"
        assert sum(1 for v in pauses if v > 6_000.0) / len(pauses) > 0.01
        assert max(pauses) > 15_000.0, "no long stillness at all is not reading"
        assert min(pauses) >= 180.0
        # ...and in log space it is roughly symmetric, which is what
        # log-normal means. Pearson's second skewness coefficient on log().
        logs = [math.log(v) for v in pauses]
        skew = (3.0 * (statistics.fmean(logs) - statistics.median(logs))
                / statistics.pstdev(logs))
        assert abs(skew) < 0.25, f"log(pause) not symmetric (skew={skew:.3f})"


def test_idle_actually_uses_that_pause_distribution():
    """The shape above is only worth anything if the planner emits it."""
    p = PointerPersona.from_seed(2026)
    steps = plan_idle(2026, p, (640.0, 360.0), VIEWPORT, 3_600_000.0, render=_renderer())
    pauses = sorted(_pauses(steps))
    assert len(pauses) > 150
    p50 = statistics.median(pauses)
    assert pauses[int(0.95 * len(pauses))] / p50 > 2.5
    assert max(pauses) > 12_000.0


def test_pause_median_differs_between_sessions():
    medians = []
    for seed in SEEDS:
        p = PointerPersona.from_seed(seed)
        steps = plan_idle(seed, p, (640.0, 360.0), VIEWPORT, 900_000.0, render=_renderer())
        pauses = _pauses(steps)
        if len(pauses) > 40:
            medians.append(statistics.median(pauses))
    assert len(medians) >= 5
    cv = statistics.pstdev(medians) / statistics.fmean(medians)
    assert cv > 0.15, f"pause scale barely varies between sessions (cv={cv:.3f})"


def test_movement_time_depends_on_distance_and_target_size():
    """Fitts, not a constant. Every movement taking ~1 s is the tell."""
    import random
    p = PointerPersona.from_seed(11)

    def mean_duration(dist, width):
        out = []
        for i in range(120):
            r = random.Random(i)
            steps = _stroke(p, r, (10.0, 360.0), (10.0 + dist, 360.0),
                              target_w=width, bounds=(2000.0, 720.0))
            out.append(sum(s.delay_ms for s in steps))
        return statistics.fmean(out)

    easy = mean_duration(80.0, 240.0)      # near, big
    far = mean_duration(1000.0, 240.0)     # far, big
    hard = mean_duration(1000.0, 12.0)     # far, small
    assert far > easy * 1.6
    assert hard > far * 1.4


def test_speed_peaks_in_the_middle_of_a_movement_not_at_the_start():
    """A profile that starts at maximum speed implies infinite acceleration."""
    import random
    peaks = []
    for seed in range(200):
        p = PointerPersona.from_seed(seed)
        r = random.Random(seed)
        steps = _stroke(p, r, (60.0, 640.0), (1180.0, 90.0), bounds=VIEWPORT)
        pos = [(60.0, 640.0)] + [(s.x, s.y) for s in steps]
        speeds = [
            math.hypot(pos[i + 1][0] - pos[i][0], pos[i + 1][1] - pos[i][1])
            / max(steps[i].delay_ms, 1e-6)
            for i in range(len(steps))
        ]
        peaks.append(speeds.index(max(speeds)) / len(speeds))
    med = statistics.median(peaks)
    assert 0.25 < med < 0.70, f"median peak-speed position = {med:.3f}"
    # the very first step is essentially never the fastest
    assert sum(1 for v in peaks if v < 0.05) / len(peaks) < 0.05


def test_the_timing_profile_itself_varies_between_sessions():
    """The old shape was one fixed curve with no random input at all."""
    fracs = [PointerPersona.from_seed(s).peak_frac for s in range(300)]
    assert statistics.pstdev(fracs) > 0.02
    assert len(set(round(f, 3) for f in fracs)) > 100


def test_micro_noise_is_zero_mean_on_both_axes():
    """A jitter law with a non-zero mean offsets every waypoint ever sent.

    The deliberate lateral bow is switched off here so that what is measured
    is the jitter law alone; the bow gets its own test below.
    """
    import dataclasses
    import random
    res_x, res_y = [], []
    for seed in range(150):
        p = dataclasses.replace(PointerPersona.from_seed(seed), curvature=1e-12)
        r = random.Random(seed)
        start, end = (100.0, 100.0), (1100.0, 620.0)
        steps = _stroke(p, r, start, end, bounds=VIEWPORT)
        dx, dy = end[0] - start[0], end[1] - start[1]
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        for s in steps[:-1]:
            along = (s.x - start[0]) * ux + (s.y - start[1]) * uy
            res_x.append(s.x - (start[0] + ux * along))
            res_y.append(s.y - (start[1] + uy * along))
    for name, res in (("x", res_x), ("y", res_y)):
        mean = statistics.fmean(res)
        sd = statistics.pstdev(res)
        assert sd > 0.2, f"{name} residual carries no noise at all"
        se = sd / math.sqrt(len(res))
        assert abs(mean) < 4.0 * se, f"{name} residual mean = {mean:+.4f} px"


def test_the_lateral_bow_has_no_preferred_side():
    """A curve that always bows the same way is a signature of its own."""
    import random
    signed = []
    for seed in range(400):
        p = PointerPersona.from_seed(seed)
        r = random.Random(seed)
        start, end = (100.0, 100.0), (1100.0, 620.0)
        steps = _stroke(p, r, start, end, bounds=VIEWPORT)
        dx, dy = end[0] - start[0], end[1] - start[1]
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        mid = steps[len(steps) // 2]
        signed.append((mid.x - start[0]) * -uy + (mid.y - start[1]) * ux)
    left = sum(1 for v in signed if v < 0) / len(signed)
    assert 0.4 < left < 0.6, f"bows left {left:.3f} of the time"
    # and the bow is a few percent of the distance, not a wild detour
    med_bow = statistics.median([abs(v) for v in signed])
    assert 0.002 * d < med_bow < 0.06 * d, f"median bow = {med_bow:.1f} px"


def test_both_axes_carry_noise():
    """An axis with exactly zero noise makes the whole curve solvable."""
    import random
    p = PointerPersona.from_seed(3)
    xs, ys = [], []
    for i in range(60):
        r = random.Random(i)
        steps = _stroke(p, r, (100.0, 400.0), (1100.0, 400.0), bounds=VIEWPORT)
        for s in steps[:-1]:
            xs.append(s.x)
            ys.append(s.y)
    # A purely analytic path would put every sample on integer-free exact
    # values with zero deviation on the axis with no jitter.
    assert statistics.pstdev([y - 400.0 for y in ys]) > 0.2
    assert len({round(x, 6) for x in xs}) == len(xs)


def test_overshoot_probability_rises_with_index_of_difficulty():
    import dataclasses
    p = dataclasses.replace(PointerPersona.from_seed(5), overshoot_bias=1.0)
    easy = overshoot_probability(p, 60.0, 240.0)
    mid = overshoot_probability(p, 500.0, 80.0)
    hard = overshoot_probability(p, 1200.0, 14.0)
    assert easy < mid < hard
    assert 0.0 < easy < 0.35
    assert hard > 0.45
    # the persona multiplier must actually move it, in both directions
    lo = overshoot_probability(dataclasses.replace(p, overshoot_bias=0.55),
                               500.0, 80.0)
    hi = overshoot_probability(dataclasses.replace(p, overshoot_bias=1.45),
                               500.0, 80.0)
    assert lo < mid < hi


def test_some_approaches_actually_overshoot_and_come_back():
    box = (300.0, 430.0, 42.0, 20.0)
    bx, by, bw, bh = box
    overshot = 0
    total = 0
    for seed in range(300):
        p = PointerPersona.from_seed(seed)
        steps = plan_approach(seed, p, (60.0, 60.0), box, VIEWPORT, render=_renderer())
        total += 1
        # went outside the target box at some point after first entering it
        entered = False
        for s in steps:
            inside = bx <= s.x <= bx + bw and by <= s.y <= by + bh
            if inside:
                entered = True
            elif entered:
                overshot += 1
                break
    frac = overshot / total
    assert 0.15 < frac < 0.95, f"overshoot fraction = {frac:.3f}"


def test_a_correction_waits_for_visual_feedback():
    """No corrective submovement may start instantly after the primary."""
    box = (300.0, 430.0, 42.0, 20.0)
    for seed in range(200):
        p = PointerPersona.from_seed(seed)
        steps = plan_approach(seed, p, (60.0, 60.0), box, VIEWPORT, render=_renderer())
        kinds = [s.kind for s in steps]
        if "correct" in kinds:
            i = kinds.index("correct")
            assert steps[i].delay_ms >= 40.0


# ──────────────────────────────────────────────────────────────────────
# 4. Occupancy - the headline property
# ──────────────────────────────────────────────────────────────────────

def _click_only_baseline(seed: int) -> list:
    """The behaviour this module replaces, expressed as a timeline.

    One movement per scripted click - ~100 events over ~1 s, which is what the
    in-binary expansion produces for a movement of any length - and complete
    silence otherwise: nothing during a read, nothing during a scroll, and no
    movement that is not a click. Every endpoint is a control.

    It exists so the "after" occupancy is measured with the same ruler as the
    "before" one rather than against a remembered number.
    """
    steps = []
    pending = 0.0
    for action in SCRIPT:
        if action[0] == "read":
            pending += float(action[1])
        elif action[0] == "scroll":
            pending += 150.0 * int(action[1])  # wheel only, pointer frozen
        elif action[0] == "click":
            bx, by, bw, bh = action[1]
            for i in range(100):
                steps.append(Step(bx + bw / 2 + i * 0.01, by + bh / 2,
                                  pending if i == 0 else 10.0, "approach"))
                pending = 0.0
        # ("move",) has no counterpart: the old behaviour never moves for
        # anything but a click.
    if pending:
        # Same accounting rule as the planner: time after the last event is
        # still part of the session, so it belongs in the denominator.
        steps.append(Step(steps[-1].x, steps[-1].y, pending, "wait"))
    return steps


def test_summarize_reproduces_the_click_only_baseline_occupancy():
    """Sanity-check the ruler: the old behaviour must score in the low units."""
    frac = summarize(_click_only_baseline(1)).active_fraction
    assert 0.0 < frac < 0.08, f"baseline occupancy = {frac:.4f}"


def test_pointer_active_fraction_is_materially_above_the_baseline():
    """The headline number."""
    planned = [summarize(_plan(s)).active_fraction for s in SEEDS]
    mean_planned = statistics.fmean(planned)
    baseline = summarize(_click_only_baseline(1)).active_fraction
    assert baseline < 0.03
    # 0.06, lowered from 0.08 on 2026-07-26, and the reason matters more than
    # the number. The threshold was calibrated while the stroke generator was
    # fed a per-seed ASSUMED target width of 33-53 px for every movement. Once
    # the planner started handing it the real box, movements to ordinary
    # controls - which are 100-200 px wide - got faster, because that is what
    # Fitts' law says and what a hand actually does. Measured: 0.0637 with the
    # assumed width, 0.0490 with the real one on a pure-idle plan.
    #
    # So the metric fell because the model got MORE accurate, not less. The
    # ratio assertion below is the one that carries the claim in this test's
    # own name, and it is comfortable: 4.12x the click-only baseline against a
    # required 3x. This absolute floor stays only so the ratio cannot be
    # satisfied by a degenerate baseline.
    #
    # It is not a target. Closing the rest of the gap is the macro layer's job
    # - more motion while reading - not the stroke's, and making strokes slower
    # to raise it would be falsifying the one law here that is measured.
    assert mean_planned > 0.06, f"planned active fraction = {mean_planned:.4f}"
    assert mean_planned > 3.0 * baseline
    assert min(planned) > 0.03, "some session is still effectively motionless"
    # ...and it is not a constant either: two sessions differ a lot. A fixed
    # occupancy would be as identifying as a fixed curve shape.
    assert statistics.pstdev(planned) / mean_planned > 0.15
    # nor is it so high that the pointer never rests, which is its own tell
    assert max(planned) < 0.55


def test_the_pointer_moves_during_a_read():
    """The gap the whole module exists to fill: silence between clicks."""
    for seed in SEEDS:
        p = PointerPersona.from_seed(seed)
        steps = plan_idle(seed, p, (640.0, 360.0), VIEWPORT, 30_000.0, render=_renderer())
        moves = [s for s in steps if s.kind not in ("wait", "wheel")]
        assert len(moves) > 10, f"seed {seed} produced a silent 30 s read"


def test_idle_accounts_for_exactly_its_budget():
    """The denominator of every occupancy figure. It has to be truthful."""
    for seed in range(150):
        p = PointerPersona.from_seed(seed)
        budget = 20_000.0
        total = sum(s.delay_ms
                    for s in plan_idle(seed, p, (640.0, 360.0), VIEWPORT, budget, render=_renderer()))
        # never short - a plan that under-reports its own duration inflates
        # every activity fraction taken from it
        assert total >= budget - 5.0, f"seed {seed}: {total:.0f} < {budget:.0f}"
        # and never meaningfully long: at most one action can straddle the end
        assert total <= budget * 1.35


def test_a_session_exercises_every_planned_behaviour():
    """All four macro behaviours must actually appear in a normal session."""
    kinds = set()
    for seed in SEEDS:
        kinds.update(s.kind for s in _plan(seed))
    assert {"settle", "drift", "park", "wheel", "scroll_drift",
            "approach", "correct", "wait"} <= kinds, sorted(kinds)


def test_the_pointer_moves_while_the_wheel_turns():
    for seed in SEEDS:
        p = PointerPersona.from_seed(seed)
        steps = plan_scroll(seed, p, (640.0, 360.0), VIEWPORT, ticks=12, render=_renderer())
        wheels = [s for s in steps if s.kind == "wheel"]
        moves = [s for s in steps if s.kind != "wheel"]
        assert len(wheels) == 12
        assert len(moves) >= 3, "the cursor held perfectly still while scrolling"


def test_not_every_movement_ends_on_a_control():
    """If the endpoints are always controls, the endpoint set is a signature."""
    fracs = []
    for seed in SEEDS:
        stats = summarize(_plan(seed))
        assert stats.n_bursts > 10
        fracs.append(stats.on_control_fraction)
    mean_frac = statistics.fmean(fracs)
    assert mean_frac < 0.35, f"bursts ending on a control = {mean_frac:.3f}"
    assert mean_frac > 0.0, "the scripted clicks must still land on controls"


def test_aimless_movements_avoid_the_known_controls():
    p = PointerPersona.from_seed(9)
    for nonce in range(400):
        steps = plan_aimless_move(9, p, (640.0, 360.0), VIEWPORT,
                                  avoid=CONTROLS, nonce=nonce, render=_renderer())
        if not steps:
            continue
        x, y = steps[-1].x, steps[-1].y
        for bx, by, bw, bh in CONTROLS:
            assert not (bx <= x <= bx + bw and by <= y <= by + bh)


# ──────────────────────────────────────────────────────────────────────
# 5. The stroke seam - the planner delegating to an injected renderer
# ──────────────────────────────────────────────────────────────────────

def test_an_injected_renderer_draws_every_stroke():
    """The seam that keeps ONE stroke model in the product.

    If a planner still called its own ``plan_move`` for even one leg, that leg
    would be drawn by a different model from all the others - two families of
    movement inside one session, which is worse than either family alone.
    """
    seen = []

    def render(start, end, *, kind, lead_ms, bounds, target_w=None):
        seen.append(kind)
        return [Step(x=end[0], y=end[1], delay_ms=lead_ms + 12.0, kind=kind)]

    steps = _plan(4242, render=render)
    assert seen, "the renderer was never called"
    assert set(seen) == {s.kind for s in steps if s.kind not in ("wheel", "wait")}
    # ...and nothing was drawn behind its back.
    assert all(s.kind in ("wheel", "wait") or s.delay_ms >= 12.0 for s in steps)


def test_steps_from_waypoints_drops_the_origin_and_clamps():
    class W:
        def __init__(self, x, y, dt):
            self.x, self.y, self.dt_ms = x, y, dt

    raw = [W(100.0, 100.0, 0.0), W(-40.0, 200.0, 12.0), W(9000.0, 5000.0, 11.0)]
    out = steps_from_waypoints(raw, kind="approach", lead_ms=30.0,
                               bounds=VIEWPORT)
    assert len(out) == 2, "the origin waypoint must not become an event"
    assert out[0].delay_ms == pytest.approx(42.0), "the lead must not be lost"
    for s in out:
        assert 0 <= s.x <= VIEWPORT[0] and 0 <= s.y <= VIEWPORT[1]


def test_steps_from_waypoints_keeps_a_duplicate_that_is_separated_in_time():
    """A stream in which two consecutive events NEVER share a pixel is as much
    an invariant as one in which every event is offset by half a pixel."""
    raw = [(10.0, 10.0, 0.0), (11.0, 11.0, 12.0), (11.0, 11.0, 40.0)]
    out = steps_from_waypoints(raw, kind="drift")
    assert len(out) == 2
    # ...while a repeat emitted in the same instant is wire noise and goes.
    tight = steps_from_waypoints([(10.0, 10.0, 0.0), (11.0, 11.0, 12.0),
                                  (11.0, 11.0, 0.0)], kind="drift")
    assert len(tight) == 1


# ──────────────────────────────────────────────────────────────────────
# 6. The tail of a plan - what the dispatcher can actually deliver
# ──────────────────────────────────────────────────────────────────────

def test_tail_within_respects_its_budget_and_re_anchors():
    p = PointerPersona.from_seed(77)
    plan = plan_idle(77, p, (640.0, 360.0), VIEWPORT, 30_000.0, render=_renderer())
    anchor = (200.0, 500.0)
    tail = tail_within(plan, 400.0, anchor, VIEWPORT)
    assert tail, "a 30 s idle plan has to have a dispatchable tail"
    # One episode may straddle the budget; nothing beyond that is dispatched.
    assert sum(s.delay_ms for s in tail[1:]) <= 400.0
    first = tail[0]
    assert math.hypot(first.x - anchor[0], first.y - anchor[1]) < 60.0, (
        "the tail must move from where the pointer really is, not from where a "
        "history that never happened left it"
    )
    for s in tail:
        assert 0 <= s.x <= VIEWPORT[0] and 0 <= s.y <= VIEWPORT[1]


def test_tail_within_is_empty_without_a_budget():
    p = PointerPersona.from_seed(3)
    plan = plan_idle(3, p, (640.0, 360.0), VIEWPORT, 20_000.0, render=_renderer())
    assert tail_within(plan, 0.0, (10.0, 10.0), VIEWPORT) == []


def test_landing_point_is_inside_biased_to_the_middle_and_never_the_centre():
    box = (600.0, 300.0, 120.0, 40.0)
    cx, cy = 660.0, 320.0
    pts = [landing_point(box, random.Random(s)) for s in range(600)]
    assert not any(p == (cx, cy) for p in pts)
    for x, y in pts:
        assert 600.0 <= x <= 720.0 and 300.0 <= y <= 340.0
    # biased to the middle: the mean sits on the centre, the spread does not
    assert abs(statistics.fmean(x for x, _ in pts) - cx) < 3.0
    assert statistics.pstdev([x for x, _ in pts]) > 5.0


# The naming guard used to live here, scanning this one module and skipping
# itself into a green run whenever the word list was not configured. It now
# lives in tests/test_naming_guard.py, covers every file the package ships,
# and fails closed. It is not duplicated here: two copies of a guard is two
# places for one of them to rot.
