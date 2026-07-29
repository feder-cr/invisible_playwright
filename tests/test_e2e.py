"""E2E tests for the launcher lifecycle.

Tests requiring the patched Firefox binary are gated behind the
``firefox_binary`` fixture, which skips the test cleanly when the
binary is not cached locally and cannot be downloaded (e.g. no
network or no release token). The constructor-only tests (seed
handling) do not need a binary and always run.
"""
from __future__ import annotations

import pytest

from invisible_playwright import InvisiblePlaywright


# ────────────────────────────────────────────────────────────────────
# Constructor-only tests (no browser launch required)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_e3_seed_is_accessible():
    """E3: explicit seed is stored on the instance after construction."""
    ip = InvisiblePlaywright(seed=42)
    assert ip.seed == 42


@pytest.mark.e2e
def test_e4_random_seed_when_none():
    """E4: omitting seed → a fresh positive int31 is chosen."""
    ip = InvisiblePlaywright()
    assert isinstance(ip.seed, int)
    assert ip.seed > 0
    assert ip.seed < 2**31


@pytest.mark.e2e
def test_e4b_random_seed_varies_across_instances():
    """E4 extension: two no-seed instances pick different seeds with
    overwhelming probability. ``secrets.randbits(31)`` collisions are
    ~1 in 2 billion, so we accept the negligible flake risk."""
    seeds = {InvisiblePlaywright().seed for _ in range(5)}
    assert len(seeds) > 1


@pytest.mark.e2e
def test_e6_profile_built_eagerly():
    """The constructor materializes the Profile up front so seed-driven
    fields are accessible without launching a browser. Guards against
    a regression where Profile generation is deferred into ``__enter__``
    and an invalid pin therefore raises only at launch time.
    """
    ip = InvisiblePlaywright(seed=42)
    assert ip._profile is not None
    assert ip._profile.seed == 42


@pytest.mark.e2e
def test_e7_invalid_pin_raises_in_constructor():
    """Invalid pin keys fail fast at construction, not at __enter__."""
    with pytest.raises(ValueError):
        InvisiblePlaywright(seed=42, pin={"not_a_real_field": 1})


# ────────────────────────────────────────────────────────────────────
# Lifecycle tests (require Firefox binary)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_e1_sync_context_manager_lifecycle(firefox_binary):
    """E1: ``with InvisiblePlaywright(...) as browser`` yields a real
    Playwright Browser object that exposes ``new_context``."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        assert browser is not None
        assert hasattr(browser, "new_context")
        assert callable(browser.new_context)


@pytest.mark.e2e
def test_e2_create_context_and_page(firefox_binary):
    """E2: a context spawned from the patched browser can create a page."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            assert page is not None
            assert hasattr(page, "goto")
        finally:
            ctx.close()


@pytest.mark.e2e
def test_e5_teardown_does_not_raise(firefox_binary):
    """E5: ``__exit__`` cleans up Playwright + virtual display without raising."""
    ip = InvisiblePlaywright(seed=42, binary_path=firefox_binary)
    browser = ip.__enter__()
    try:
        assert browser is not None
    finally:
        ip.__exit__(None, None, None)
    # second teardown is idempotent
    ip.__exit__(None, None, None)


@pytest.mark.e2e
def test_e8_new_context_defaults_from_profile(firefox_binary):
    """new_context() without kwargs should inherit profile-derived
    viewport/screen. Guards the monkey-patch installed in __enter__."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            vp = page.viewport_size
            assert vp is not None
            assert vp["width"] > 0
            assert vp["height"] > 0
        finally:
            ctx.close()


# ────────────────────────────────────────────────────────────────────
# Linux-specific lifecycle tests (no Firefox binary required).
#
# These exercise the launcher's Linux code paths without spawning real
# Firefox or Xvfb. They monkeypatch ``sys.platform`` and (where needed)
# the ``make_virtual_display`` dispatcher so the tests run on any host
# - including Windows hosts that ship the production CI for this repo.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_e9_linux_build_prefs_omits_windows_sandbox_key(monkeypatch):
    """E9: ``_build_prefs(headless=True)`` on Linux must pass
    ``virtual_display=False`` to the prefs translator. The Win32-only
    ``security.sandbox.gpu.level`` workaround targets the alt-desktop
    GPU sandbox bug and MUST NOT leak into Linux prefs, where Xvfb
    handles window hiding instead."""
    import sys as _sys
    monkeypatch.setattr(_sys, "platform", "linux")
    ip = InvisiblePlaywright(seed=42, headless=True)
    prefs = ip._build_prefs()
    assert "security.sandbox.gpu.level" not in prefs


@pytest.mark.e2e
def test_e10_linux_resolve_headless_invokes_xvfb_dispatcher(monkeypatch):
    """E10: ``_resolve_headless`` with ``headless=True`` on Linux must
    call ``make_virtual_display().start()`` and store the result on
    ``self._virtual_display``. We stub the dispatcher so no real Xvfb
    is spawned - the dispatcher's platform routing is covered separately
    in ``test_headless.py``."""
    import sys as _sys
    monkeypatch.setattr(_sys, "platform", "linux")

    events: list[str] = []

    class _FakeDisplay:
        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    from invisible_playwright import launcher as _l
    monkeypatch.setattr(_l, "make_virtual_display", lambda: _FakeDisplay())

    ip = InvisiblePlaywright(seed=42, headless=True)
    result = ip._resolve_headless()
    assert result is False
    assert events == ["start"]
    assert ip._virtual_display is not None


@pytest.mark.e2e
def test_e11_linux_teardown_stops_virtual_display_and_is_idempotent(monkeypatch):
    """E11: ``_teardown`` stops the Linux virtual display, clears the
    reference, and a second invocation is a no-op. Guards the cleanup
    path used by ``__exit__`` so a failed ``__enter__`` cannot leak Xvfb."""
    import sys as _sys
    monkeypatch.setattr(_sys, "platform", "linux")

    stops: list[bool] = []

    class _FakeDisplay:
        def start(self) -> None:
            pass

        def stop(self) -> None:
            stops.append(True)

    from invisible_playwright import launcher as _l
    monkeypatch.setattr(_l, "make_virtual_display", lambda: _FakeDisplay())

    ip = InvisiblePlaywright(seed=42, headless=True)
    ip._resolve_headless()
    ip._teardown()
    assert stops == [True]
    assert ip._virtual_display is None
    ip._teardown()
    assert stops == [True]


@pytest.mark.e2e
def test_e12_linux_resolve_headless_without_xvfb_raises_clear_error(monkeypatch):
    """E12: On Linux with ``headless=True`` and ``Xvfb`` missing from
    ``PATH``, ``_resolve_headless`` must surface a clear, actionable
    ``RuntimeError`` instead of a cryptic FileNotFoundError. Verifies
    the early-check path in ``_LinuxVirtualDisplay.start``."""
    import sys as _sys
    monkeypatch.setattr(_sys, "platform", "linux")
    from invisible_core import _headless as _h
    monkeypatch.setattr(_h, "_binary_on_path", lambda name: False)

    ip = InvisiblePlaywright(seed=42, headless=True)
    with pytest.raises(RuntimeError, match="Xvfb"):
        ip._resolve_headless()
    assert ip._virtual_display is None
