"""Sync Playwright launcher for stealthfox."""
from __future__ import annotations

import secrets
from typing import Any, Dict, Optional, Union

from playwright.sync_api import Browser, Playwright, sync_playwright

from ._fpforge import Profile, generate_profile
from ._headless import make_virtual_display
from ._proxy import configure_proxy as _configure_proxy_shared
from .download import ensure_binary
from .prefs import translate_profile_to_prefs


def _patch_sync_new_page_sleep(ctx: Any) -> None:
    """Wrap ctx.new_page() to add a brief settle after tab creation.

    FF150 with Fission emits an about:newtab navigation ~100ms after a tab
    is created.  If goto() is called immediately, it races with that internal
    navigation and raises "Navigation interrupted by about:newtab".  A short
    sleep breaks the race without requiring every call-site to know about it.
    """
    import time as _time
    original_new_page = ctx.new_page

    def patched_new_page(**kw):
        page = original_new_page(**kw)
        _time.sleep(0.4)
        return page

    ctx.new_page = patched_new_page  # type: ignore[assignment]


# Window-chrome and taskbar offsets measured empirically on a headed
# Firefox 150 (no compositor). Used to derive the default new_context
# viewport so it fits inside the spoofed screen without out-of-bounds.
_CHROME_W  = 14
_CHROME_H  = 91
_TASKBAR_H = 40

# IANA → POSIX TZ mapping. Linux glibc accepts IANA names directly via
# /usr/share/zoneinfo, but Windows MSVCRT only understands the POSIX form
# ("EST5EDT") — convert here so ``TZ`` works on both platforms when the
# binary runs on Windows. Common US zones cover the vast majority of
# residential proxies; everything else falls through to its IANA name.
_IANA_TO_POSIX_TZ = {
    "America/New_York":            "EST5EDT",
    "America/Detroit":              "EST5EDT",
    "America/Indiana/Indianapolis": "EST5EDT",
    "America/Kentucky/Louisville":  "EST5EDT",
    "America/Chicago":              "CST6CDT",
    "America/Denver":               "MST7MDT",
    "America/Los_Angeles":          "PST8PDT",
    # Arizona (except Navajo Nation) does NOT observe DST. Mapping it to
    # MST7MDT made libc apply DST → Date.getTimezoneOffset() returned -360
    # in summer (Denver-like) instead of -420 (true Phoenix), and FP Pro
    # deduced vpn_origin_timezone="America/Denver" → timezone_mismatch.
    "America/Phoenix":              "MST7",
    "America/Anchorage":            "AKST9AKDT",
    # Hawaii does not observe DST.
    "Pacific/Honolulu":             "HST10",
}


def _tz_env(timezone: str) -> str:
    """Return the value to set in ``TZ`` for the given IANA zone."""
    return _IANA_TO_POSIX_TZ.get(timezone, timezone)


class Stealthfox:
    """Context manager launching a patched Firefox with a deterministic profile.

    Usage:

        from stealthfox import Stealthfox

        # random seed (different fingerprint each call)
        with Stealthfox() as browser:
            page = browser.new_page()
            page.goto("https://example.com")

        # explicit seed → same profile every time
        with Stealthfox(seed=42) as browser:
            ...

        # human-like cursor motion (Bezier trajectory on every mousemove)
        with Stealthfox(humanize=True) as browser:
            ...

    Optional ``pin`` forces specific fingerprint fields while the rest still
    varies with ``seed``::

        with Stealthfox(seed=42, pin={"screen.width": 2560}) as browser:
            ...

    After construction, the chosen seed is available as ``self.seed`` — useful
    to reproduce a random run later.
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        *,
        pin: Optional[Dict[str, Any]] = None,
        headless: bool = False,
        proxy: Optional[Dict[str, str]] = None,
        extra_args: Optional[list[str]] = None,
        humanize: Union[bool, float] = True,
        locale: str = "en-US",
        timezone: str = "",
        extra_prefs: Optional[Dict[str, Any]] = None,
        binary_path: Optional[str] = None,
    ) -> None:
        """
        Args:
            seed: Integer seed driving the Bayesian fingerprint sampler.
                Same seed → same fingerprint. ``None`` = fresh random.
            pin: Force specific fingerprint fields (see docs/pinning.md).
            headless: When ``True``, browser renders on a hidden virtual
                display (Xvfb on Linux, ``CreateDesktop`` on Windows) so
                Firefox stays in *headed* mode (real rendering pipeline,
                coherent fingerprint) without showing windows.
            proxy: ``{"server": "...", "username": "...", "password": "..."}``.
                ``socks5://`` / ``socks4://`` go through the patched
                ``nsProtocolProxyService``; ``http(s)://`` go through
                Playwright's own ``proxy=`` kwarg.
            extra_args: Extra command-line args forwarded to Firefox.
            humanize: Every mouse move is expanded by the patched Juggler
                into a Bezier trajectory with ~10 ms between waypoints.
                Default ``True`` (~1.5 s max motion). ``False`` disables;
                a float caps the motion in seconds.
            locale: BCP-47 tag (e.g. ``"en-US"``). Drives the
                ``Accept-Language`` header and ``navigator.language``.
            timezone: IANA timezone (e.g. ``"America/New_York"``). Empty
                means use the host TZ.
            extra_prefs: Optional dict of Firefox prefs overlayed on top
                of the generated profile — useful for niche tweaks
                without monkey-patching the package.
        """
        # Constrain to int31 — Firefox's `zoom.stealth.fpp.hw_seed` and
        # related stealth prefs are declared as ``int32_t`` in
        # ``StaticPrefList.yaml``. A 32-bit seed risks the high bit being
        # interpreted as negative on the C++ side, where the noise hooks
        # bail out on ``seed <= 0`` — which produces bit-identical audio
        # / canvas fingerprints across half the sessions.
        self.seed: int = int(seed) if seed is not None else secrets.randbits(31)
        self._pin = pin
        self._headless = headless
        self._proxy = proxy
        self._extra_args = list(extra_args or [])
        self._humanize = humanize
        self._locale = locale
        self._timezone = timezone
        self._extra_prefs = extra_prefs
        self._binary_path = binary_path
        self._profile: Profile = generate_profile(self.seed, pin=self._pin)
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._virtual_display: Any = None

    def __enter__(self) -> Browser:
        executable = self._binary_path or ensure_binary()
        prefs = self._build_prefs()
        playwright_proxy = _configure_proxy_shared(self._proxy, prefs)
        pw_headless = self._resolve_headless()
        env = self._build_env()

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.firefox.launch(
                executable_path=str(executable),
                headless=pw_headless,
                firefox_user_prefs=prefs,
                proxy=playwright_proxy,
                args=self._extra_args,
                env=env,
            )
        except BaseException:
            # Python doesn't call __exit__ when __enter__ raises — clean up
            # the virtual display + Playwright manually so we don't leak Xvfb
            # / desktop handles into the user's process.
            self._teardown()
            raise
        self._patch_new_context_defaults(self._browser)
        return self._browser

    def _patch_new_context_defaults(self, browser: Browser) -> None:
        """Wrap ``browser.new_context`` so its defaults derive from the
        profile (viewport, screen, DPR, color-scheme). Users get a
        coherent context for free; explicit kwargs still override.
        """
        original = browser.new_context
        defaults = self._default_context_kwargs()

        def patched(**kw):
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins
            ctx = original(**merged)
            _patch_sync_new_page_sleep(ctx)
            return ctx

        browser.new_context = patched  # type: ignore[assignment]

    def _default_context_kwargs(self) -> Dict[str, Any]:
        p = self._profile
        kwargs: Dict[str, Any] = {
            "viewport":            {"width":  p.screen.width  - _CHROME_W,
                                     "height": p.screen.height - _TASKBAR_H - _CHROME_H},
            "screen":              {"width": p.screen.width, "height": p.screen.height},
            "device_scale_factor": p.screen.dpr,
            "color_scheme":        "dark" if p.dark_theme else "light",
        }
        # Pass timezone via Playwright's per-realm override (docShell.overrideTimezone
        # → JS::SetRealmTimezoneOverride). The juggler.timezone.override pref path
        # uses JS::SetTimeZoneOverride globally, which is broken on Windows ICU for
        # no-DST IANA names (America/Phoenix, Pacific/Honolulu, ...) — those silently
        # fall back to the host system TZ. The per-realm path works for every zone.
        if self._timezone:
            kwargs["timezone_id"] = self._timezone
        if self._locale:
            kwargs["locale"] = self._locale
        return kwargs

    def __exit__(self, *exc: Any) -> None:
        self._teardown()

    def _teardown(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
        if self._virtual_display is not None:
            try:
                self._virtual_display.stop()
            except Exception:
                pass
            self._virtual_display = None

    # ── helpers ─────────────────────────────────────────────────────────

    def _build_prefs(self) -> Dict[str, Any]:
        """Fingerprint prefs plus humanize toggle (always set explicitly)."""
        import sys as _sys
        prefs = translate_profile_to_prefs(
            self._profile,
            locale=self._locale,
            timezone=self._timezone,
            extra_prefs=self._extra_prefs,
            virtual_display=bool(self._headless and _sys.platform == "win32"),
        )
        prefs["stealthfox.humanize"] = bool(self._humanize)
        if self._humanize:
            prefs["stealthfox.humanize.maxTime"] = str(self._humanize_max_seconds())
        return prefs

    def _build_env(self) -> Dict[str, str]:
        """Env vars passed to the Firefox subprocess.

        ``TZ`` tunes the libc clock the content process reads for
        ``Date`` / ``Intl.DateTimeFormat`` so the JS-visible timezone
        matches ``self._timezone`` regardless of the host TZ.
        ``STEALTHFOX_WEBRTC_PUBLIC_IP`` is propagated when the calling
        process has set it — read by nICEr's nr_stealth_bridge to inject
        a synthetic srflx candidate matching the proxy egress IP, avoiding
        the StaticPref IPC propagation timing issue between parent and
        socket processes.
        """
        import os as _os
        env = _os.environ.copy()
        if self._timezone:
            env["TZ"] = _tz_env(self._timezone)
        # Propagate STEALTHFOX_WEBRTC_PUBLIC_IP if the process set it — read
        # by nICEr's nr_stealth_bridge to inject a synthetic srflx candidate
        # matching the proxy egress IP. This avoids the StaticPref IPC
        # propagation timing issue between parent and socket processes.
        if _os.environ.get("STEALTHFOX_WEBRTC_PUBLIC_IP"):
            env["STEALTHFOX_WEBRTC_PUBLIC_IP"] = _os.environ["STEALTHFOX_WEBRTC_PUBLIC_IP"]
        return env

    def _resolve_headless(self) -> bool:
        """Translate the user's ``headless`` flag.

        When ``True``, we keep Firefox in headed mode (real rendering
        pipeline → coherent fingerprint) and hide the windows on a fresh
        Xvfb (Linux) or hidden Windows desktop.
        """
        if not self._headless:
            return False
        vd = make_virtual_display()
        vd.start()
        self._virtual_display = vd
        return False

    def _humanize_max_seconds(self) -> float:
        if self._humanize is True:
            return 1.5
        return float(self._humanize)

