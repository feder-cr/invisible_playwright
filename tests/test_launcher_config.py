"""Launcher helpers that don't require launching the actual browser."""
import pytest

from invisible_playwright.launcher import (
    InvisiblePlaywright,
    _IANA_TO_POSIX_TZ,
    _tz_env,
    _CHROME_W,
    _CHROME_H,
    _TASKBAR_H,
)


def test_tz_env_known_iana_returns_posix():
    assert _tz_env("America/New_York") == "EST5EDT"
    assert _tz_env("America/Chicago") == "CST6CDT"
    assert _tz_env("America/Los_Angeles") == "PST8PDT"


def test_tz_env_arizona_no_dst():
    """America/Phoenix must NOT have a DST suffix - Arizona doesn't observe DST."""
    assert _tz_env("America/Phoenix") == "MST7"


def test_tz_env_hawaii_no_dst():
    assert _tz_env("Pacific/Honolulu") == "HST10"


def test_tz_env_unknown_iana_passes_through():
    """Linux glibc parses IANA names directly via /usr/share/zoneinfo,
    so unknown zones should fall through unchanged."""
    assert _tz_env("Europe/Berlin") == "Europe/Berlin"
    assert _tz_env("Asia/Tokyo") == "Asia/Tokyo"


def test_iana_to_posix_table_well_formed():
    for iana, posix in _IANA_TO_POSIX_TZ.items():
        assert "/" in iana, f"{iana} is not an IANA zone identifier"
        assert "/" not in posix, f"{posix} should be POSIX format, no slashes"
        assert posix[0].isalpha(), f"{posix} should start with a letter"


def test_chrome_offsets_are_positive_ints():
    """These pad the spoofed viewport to fit inside the spoofed screen.
    Any zero/negative value would let viewport bleed past screen bounds."""
    assert _CHROME_W > 0
    assert _CHROME_H > 0
    assert _TASKBAR_H > 0


def test_invisible_playwright_constructs_without_launching():
    """The class should be instantiable for inspection without entering
    the context manager (which would try to download the binary)."""
    obj = InvisiblePlaywright(seed=42)
    assert obj is not None
    obj2 = InvisiblePlaywright(seed=42, headless=True)
    assert obj2 is not None


# ─── profile_dir kwarg - persistent context support ─────────────────────── #

from pathlib import Path


@pytest.mark.unit
def test_profile_dir_none_by_default():
    """No persistent profile unless explicitly opted in. Prevents accidental
    state-leak between scripts that share the same seed."""
    obj = InvisiblePlaywright(seed=42)
    assert obj._profile_dir is None
    assert obj._persistent_context is None


@pytest.mark.unit
def test_profile_dir_string_is_coerced_to_path(tmp_path):
    """Accept str or Path. Always store as Path internally."""
    obj = InvisiblePlaywright(seed=42, profile_dir=str(tmp_path))
    assert isinstance(obj._profile_dir, Path)
    assert obj._profile_dir == tmp_path


@pytest.mark.unit
def test_profile_dir_path_is_stored_as_is(tmp_path):
    obj = InvisiblePlaywright(seed=42, profile_dir=tmp_path)
    assert obj._profile_dir == tmp_path


@pytest.mark.unit
def test_profile_dir_does_not_create_dir_until_enter(tmp_path):
    """Construction must not touch the filesystem. Directory creation only
    happens when the user actually enters the context manager - otherwise
    a typo at instantiation would silently spawn dirs."""
    target = tmp_path / "nonexistent"
    assert not target.exists()
    InvisiblePlaywright(seed=42, profile_dir=target)
    assert not target.exists()


@pytest.mark.unit
def test_persistent_context_kwargs_match_default_exactly():
    """Persistent kwargs must be IDENTICAL to non-persistent default
    kwargs. From firefox-5 (C7 closure) the docShell.overrideTimezone
    method is present in the patched binary, so the per-realm overrides
    Playwright applies for `locale=`/`timezone_id=` land successfully and
    no longer hang the persistent context launch handshake.

    Before firefox-5 we had to filter these out (180s timeout otherwise).
    A future refactor that re-introduces that filter would silently lose
    timezone/locale isolation in persistent sessions - this test is the
    sentinel that catches the regression at the unit level."""
    obj = InvisiblePlaywright(seed=42, locale="en-GB", timezone="Europe/London",
                              profile_dir="/tmp/x")
    persistent = obj._persistent_context_kwargs()
    default = obj._default_context_kwargs()
    assert persistent == default, (
        "persistent_context kwargs must match default_context kwargs since "
        f"firefox-5.\n  persistent: {persistent!r}\n  default:    {default!r}"
    )


@pytest.mark.unit
def test_persistent_context_kwargs_INCLUDES_locale_and_timezone():
    """Sentinel for the C7 closure: firefox-5 ships the C++ overrideTimezone
    IDL method, so locale + timezone_id MUST be passed through to
    launch_persistent_context. If they're not, the wrapper is silently
    dropping per-context isolation - two sessions with different
    `timezone=` would end up sharing whatever TZ the env var set.

    Regression-defense: do NOT re-add the firefox-4-era filter."""
    obj = InvisiblePlaywright(seed=42, locale="en-GB", timezone="Europe/London",
                              profile_dir="/tmp/x")
    kw = obj._persistent_context_kwargs()
    assert kw.get("locale") == "en-GB", (
        f"locale must be in persistent kwargs (firefox-5+ supports it via "
        f"docShell.languageOverride). Got: {kw.get('locale')!r}"
    )
    assert kw.get("timezone_id") == "Europe/London", (
        f"timezone_id must be in persistent kwargs (firefox-5+ supports it "
        f"via docShell.overrideTimezone IDL method, patch.md section 19). "
        f"Got: {kw.get('timezone_id')!r}"
    )


@pytest.mark.unit
def test_persistent_context_kwargs_omits_timezone_when_empty_string():
    """Empty timezone='' is the 'use host TZ' sentinel - must NOT pass
    timezone_id to Playwright in that case (would pin to literal '' and
    break Intl)."""
    obj = InvisiblePlaywright(seed=42, timezone="", profile_dir="/tmp/x")
    kw = obj._persistent_context_kwargs()
    assert "timezone_id" not in kw


# ─── Mocked __enter__ flow - confirms the right Playwright call is made ── #


@pytest.mark.unit
def test_enter_with_profile_dir_calls_launch_persistent_context(tmp_path, monkeypatch):
    """When profile_dir is set, __enter__ must call
    `firefox.launch_persistent_context(user_data_dir=...)` and NOT
    `firefox.launch(...)`. This is the structural test that the persistent
    branch is wired correctly - without it, profile_dir would be silently
    accepted but ignored."""
    from unittest.mock import MagicMock
    # Mock the engine resolver (download + seal check) so we don't hit the network
    monkeypatch.setattr("invisible_playwright.launcher.resolve_executable",
                       lambda binary_path=None: tmp_path / "firefox")

    # Mock sync_playwright().start() → fake playwright with our recording firefox
    fake_ctx = MagicMock(name="persistent_context")
    fake_firefox = MagicMock()
    fake_firefox.launch_persistent_context.return_value = fake_ctx
    fake_playwright = MagicMock()
    fake_playwright.firefox = fake_firefox
    fake_pw = MagicMock()
    fake_pw.start.return_value = fake_playwright

    monkeypatch.setattr("invisible_playwright.launcher.sync_playwright",
                       lambda: fake_pw)

    profile = tmp_path / "myprofile"
    obj = InvisiblePlaywright(seed=42, profile_dir=profile)
    returned = obj.__enter__()

    # The persistent branch was taken
    fake_firefox.launch_persistent_context.assert_called_once()
    fake_firefox.launch.assert_not_called()

    # The user_data_dir was passed verbatim
    call_kwargs = fake_firefox.launch_persistent_context.call_args.kwargs
    assert call_kwargs["user_data_dir"] == str(profile)

    # The directory was created on disk (Playwright fails otherwise)
    assert profile.exists() and profile.is_dir()

    # __enter__ returned the BrowserContext, not a Browser
    assert returned is fake_ctx


@pytest.mark.unit
def test_enter_without_profile_dir_calls_launch_not_persistent(tmp_path, monkeypatch):
    """Default path: profile_dir=None → firefox.launch, not
    launch_persistent_context. Sentinel that the non-persistent flow
    isn't accidentally rerouted."""
    from unittest.mock import MagicMock
    monkeypatch.setattr("invisible_playwright.launcher.resolve_executable",
                       lambda binary_path=None: tmp_path / "firefox")

    fake_browser = MagicMock(name="browser")
    fake_browser.new_context = MagicMock()
    fake_firefox = MagicMock()
    fake_firefox.launch.return_value = fake_browser
    fake_playwright = MagicMock()
    fake_playwright.firefox = fake_firefox
    fake_pw = MagicMock()
    fake_pw.start.return_value = fake_playwright

    monkeypatch.setattr("invisible_playwright.launcher.sync_playwright",
                       lambda: fake_pw)

    obj = InvisiblePlaywright(seed=42)
    returned = obj.__enter__()

    fake_firefox.launch.assert_called_once()
    fake_firefox.launch_persistent_context.assert_not_called()
    assert returned is fake_browser


@pytest.mark.unit
def test_persistent_context_user_data_dir_is_created_if_missing(tmp_path, monkeypatch):
    """First-run scenario: the directory the user names doesn't exist yet.
    __enter__ must mkdir -p it (Playwright won't, and would crash with
    'user_data_dir does not exist')."""
    from unittest.mock import MagicMock
    monkeypatch.setattr("invisible_playwright.launcher.resolve_executable",
                       lambda binary_path=None: tmp_path / "firefox")
    fake_pw = MagicMock()
    fake_pw.start.return_value = MagicMock()
    fake_pw.start.return_value.firefox.launch_persistent_context = MagicMock(
        return_value=MagicMock()
    )
    monkeypatch.setattr("invisible_playwright.launcher.sync_playwright",
                       lambda: fake_pw)

    nested = tmp_path / "a" / "b" / "c" / "profile"
    assert not nested.parent.exists()  # parent doesn't exist either
    obj = InvisiblePlaywright(seed=42, profile_dir=nested)
    obj.__enter__()
    assert nested.is_dir()


@pytest.mark.unit
def test_teardown_closes_persistent_context(tmp_path, monkeypatch):
    """The teardown must close the persistent context. Forgetting this
    leaves Firefox + Playwright running until the parent process exits,
    which on long-running tools (job orchestrators, MCP servers) leaks
    handles indefinitely."""
    from unittest.mock import MagicMock
    monkeypatch.setattr("invisible_playwright.launcher.resolve_executable",
                       lambda binary_path=None: tmp_path / "firefox")
    fake_ctx = MagicMock(name="persistent_context")
    fake_pw = MagicMock()
    fake_pw.start.return_value.firefox.launch_persistent_context.return_value = fake_ctx
    monkeypatch.setattr("invisible_playwright.launcher.sync_playwright",
                       lambda: fake_pw)

    obj = InvisiblePlaywright(seed=42, profile_dir=tmp_path / "p")
    obj.__enter__()
    obj.__exit__(None, None, None)
    fake_ctx.close.assert_called_once()
