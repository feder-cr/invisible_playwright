"""Unit tests for the public ``config`` helpers."""

import pytest

from invisible_playwright import (
    ensure_binary,
    get_default_args,
    get_default_stealth_prefs,
)
from invisible_playwright.config import get_default_stealth_prefs as _direct


pytestmark = pytest.mark.unit


def test_get_default_args_is_empty_list():
    """Currently no baseline CLI args, but must return a list (mutable, fresh each call)."""
    args = get_default_args()
    assert args == []
    assert isinstance(args, list)
    args.append("--foo")
    # next call must return a fresh empty list, not the mutated one
    assert get_default_args() == []


def test_get_default_stealth_prefs_random_seed_returns_dict():
    """No seed -> fresh random fingerprint, dict has expected stealth keys."""
    prefs = get_default_stealth_prefs()
    assert isinstance(prefs, dict)
    assert len(prefs) > 0
    # humanize toggle is always set explicitly
    assert "invisible_playwright.humanize" in prefs
    assert prefs["invisible_playwright.humanize"] is True


def test_get_default_stealth_prefs_seed_is_deterministic():
    """Same seed -> byte-identical prefs across calls."""
    a = get_default_stealth_prefs(seed=42)
    b = get_default_stealth_prefs(seed=42)
    assert a == b


def test_get_default_stealth_prefs_different_seeds_differ():
    """Different seeds -> different prefs."""
    a = get_default_stealth_prefs(seed=1)
    b = get_default_stealth_prefs(seed=2)
    assert a != b


def test_humanize_false_disables_prefs():
    """humanize=False removes the maxTime knob and flips the toggle to False."""
    prefs = get_default_stealth_prefs(seed=42, humanize=False)
    assert prefs["invisible_playwright.humanize"] is False
    assert "invisible_playwright.humanize.maxTime" not in prefs


def test_humanize_default_sets_max_time_1_5():
    """humanize=True -> default maxTime is 1.5s, stored as string."""
    prefs = get_default_stealth_prefs(seed=42, humanize=True)
    assert prefs["invisible_playwright.humanize"] is True
    assert prefs["invisible_playwright.humanize.maxTime"] == "1.5"


def test_humanize_float_overrides_max_time():
    """Float for humanize is the explicit cap in seconds."""
    prefs = get_default_stealth_prefs(seed=42, humanize=3.0)
    assert prefs["invisible_playwright.humanize"] is True
    assert prefs["invisible_playwright.humanize.maxTime"] == "3.0"


def test_extra_prefs_overlay_takes_precedence():
    """extra_prefs overlay LAST overrides any baseline value."""
    prefs = get_default_stealth_prefs(
        seed=42, extra_prefs={"some.custom.pref": 999}
    )
    assert prefs["some.custom.pref"] == 999


def test_extra_prefs_can_override_baseline():
    """A key in extra_prefs that also exists in baseline gets overridden."""
    baseline = get_default_stealth_prefs(seed=42)
    a_baseline_key = next(iter(baseline.keys()))
    overridden = get_default_stealth_prefs(
        seed=42, extra_prefs={a_baseline_key: "OVERRIDDEN_SENTINEL"}
    )
    assert overridden[a_baseline_key] == "OVERRIDDEN_SENTINEL"


def test_locale_argument_changes_prefs():
    """Different locales produce different prefs (Accept-Language affected)."""
    en = get_default_stealth_prefs(seed=42, locale="en-US")
    it = get_default_stealth_prefs(seed=42, locale="it-IT")
    assert en != it


def test_timezone_argument_changes_prefs():
    """Different timezones produce different prefs."""
    ny = get_default_stealth_prefs(seed=42, timezone="America/New_York")
    rome = get_default_stealth_prefs(seed=42, timezone="Europe/Rome")
    assert ny != rome


def test_pin_argument_forces_specific_fields():
    """Pin forces a specific field while the rest stays seed-derived."""
    plain = get_default_stealth_prefs(seed=42)
    pinned = get_default_stealth_prefs(
        seed=42, pin={"hardware.concurrency": 999}
    )
    # something in the dict must differ vs the plain seed=42 build
    assert plain != pinned


def test_public_import_matches_direct_import():
    """Top-level re-export and direct module import return identical output."""
    a = get_default_stealth_prefs(seed=42)
    b = _direct(seed=42)
    assert a == b


def test_ensure_binary_is_callable_via_public_namespace():
    """ensure_binary is re-exported and stays callable from the package root."""
    # We don't invoke it (would trigger a network download in CI) — just
    # verify the public attribute is the same callable as the underlying.
    from invisible_playwright.download import ensure_binary as _direct_eb
    assert ensure_binary is _direct_eb
