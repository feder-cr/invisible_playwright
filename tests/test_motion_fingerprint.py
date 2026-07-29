"""The paths must not move without somebody deciding they should.

Seed-reproducibility is a documented property callers rely on
(``InvisiblePlaywright(seed=42)``), and it is invisible to the statistical
tests next door: they check distributions, and a refactor that shifts every
coordinate leaves every distribution intact.

WHAT IS PINNED, AND WHY IT IS NOT THE RAW FLOATS
------------------------------------------------
The first version of this file hashed ``round(x, 10)`` - float64, essentially
verbatim - and it went red on GitHub on three runners out of four while passing
locally. Measured rather than guessed, by running the identical grid on Windows
and on Linux:

  * on the SAME Python, 1 case out of 576 differs, with the same waypoint
    count. A last-bit libm difference in one coordinate, at one input;
  * hashing the INTEGER PIXELS instead, the two platforms are byte-identical.

Which settles what the promise can honestly be. A pointer event carries integer
coordinates: that sub-bit disagreement is not observable by a page, by a
detector or by a user - it exists only in a float nobody dispatches. Pinning it
was pinning an implementation detail of libm, and a gate that cannot be green on
a supported platform is a gate somebody eventually deletes.

So this pins what actually reaches the wire: the rounded pixel, the
millisecond, and the number of events. That is the level the product promises
and the level a regression would be felt at.

WHAT IT STILL CATCHES, WHICH IS THE POINT
-----------------------------------------
Extracting the planner into stages looked completely safe and changed the
output twice, both times through float multiplication not being associative:

  * ``axis.at(dist * u, ...)`` computes ``ux * (dist * u)`` where the original
    wrote ``ux * dist * u``, i.e. ``(ux * dist) * u``;
  * returning ``amp * w`` from the overshoot and multiplying by the unit vector
    at the call site regrouped the same product.

Both moved rounded pixels, not merely last bits, so both are still caught here.
Reordering a draw from the rng, or adding one, moves the counts - which is why
the count digest is asserted separately: it is integer-valued, so no platform
explains it away, and it fails first with a clearer message.

If this goes red the question is not "which constant do I update". It is "did I
mean to change every path of every session", and the answer is almost always no.
"""
from __future__ import annotations

import hashlib
import json
import random

import pytest

from invisible_playwright import _motion

pytestmark = pytest.mark.unit

CASES = [(0, 0, 50, 20), (0, 0, 600, 400), (100, 100, 105, 102), (0, 0, 3, 1),
         (800, 600, 20, 40), (0, 0, 1200, 20), (500, 500, 500, 500), (0, 0, 0, 900)]

#: What a page receives: integer coordinates and the schedule to a tenth of a
#: millisecond. Verified identical on Windows and Linux.
PIXEL_FINGERPRINT = "3674be33e472039b56f27949480212d027ee2f670c861cd9fa6dc5c54ef8e29f"

#: How many events each case emits. Integer-valued, so it is immune to
#: arithmetic drift and moves only when the rng is consumed differently.
COUNT_FINGERPRINT = "1e15df39cb63e480a9e5cc5c797757a52156bcd13b576dbe260ba4ef5b594c50"

CASE_COUNT = 576
WAYPOINT_COUNT = 16135


def _grid():
    """Every planned path in the grid, as (pixels, counts)."""
    rows, counts = [], []
    for seed in range(12):
        style = _motion.style_for_seed(seed)
        for (ax, ay, bx, by) in CASES:
            for jitter in (True, False):
                for target_w in (None, 20.0, 220.0):
                    rng = random.Random(seed * 7919 + 13)
                    path = _motion._plan(rng, ax, ay, bx, by, style,
                                         with_jitter=jitter, target_w=target_w)
                    rows.append([[round(p.x), round(p.y), round(p.t_ms, 1)]
                                 for p in path])
                    counts.append(len(path))
    return rows, counts


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def test_the_number_of_events_per_path_is_unchanged():
    """Asserted before the coordinates, deliberately.

    A changed count means the rng was consumed differently - a draw added,
    removed or reordered - which is a categorically different mistake from a
    coordinate moving, and should not be reported as "the pixels changed".
    """
    _, counts = _grid()
    assert (len(counts), sum(counts)) == (CASE_COUNT, WAYPOINT_COUNT), (
        f"the grid itself changed: {len(counts)} cases / {sum(counts)} waypoints")
    assert _digest(counts) == COUNT_FINGERPRINT, (
        "the number of events per path moved, so the rng is being consumed "
        "differently - a draw was added, removed or reordered. That is not an "
        "arithmetic difference and no platform explains it")


def test_the_dispatched_pixels_are_unchanged():
    """What a page would actually receive."""
    rows, _ = _grid()
    assert _digest(rows) == PIXEL_FINGERPRINT, (
        "every planned path moved at the pixel level. If that was deliberate, "
        "re-record the fingerprint IN THE SAME COMMIT as the change that "
        "caused it and say which change; if it was not, something regrouped an "
        "arithmetic expression or reordered a draw from the rng")


def test_the_fingerprint_is_actually_sensitive():
    """A fingerprint that would survive a real change is decoration.

    Perturbs one style field by one part in ten thousand and requires the pixel
    digest to move. Without this, the tests above could be passing because the
    grid collapsed to nothing.
    """
    original = _motion.style_for_seed

    def nudged(seed):
        style = original(seed)
        return style.__class__(**{**style.__dict__,
                                  "bow_frac": style.bow_frac * 1.0001})

    _motion.style_for_seed = nudged
    try:
        rows, _ = _grid()
    finally:
        _motion.style_for_seed = original
    assert _digest(rows) != PIXEL_FINGERPRINT, (
        "a 0.01% change to the bow fraction left the pixels identical - the "
        "fingerprint is not measuring the paths")


def test_the_coordinates_hashed_here_really_are_integers():
    """Guards the decision that makes this portable at all.

    The raw float64 output is NOT identical across platforms - measured, one
    case in 576 - so an edit that "tightens" this back to raw floats would
    reintroduce a permanently red CI on some runner. Asserting the hashed
    coordinates are integers keeps that decision visible at the point somebody
    would undo it.
    """
    rows, _ = _grid()
    flat = [v for path in rows for point in path for v in point[:2]]
    assert flat, "the grid produced no coordinates at all"
    assert all(isinstance(v, int) for v in flat), (
        "the fingerprint is hashing non-integer coordinates again; that is "
        "libm-dependent and cannot be green on every supported platform")
