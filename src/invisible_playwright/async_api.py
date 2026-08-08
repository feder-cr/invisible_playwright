"""Async Playwright façade - mirrors sync_api but with async/await."""
from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any, Dict, Optional, Union

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from .launcher import _NEWTAB_SETTLE
from . import _session
from ._cursor import (
    ENGINE_PYTHON,
    enable_for as _enable_cursor_engine,
    max_seconds_for as _cursor_max_seconds,
    resolve_cursor_engine,
)
from invisible_core._fpforge import Profile, generate_profile
from invisible_core import forced_gpu_class
from invisible_core import prepare_session_geo
from invisible_core import make_virtual_display
from ._engine import assert_wire_version, resolve_executable
from invisible_core import configure_proxy as _configure_proxy_shared
from ._reaper import SessionToken, guard_for
from .launcher import _CHROME_H, _CHROME_W, _TASKBAR_H


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
        await asyncio.sleep(_NEWTAB_SETTLE)
        return page

    ctx.new_page = patched_new_page  # type: ignore[assignment]


class InvisiblePlaywright:
    """Async context manager - see invisible_playwright.InvisiblePlaywright for the sync variant."""

    def __init__(
        self,
        seed: Optional[int] = None,
        *,
        pin: Optional[Dict[str, Any]] = None,
        headless: bool = False,
        proxy: Optional[Dict[str, str]] = None,
        extra_args: Optional[list[str]] = None,
        humanize: Union[bool, float] = True,
        locale: str = "auto",
        timezone: str = "",
        extra_prefs: Optional[Dict[str, Any]] = None,
        binary_path: Optional[str] = None,
        profile_dir: Optional[Union[str, Path]] = None,
        prep_recaptcha: bool = False,
    ) -> None:
        # See sync launcher: `zoom.stealth.fpp.hw_seed` is int32_t - clamp.
        self.seed: int = int(seed) if seed is not None else secrets.randbits(31)
        self._pin = pin
        self._headless = headless
        self._proxy = proxy
        self._extra_args = list(extra_args or [])
        self._humanize = humanize
        # See the sync launcher: who draws the cursor path (this package by
        # default, the browser under INVPW_CURSOR_ENGINE=binary, nobody when
        # humanize is falsy). Decided here because the prefs depend on it.
        self._cursor_engine = resolve_cursor_engine(humanize)
        self._locale = locale
        self._timezone = timezone
        self._extra_prefs = extra_prefs
        self._binary_path = binary_path
        self._profile_dir: Optional[Path] = Path(profile_dir) if profile_dir else None
        # reCAPTCHA pre-seed gated server-side; respect persistent profile.
        self._prep_recaptcha = bool(prep_recaptcha) and self._profile_dir is None
        self._profile: Profile = generate_profile(
            self.seed, pin=self._pin, fixed_gpu_class=forced_gpu_class(self.seed)
        )
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._persistent_context: Optional[BrowserContext] = None
        self._virtual_display: Any = None
        # Identity for this session's browser tree, and the guard that ties it
        # to this process's lifetime. Declared here rather than in __aenter__ so
        # _teardown - which runs on the failure path too - always finds them.
        #
        # THIS WAS MISSING ENTIRELY until 2026-07-26. The Windows process-leak
        # fix shipped in 0.4.0 and was described as fixed; it went to the sync
        # launcher only. Every async user kept the whole leak - a killed runner
        # left eight to twelve browsers behind - while the release notes said
        # otherwise. Nothing was red because no test enters this context manager.
        self._session_token = SessionToken()
        self._lifetime_guard = guard_for()
        # Proxy egress IP (WebRTC srflx override); discovered in __aenter__.
        self._webrtc_egress_ip: Optional[str] = None

    async def __aenter__(self) -> Union[Browser, BrowserContext]:
        # Resolve timezone="auto" AND discover the proxy egress IP in one
        # round-trip, off the event loop, before anything reads self._timezone
        # or builds prefs/env. Fail-early if a proxy is set but the egress
        # can't be resolved.
        _geo = await asyncio.to_thread(
            prepare_session_geo, self._timezone, self._proxy
        )
        self._timezone = _geo.timezone
        self._webrtc_egress_ip = _geo.egress_ip
        # Geo-aware locale: "auto" derives the language from the egress country (reusing
        # the egress IP just discovered), like timezone="auto". Keeps the browser language
        # consistent with the proxy's country instead of a fixed en-US.
        if (self._locale or "").strip().lower() == "auto":
            from invisible_core import resolve_session_locale
            self._locale = await asyncio.to_thread(
                resolve_session_locale, _geo.egress_ip, self._proxy
            )
        # binary_path= never reaches ensure_binary(), so the engine check lives
        # on the resolved executable rather than inside the fetcher.
        executable = resolve_executable(self._binary_path)
        prefs = self._build_prefs()
        playwright_proxy = _configure_proxy_shared(self._proxy, prefs)
        pw_headless = self._resolve_headless()
        self._session_token = SessionToken.mint()
        env = self._build_env(prefs)
        try:
            self._pw = await async_playwright().start()
            if self._profile_dir is not None:
                # See sync launcher for the persistent-context rationale.
                self._profile_dir.mkdir(parents=True, exist_ok=True)
                # firefox-5 ships the C++ overrideTimezone IDL method (C7
                # chiusura), so locale + timezone_id now propagate cleanly
                # to the persistent context without hanging the launch.
                self._persistent_context = await self._pw.firefox.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    executable_path=str(executable),
                    headless=pw_headless,
                    firefox_user_prefs=prefs,
                    proxy=playwright_proxy,
                    args=self._extra_args,
                    env=env,
                    **self._default_context_kwargs(),
                )
                _patch_new_page_sleep(self._persistent_context)
                self._bind_process_tree()
                self._arm_cursor_engine(self._persistent_context)
                return self._persistent_context
            self._browser = await self._pw.firefox.launch(
                executable_path=str(executable),
                headless=pw_headless,
                firefox_user_prefs=prefs,
                proxy=playwright_proxy,
                args=self._extra_args,
                env=env,
            )
            # See the sync launcher: browser.version comes from the connection
            # initializer, costs no round trip, and cannot be spoofed by a pref.
            assert_wire_version(self._browser)
            self._bind_process_tree()
        except BaseException:
            await self._teardown()
            raise
        self._patch_new_context_defaults(self._browser)
        self._arm_cursor_engine(self._browser)
        return self._browser

    def _bind_process_tree(self) -> None:
        """Tie the browser tree to this process's lifetime, at the OS level.

        The same call the sync launcher makes. Its absence here is why the
        Windows leak survived 0.4.0 on this API: an exception out of the async
        block runs __aexit__ and Playwright cleans up, but a KILLED runner never
        reaches either, and only the kernel can act then.

        Best-effort: a failure leaves the pre-existing behaviour rather than
        breaking a launch that is otherwise fine.
        """
        try:
            self._lifetime_guard.bind(self._session_token)
        except Exception:
            pass

    def _arm_cursor_engine(self, owner: Any) -> None:
        """Register this session so its pages move through the Python generator.

        Same wiring as the sync launcher, and the same single hook point: the
        wrappers live on the shared implementation objects, so arming a session
        here covers ``await page.click(...)``, ``await locator.hover(...)`` and
        ``await page.mouse.move(...)`` without a second implementation.
        """
        if self._cursor_engine != ENGINE_PYTHON:
            return
        _enable_cursor_engine(
            owner, seed=self.seed, max_seconds=_cursor_max_seconds(self._humanize)
        )

    def _patch_new_context_defaults(self, browser: Browser) -> None:
        """Both entry points, for the reason spelled out in the sync launcher:
        Playwright's `Browser.new_page` forwards to the IMPLEMENTATION object,
        whose own `new_page` calls `self.new_context` - itself - so a wrapper
        installed on the api object is never consulted, and
        `await browser.new_page()` opened a page with the stock viewport and
        colour scheme against a fingerprint claiming the profile's screen."""
        original = browser.new_context
        defaults = self._default_context_kwargs()
        prep = self._prep_recaptcha
        profile = self._profile  # pass the whole Profile (seed + browsing_history)
        tz = self._timezone  # used by _recaptcha_seed for CONSENT lang+region

        async def patched(**kw):
            merged = dict(defaults)
            merged.update(kw)
            ctx = await original(**merged)
            _patch_new_page_sleep(ctx)
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_async
                await seed_recaptcha_cookies_async(ctx, profile, timezone=tz)
            return ctx

        browser.new_context = patched  # type: ignore[assignment]

        original_page = browser.new_page

        async def patched_page(**kw):
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins, same rule as new_context
            page = await original_page(**merged)
            ctx = page.context
            # The settle new_context installs for later tabs, applied to THIS
            # tab too: the same about:newtab race, and a caller doing
            # `page = await browser.new_page()` goes straight to goto().
            await asyncio.sleep(_NEWTAB_SETTLE)
            _patch_new_page_sleep(ctx)
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_async
                await seed_recaptcha_cookies_async(ctx, profile, timezone=tz)
            return page

        browser.new_page = patched_page  # type: ignore[assignment]

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
        if self._persistent_context is not None:
            try:
                await self._persistent_context.close()
            except Exception:
                pass
            self._persistent_context = None
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
        # Last, and unconditionally: whatever Playwright's close() managed or
        # did not, nothing carrying this session's token may outlive it. Each
        # step above is wrapped in `except: pass`, so before this existed a
        # browser that refused to close was swallowed and leaked in silence.
        if self._session_token:
            try:
                self._lifetime_guard.reap(self._session_token)
            except Exception:
                pass
            self._session_token = SessionToken()

    def _build_env(self, prefs: Dict[str, Any]) -> Dict[str, str]:
        """Same body as the sync class - it always was, character for character.

        The token stamp stays here: it is the one per-session part, and losing
        it is what made the reaper unable to find this API's browser tree.
        """
        return self._session_token.stamp(
            _session.build_env(timezone=self._timezone,
                               egress_ip=self._webrtc_egress_ip))

    def _build_prefs(self) -> Dict[str, Any]:
        """Same body as the sync class, because it is the same body.

        These were twenty identical lines here and twenty in
        `launcher._build_prefs` - same calls, same order, differing only in
        their comments. Both delegate to `_session.build_prefs` now.
        """
        return _session.build_prefs(
            profile=self._profile,
            locale=self._locale,
            timezone=self._timezone,
            extra_prefs=self._extra_prefs,
            headless=self._headless,
            cursor_engine=self._cursor_engine,
            humanize=self._humanize,
        )

    def _resolve_headless(self) -> bool:
        if not self._headless:
            return False
        # Opt-in TRUE headless. The default headful+cloak path intermittently
        # hangs launch_persistent_context ~40% on Windows (window/compositor
        # race with a persistent profile). True headless applies the IDENTICAL
        # fingerprint prefs (screen/viewport/canvas/webgl spoofed the same) and
        # is reliable (~2.3s). Read through `_session` so the sync class gets
        # it too: until 2026-07-27 this env var was honoured HERE ONLY, so a
        # documented knob worked or not depending on which entry point the
        # caller had picked.
        if _session.true_headless_requested():
            return True
        vd = make_virtual_display()
        # Linux: Xvfb to start. Windows/macOS: make_virtual_display() returns
        # None (the binary self-cloaks via cloak_prefs injected in __aenter__),
        # so there is nothing to start - guarding the None was the missing piece
        # that made async headless=True crash with AttributeError on Windows.
        if vd is not None:
            vd.start()
            self._virtual_display = vd
        return False


__all__ = ["InvisiblePlaywright"]
