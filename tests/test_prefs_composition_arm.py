"""The wrapper's arm of the core's prefs-composition comparison.

The core compares its two entry points against each other
(`invisible_core/tests/test_prefs_composition.py`). This is the third: the
wrapper's `build_prefs`, which was the third place stacking layers on top of
`translate_profile_to_prefs` in its own order while nothing compared the
results.

It lives here rather than there because this package pins `invisible-core==`
exactly - the core's suite cannot import a consumer, and a consumer may only use
what the index has.
"""
from __future__ import annotations

import sys

import pytest

from invisible_core import generate_profile, get_default_stealth_prefs
from invisible_playwright._cursor import ENGINE_BINARY, ENGINE_PYTHON
from invisible_playwright._session import build_prefs

pytestmark = pytest.mark.unit

SEED = 424242
LOCALE = "en-US"
TZ = "America/New_York"

#: What the wrapper's path is ALLOWED to differ by from the public API's, and
#: why. An exact set: a new divergence has to be added here deliberately.
DELIBERATE_VS_PUBLIC_API = {
    # The wrapper draws its own trajectories, so the binary's generator must be
    # OFF or every waypoint it sends would itself be expanded into a path.
    # get_default_stealth_prefs exists for a caller who has no generator of
    # their own, so it leaves the binary's on.
    "stealthfox.humanize.maxTime",
}


def test_the_wrapper_composes_what_the_public_api_composes():
    profile = generate_profile(SEED)
    mine = build_prefs(profile=profile, locale=LOCALE, timezone=TZ,
                       extra_prefs=None, headless=False,
                       cursor_engine=ENGINE_PYTHON, humanize=True)
    public = get_default_stealth_prefs(SEED, locale=LOCALE, timezone=TZ)

    difference = set(mine) ^ set(public)
    assert difference == DELIBERATE_VS_PUBLIC_API, (
        "the wrapper and the public API no longer build the same prefs.\n"
        f"  only in build_prefs:                {sorted(set(mine) - set(public))}\n"
        f"  only in get_default_stealth_prefs:  {sorted(set(public) - set(mine))}\n"
        "If a new one is deliberate, add it to DELIBERATE_VS_PUBLIC_API.")

    differing = {k: (mine[k], public[k]) for k in set(mine) & set(public)
                 if mine[k] != public[k] and k != "stealthfox.humanize"}
    assert not differing, (
        "same keys, different values - worse than a missing key, because "
        f"nothing looks wrong: {differing}")


def test_the_binary_engine_still_produces_the_cap_it_always_did():
    """The delegation must not change what either engine writes.

    ENGINE_BINARY means the browser draws the path, so the pref is on and the
    cap is written; ENGINE_PYTHON means the wrapper draws it, so the pref is off
    and no cap is written - both exactly as before the core took the layer.
    """
    profile = generate_profile(SEED)
    binary = build_prefs(profile=profile, locale=LOCALE, timezone=TZ,
                         extra_prefs=None, headless=False,
                         cursor_engine=ENGINE_BINARY, humanize=2.5)
    python = build_prefs(profile=profile, locale=LOCALE, timezone=TZ,
                         extra_prefs=None, headless=False,
                         cursor_engine=ENGINE_PYTHON, humanize=2.5)

    assert binary["stealthfox.humanize"] is True
    assert binary["stealthfox.humanize.maxTime"] == "2.5"
    assert python["stealthfox.humanize"] is False
    assert "stealthfox.humanize.maxTime" not in python


def test_a_zero_cap_with_the_binary_engine_means_the_default_not_off():
    """The edge the delegation had to be written around.

    `humanize=0` with the binary engine selected is a cap of nothing, not a
    request to disable motion - `max_seconds_for` has always turned it into the
    default. Passing `humanize` straight through to the core would have made it
    falsy and switched the generator off, which is a different browser.
    """
    prefs = build_prefs(profile=generate_profile(SEED), locale=LOCALE,
                        timezone=TZ, extra_prefs=None, headless=False,
                        cursor_engine=ENGINE_BINARY, humanize=0)
    assert prefs["stealthfox.humanize"] is True
    assert prefs["stealthfox.humanize.maxTime"] == "1.5"


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"),
                    reason="the cloak is a Windows/macOS path")
def test_headless_still_applies_the_cloak_without_beating_extra_prefs():
    """setdefault, which is the precedence this layer had here before the core
    took it: an explicit user override wins over the cloak."""
    from invisible_core import cloak_prefs

    key = next(iter(cloak_prefs()))
    plain = build_prefs(profile=generate_profile(SEED), locale=LOCALE,
                        timezone=TZ, extra_prefs=None, headless=True,
                        cursor_engine=ENGINE_PYTHON, humanize=True)
    overridden = build_prefs(profile=generate_profile(SEED), locale=LOCALE,
                             timezone=TZ, extra_prefs={key: "mine"},
                             headless=True, cursor_engine=ENGINE_PYTHON,
                             humanize=True)

    assert key in plain, "the cloak pref did not reach a headless session"
    assert overridden[key] == "mine", "the cloak overwrote an explicit override"
