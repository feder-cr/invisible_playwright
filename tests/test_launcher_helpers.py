"""Unit tests for pure helpers in ``launcher.py``.

These cover code paths that are not exercised by the E2E launcher tests
(`test_e2e.py`) because they live in private helpers below the Playwright
boundary. The tests instantiate ``InvisiblePlaywright`` for the methods
that read ``self._profile`` but never enter ``__enter__``, so no Firefox
binary or virtual display is required.
"""
from __future__ import annotations

import pytest

from invisible_playwright import InvisiblePlaywright, _cursor
from invisible_playwright.launcher import (
    _CHROME_H,
    _CHROME_W,
    _IANA_TO_POSIX_TZ,
    _TASKBAR_H,
    _tz_env,
)


# ── _tz_env (IANA → POSIX) ────────────────────────────────────────────


@pytest.mark.unit
def test_tz_env_eastern_us_maps_to_posix_with_dst():
    """Eastern US zones share the same POSIX form; spot-check a few."""
    assert _tz_env("America/New_York") == "EST5EDT"
    assert _tz_env("America/Detroit") == "EST5EDT"
    assert _tz_env("America/Indiana/Indianapolis") == "EST5EDT"


@pytest.mark.unit
def test_tz_env_central_mountain_pacific_map_to_posix_with_dst():
    assert _tz_env("America/Chicago") == "CST6CDT"
    assert _tz_env("America/Denver") == "MST7MDT"
    assert _tz_env("America/Los_Angeles") == "PST8PDT"


@pytest.mark.unit
def test_tz_env_phoenix_strips_dst():
    """Arizona (outside Navajo Nation) does NOT observe DST. The POSIX
    form must be ``MST7`` (no second segment) - using ``MST7MDT`` caused
    FP Pro to deduce vpn_origin_timezone=America/Denver from a 60-minute
    offset error in summer. Guard against regression of that mapping.
    """
    assert _tz_env("America/Phoenix") == "MST7"


@pytest.mark.unit
def test_tz_env_honolulu_strips_dst():
    """Hawaii does not observe DST. POSIX form ``HST10`` (no DST segment)."""
    assert _tz_env("Pacific/Honolulu") == "HST10"


@pytest.mark.unit
def test_tz_env_passthrough_for_unmapped_zone():
    """Zones outside the lookup table fall through to their IANA name -
    glibc on Linux reads /usr/share/zoneinfo directly. Windows MSVCRT
    won't understand them but that's accepted; the mapping covers the
    common residential-proxy zones."""
    assert _tz_env("Europe/Berlin") == "Europe/Berlin"
    assert _tz_env("Asia/Tokyo") == "Asia/Tokyo"


@pytest.mark.unit
def test_tz_env_empty_string_passes_through():
    """Empty string is never set as ``TZ`` by the caller, but the helper
    is still defensive - return it unchanged rather than raising."""
    assert _tz_env("") == ""


@pytest.mark.unit
def test_iana_to_posix_phoenix_and_honolulu_present():
    """Sanity-check the no-DST entries are still in the mapping; deleting
    them would silently revert the Phoenix DST bug."""
    assert _IANA_TO_POSIX_TZ["America/Phoenix"] == "MST7"
    assert _IANA_TO_POSIX_TZ["Pacific/Honolulu"] == "HST10"


# ── InvisiblePlaywright._humanize_max_seconds ─────────────────────────


# These pinned `InvisiblePlaywright._humanize_max_seconds()` until 2026-07-27.
# That method had had no caller in `src/` since the cursor engine moved into
# this package - `_arm_cursor_engine` calls `_cursor.max_seconds_for` directly -
# so four tests were holding a dead one-line wrapper in place while the live
# path was uncovered, and would have stayed green whatever the real contract
# did. The method is deleted; these assert the function itself, and the fifth
# test below is the one that ties it to the path that runs.


@pytest.mark.unit
def test_humanize_true_defaults_to_one_and_a_half_seconds():
    assert _cursor.max_seconds_for(True) == 1.5


@pytest.mark.unit
def test_humanize_float_passes_through_as_seconds():
    assert _cursor.max_seconds_for(2.5) == 2.5


@pytest.mark.unit
def test_humanize_int_coerced_to_float():
    """``humanize=3`` is valid (truthy, not ``True``) → float coercion."""
    out = _cursor.max_seconds_for(3)
    assert out == 3.0
    assert isinstance(out, float)


@pytest.mark.unit
def test_humanize_small_float_passes_through():
    """Below the default cap - the user's value wins."""
    assert _cursor.max_seconds_for(0.4) == 0.4


@pytest.mark.unit
def test_the_cap_reaches_the_live_cursor_arming_path():
    """The claim the four above cannot make alone: that this value is what the
    session actually arms the generator with. Without it they are back to
    pinning a function nothing calls."""
    import inspect

    from invisible_playwright import launcher

    src = inspect.getsource(launcher.InvisiblePlaywright._arm_cursor_engine)
    assert "_cursor_max_seconds(self._humanize)" in src, (
        "the arming path no longer passes the cap through max_seconds_for")


# ── InvisiblePlaywright._default_context_kwargs ───────────────────────


@pytest.mark.unit
def test_default_context_viewport_subtracts_window_chrome():
    """Viewport must fit inside the spoofed screen with the headed
    window chrome subtracted. Otherwise Playwright complains about the
    viewport being larger than the screen."""
    ip = InvisiblePlaywright(seed=42)
    kw = ip._default_context_kwargs()
    p = ip._profile
    assert kw["viewport"]["width"] == p.screen.width - _CHROME_W
    assert kw["viewport"]["height"] == (p.screen.height - p.screen.taskbar_px
                                        - _CHROME_H)
    # and the profile is the ONE source: the wrapper carried its own 40 while
    # the core declared 48 and the engine floor was 48, so the viewport and
    # screen.availHeight disagreed about the same taskbar. Asserting against
    # _TASKBAR_H alone could not see that - it passed with either number.
    assert p.screen.taskbar_px == _TASKBAR_H


@pytest.mark.unit
def test_default_context_viewport_follows_a_pinned_taskbar():
    """A pinned taskbar has to move the viewport, or the wrapper is still
    deriving it from a constant of its own."""
    ip = InvisiblePlaywright(seed=42, pin={"screen.taskbar_px": 72})
    kw = ip._default_context_kwargs()
    p = ip._profile
    assert p.screen.taskbar_px == 72
    assert kw["viewport"]["height"] == p.screen.height - 72 - _CHROME_H


@pytest.mark.unit
def test_default_context_screen_matches_profile():
    ip = InvisiblePlaywright(seed=42)
    kw = ip._default_context_kwargs()
    p = ip._profile
    assert kw["screen"] == {"width": p.screen.width, "height": p.screen.height}
    assert kw["device_scale_factor"] == p.screen.dpr


@pytest.mark.unit
def test_default_context_color_scheme_follows_dark_theme():
    """``color_scheme`` must match ``profile.dark_theme`` so the Playwright
    realm tells matchMedia the same thing the prefs tell the chrome."""
    ip_dark = InvisiblePlaywright(seed=42, pin={"dark_theme": True})
    ip_light = InvisiblePlaywright(seed=42, pin={"dark_theme": False})
    assert ip_dark._default_context_kwargs()["color_scheme"] == "dark"
    assert ip_light._default_context_kwargs()["color_scheme"] == "light"


@pytest.mark.unit
def test_default_context_includes_timezone_when_set():
    ip = InvisiblePlaywright(seed=42, timezone="America/New_York")
    assert ip._default_context_kwargs()["timezone_id"] == "America/New_York"


@pytest.mark.unit
def test_default_context_omits_timezone_when_empty():
    """Default ``timezone=""`` means "let the host TZ leak through" -
    Playwright must not receive ``timezone_id`` at all in that case,
    otherwise it overrides to the literal empty string."""
    ip = InvisiblePlaywright(seed=42)
    assert "timezone_id" not in ip._default_context_kwargs()


@pytest.mark.unit
def test_default_context_includes_locale_when_set():
    ip = InvisiblePlaywright(seed=42, locale="de-DE")
    assert ip._default_context_kwargs()["locale"] == "de-DE"


@pytest.mark.unit
def test_default_context_omits_locale_when_empty():
    ip = InvisiblePlaywright(seed=42, locale="")
    assert "locale" not in ip._default_context_kwargs()


# ── InvisiblePlaywright._build_env - WebRTC egress auto-derive ─────────
# Locks the 2026-06-10 fix: behind a proxy the launcher feeds the discovered
# egress IP to nICEr (srflx override) + drops IPv6. Without it, a proxied
# session's WebRTC silently fell back to leaking/blocking. Runs in tests.yml.


@pytest.mark.unit
def test_build_env_injects_webrtc_egress_when_discovered():
    ip = InvisiblePlaywright(seed=42)
    ip._webrtc_egress_ip = "203.0.113.9"  # what __enter__ resolves behind a proxy
    env = ip._build_env({})
    assert env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "203.0.113.9"
    assert env["STEALTHFOX_WEBRTC_DISABLE_IPV6"] == "1"


@pytest.mark.unit
def test_build_env_no_webrtc_keys_without_proxy(monkeypatch):
    monkeypatch.delenv("STEALTHFOX_WEBRTC_PUBLIC_IP", raising=False)
    ip = InvisiblePlaywright(seed=42)
    ip._webrtc_egress_ip = None  # no proxy → real STUN already truthful
    env = ip._build_env({})
    assert "STEALTHFOX_WEBRTC_PUBLIC_IP" not in env
    assert "STEALTHFOX_WEBRTC_DISABLE_IPV6" not in env


@pytest.mark.unit
def test_build_env_caller_env_override_wins(monkeypatch):
    monkeypatch.setenv("STEALTHFOX_WEBRTC_PUBLIC_IP", "198.51.100.5")
    ip = InvisiblePlaywright(seed=42)
    ip._webrtc_egress_ip = "203.0.113.9"  # auto-discovered
    env = ip._build_env({})
    assert env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "198.51.100.5"  # caller wins
    assert env["STEALTHFOX_WEBRTC_DISABLE_IPV6"] == "1"


@pytest.mark.unit
def test_build_env_never_injects_font_env():
    # The patched binary is self-contained for fonts (always bundle-only; the
    # exposed set IS the bundle, system-ui + generics baked in C++). The wrapper
    # must NOT inject any STEALTHFOX_FONTLIST/SYSTEMUI env - even if legacy font
    # prefs are passed - so there is no external font customization channel.
    ip = InvisiblePlaywright(seed=42)
    env = ip._build_env({
        "zoom.stealth.font.fontlist": "arial,calibri,segoe ui",
        "zoom.stealth.font.system_ui": "Segoe UI",
    })
    assert "STEALTHFOX_FONTLIST" not in env
    assert "STEALTHFOX_SYSTEMUI" not in env


@pytest.mark.unit
def test_build_env_no_font_keys_when_absent():
    ip = InvisiblePlaywright(seed=42)
    env = ip._build_env({})
    assert "STEALTHFOX_FONTLIST" not in env
    assert "STEALTHFOX_SYSTEMUI" not in env
