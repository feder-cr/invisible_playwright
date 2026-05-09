"""Async Playwright façade — mirrors sync_api but with async/await."""
from __future__ import annotations

import asyncio
import secrets
from typing import Any, Dict, Optional, Union

from playwright.async_api import Browser, Playwright, async_playwright

from ._fpforge import Profile, generate_profile
from ._headless import make_virtual_display
from ._proxy import configure_proxy as _configure_proxy_shared
from .download import ensure_binary
from .launcher import _CHROME_H, _CHROME_W, _TASKBAR_H, _tz_env
from .prefs import translate_profile_to_prefs


def _patch_new_page_sleep(ctx: Any) -> None:
    """Wrap ctx.new_page() to add a brief settle after tab creation.

    FF150 with Fission emits an about:newtab navigation ~100ms after a tab
    is created.  If goto() is called immediately, it races with that internal
    navigation and raises "Navigation interrupted by about:newtab".  A short
    sleep breaks the race without requiring every call-site to know about it.
    """
    original_new_page = ctx.new_page

    async def patched_new_page(**kw):
        page = await original_new_page(**kw)
        await asyncio.sleep(0.4)
        return page

    ctx.new_page = patched_new_page  # type: ignore[assignment]


class Stealthfox:
    """Async context manager — see stealthfox.Stealthfox for the sync variant."""

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
        # See sync launcher: `zoom.stealth.fpp.hw_seed` is int32_t — clamp.
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

    async def __aenter__(self) -> Browser:
        import sys as _sys
        executable = self._binary_path or ensure_binary()
        prefs = translate_profile_to_prefs(
            self._profile,
            locale=self._locale,
            timezone=self._timezone,
            extra_prefs=self._extra_prefs,
            virtual_display=bool(self._headless and _sys.platform == "win32"),
        )
        prefs["stealthfox.humanize"] = bool(self._humanize)
        if self._humanize:
            cap = 1.5 if self._humanize is True else float(self._humanize)
            prefs["stealthfox.humanize.maxTime"] = str(cap)
        playwright_proxy = _configure_proxy_shared(self._proxy, prefs)
        pw_headless = self._resolve_headless()
        env = self._build_env()
        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.firefox.launch(
                executable_path=str(executable),
                headless=pw_headless,
                firefox_user_prefs=prefs,
                proxy=playwright_proxy,
                args=self._extra_args,
                env=env,
            )
        except BaseException:
            await self._teardown()
            raise
        self._patch_new_context_defaults(self._browser)
        return self._browser

    def _patch_new_context_defaults(self, browser: Browser) -> None:
        original = browser.new_context
        defaults = self._default_context_kwargs()

        async def patched(**kw):
            merged = dict(defaults)
            merged.update(kw)
            ctx = await original(**merged)
            _patch_new_page_sleep(ctx)
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
        # Pass timezone via Playwright per-realm override (works for every
        # IANA name, including no-DST zones that Windows ICU silently drops
        # on the global pref path).
        if self._timezone:
            kwargs["timezone_id"] = self._timezone
        if self._locale:
            kwargs["locale"] = self._locale
        return kwargs

    async def __aexit__(self, *exc: Any) -> None:
        await self._teardown()

    async def _teardown(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
        if self._virtual_display is not None:
            try:
                self._virtual_display.stop()
            except Exception:
                pass
            self._virtual_display = None

    def _build_env(self) -> Dict[str, str]:
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
        if not self._headless:
            return False
        vd = make_virtual_display()
        vd.start()
        self._virtual_display = vd
        return False


__all__ = ["Stealthfox"]
