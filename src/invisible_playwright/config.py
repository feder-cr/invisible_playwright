"""Public helpers for building Firefox launch config without using ``InvisiblePlaywright``.

Use these when you need to call ``playwright.firefox.launch()`` (or
``firefox.launch_persistent_context()``) directly with our patched binary
and stealth prefs, instead of using the ``InvisiblePlaywright`` context
manager.

Typical caller is an external integration that owns its own browser
lifecycle (a Crawlee/Skyvern/changedetection-style fetcher, a Playwright
Server wrapper, a multi-language harness) and just wants the building
blocks::

    from playwright.async_api import async_playwright
    from invisible_playwright import ensure_binary, get_default_stealth_prefs

    async with async_playwright() as p:
        browser = await p.firefox.launch(
            executable_path=str(ensure_binary()),
            firefox_user_prefs=get_default_stealth_prefs(seed=42),
        )

For everyday Python usage the ``InvisiblePlaywright`` context manager is
still the recommended entry point; these helpers expose the same internals
without the lifecycle ownership.

.. note::
   When calling ``firefox.launch()`` yourself, pass ``headless=False`` and
   manage the display hiding (Xvfb on Linux, hidden desktop on Windows)
   externally. Passing ``headless=True`` directly to Playwright puts
   Firefox in true headless mode, which skips the real rendering pipeline
   and breaks canvas / audio / WebGL fingerprint coherence. The
   ``InvisiblePlaywright`` context manager does this translation
   automatically; the public helpers leave it to the caller.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional, Union

from ._fpforge import generate_profile
from .prefs import translate_profile_to_prefs


def get_default_stealth_prefs(
    seed: Optional[int] = None,
    *,
    pin: Optional[Dict[str, Any]] = None,
    locale: str = "en-US",
    timezone: str = "",
    extra_prefs: Optional[Dict[str, Any]] = None,
    humanize: Union[bool, float] = True,
    virtual_display: bool = False,
) -> Dict[str, Any]:
    """Build a complete ``firefox_user_prefs`` dict for ``firefox.launch()``.

    Same prefs that ``InvisiblePlaywright(seed=..., locale=..., timezone=...,
    extra_prefs=..., humanize=...)`` would inject. Use this when you need to
    drive ``playwright.firefox.launch()`` yourself.

    Args:
        seed: Integer seed for the Bayesian fingerprint sampler. Same seed
            produces the same fingerprint. ``None`` generates a fresh
            random int31 (matches ``InvisiblePlaywright`` default).
        pin: Optional dict forcing specific fingerprint fields while the
            rest stays seed-derived. See ``docs/pinning.md``.
        locale: BCP-47 tag (e.g. ``"en-US"``). Drives ``Accept-Language``
            and ``navigator.language``.
        timezone: IANA timezone (e.g. ``"America/New_York"``). Empty means
            use the host TZ.
        extra_prefs: Optional dict overlaid LAST onto the generated prefs.
        humanize: When True (default), every mouse move is expanded into
            a Bezier trajectory by the patched Juggler. A float caps the
            motion in seconds. False disables the behavior.
        virtual_display: When True on Windows, apply GPU-disabling prefs
            to prevent GPU process crashes on virtual desktops without
            D3D11 backend.

    Returns:
        Dict ready to pass as ``firefox_user_prefs=`` to
        ``playwright.firefox.launch()`` or ``launch_persistent_context()``.
    """
    resolved_seed = int(seed) if seed is not None else secrets.randbits(31)
    profile = generate_profile(resolved_seed, pin=pin)
    prefs = translate_profile_to_prefs(
        profile,
        locale=locale,
        timezone=timezone,
        extra_prefs=extra_prefs,
        virtual_display=virtual_display,
    )
    prefs["invisible_playwright.humanize"] = bool(humanize)
    if humanize:
        max_seconds = float(humanize) if not isinstance(humanize, bool) else 1.5
        prefs["invisible_playwright.humanize.maxTime"] = str(max_seconds)
    return prefs


def get_default_args() -> List[str]:
    """Return the default Firefox CLI args to pass via ``args=``.

    Currently empty list, since all our stealth configuration is delivered
    via ``firefox_user_prefs`` rather than CLI flags. Exposed for parity
    with the ``cloakbrowser.config.get_default_stealth_args`` pattern and
    to future-proof integrations that already wire ``args=[*existing,
    *get_default_args()]``.
    """
    return []
