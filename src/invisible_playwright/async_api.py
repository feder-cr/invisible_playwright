"""Async Playwright façade - mirrors sync_api but with async/await."""
from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

from invisible_playwright._pw.async_api import Browser, BrowserContext, Playwright, async_playwright

from . import _session
from ._cursor import resolve_cursor_engine
from invisible_core._fpforge import Profile, generate_profile
from invisible_core import prepare_session_geo
from ._engine import assert_wire_version, resolve_executable
from invisible_core import configure_proxy as _configure_proxy_shared
from ._reaper import SessionToken, guard_for


class InvisiblePlaywright(_session.CommonLaunch):
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
        show_cursor: Optional[bool] = None,
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
        # See the sync launcher: independent of `humanize`, deliberately.
        # ⛔ NOT `bool(...)`: that would collapse None - "the caller did not
        # say" - into False and pin the switch off, silently undoing the one
        # place that decides the default. None is carried all the way to
        # `invisible_core.prefs`, which is the only thing allowed to resolve
        # it.
        self._show_cursor = (None if show_cursor is None
                             else bool(show_cursor))
        self._locale = locale
        self._timezone = timezone
        self._extra_prefs = extra_prefs
        self._binary_path = binary_path
        self._profile_dir: Optional[Path] = Path(profile_dir) if profile_dir else None
        # reCAPTCHA pre-seed gated server-side; respect persistent profile.
        self._prep_recaptcha = bool(prep_recaptcha) and self._profile_dir is None
        # No `fixed_gpu_class=` - see the note on the same call in `launcher.py`.
        # The GPU class is derived from the persona `invisible_core` chooses.
        self._profile: Profile = generate_profile(self.seed, pin=self._pin)
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
        #: The DECISION about the srflx, distinct from the fact above.
        #: Starts as None like it: `_build_env` can be called before
        #: the geo path has resolved, and in that case nothing is
        #: declared.
        self._srflx_declared: Optional[str] = None
        #: See the twin in `launcher.py`: starts at 0 because the first
        #: context must be able to check right away.
        self._ultimo_controllo_uscita: float = 0.0
        #: Consecutive probes that got no response. Reset by every
        #: successful check, so it counts bursts, not the total.
        self._uscite_non_misurabili: int = 0

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
        # ⛔ TWO DIFFERENT THINGS, and they used to be a single field.
        # `_webrtc_egress_ip` is the FACT: where we exit from. It feeds
        # the guard against drift, which compares the current egress
        # against the one at launch.
        # `_srflx_declared` is the DECISION: what the engine must
        # announce. It is None when the egress has proven, consistent
        # UDP, because there the real srflx is already correct and
        # declaring one would add a candidate with no matching
        # allocation - the signal a detector running its own TURN reads.
        # The core takes it in a single spot.
        self._srflx_declared = _geo.srflx_to_declare()
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
        # the REAL result of _resolve_headless (did it create an
        # alternate desktop or not?) must be known BEFORE composing the
        # prefs, not after: B172, 2026-08-24.
        pw_headless = self._resolve_headless()
        prefs = self._build_prefs()
        playwright_proxy = _configure_proxy_shared(self._proxy, prefs)
        self._session_token = SessionToken.mint()
        env = self._build_env(prefs)
        try:
            self._pw = await async_playwright().start()
            if self._profile_dir is not None:
                # See sync launcher for the persistent-context rationale.
                self._profile_dir.mkdir(parents=True, exist_ok=True)
                # firefox-5 ships the C++ overrideTimezone IDL method (C7
                # closure), so locale + timezone_id now propagate cleanly
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



    #: Twin of `launcher._INTERVALLO_CONTROLLO_USCITA_S`. The two classes
    #: must stay equal: that is the defect `_session.py` exists to stop
    #: repeating, and it has already produced three bugs.
    _INTERVALLO_CONTROLLO_USCITA_S = 120.0

    #: How many times in a row the probe can fail to respond before the
    #: session gets rejected. One alone would be too strict - a timeout
    #: happens - but unlimited would be declared blindness: after three
    #: silent checks at 120 s each, that is six minutes in which nothing
    #: is confirming the address the engine announces on every page.
    _MAX_USCITE_NON_MISURABILI = 3

    async def _assert_uscita_invariata(self) -> None:
        """Rejects if the egress IP has changed since launch. See the
        sync twin in `launcher.py` for why it does not update on the fly."""
        if not self._proxy or not self._webrtc_egress_ip:
            return
        now = time.monotonic()
        if now - self._ultimo_controllo_uscita < self._INTERVALLO_CONTROLLO_USCITA_S:
            return
        self._ultimo_controllo_uscita = now
        # Discovery is synchronous and hits the network: must not block the loop.
        outcome, current = await asyncio.get_running_loop().run_in_executor(
            None, _session.egress_ancora_valido, self._proxy,
            self._webrtc_egress_ip)
        if outcome == _session.USCITA_DERIVATA:
            raise _session.ProxyEgressDrifted(
                "the proxy's egress IP changed during the session: "
                "at launch it was %s, now it is %s. The WebRTC srflx "
                "candidate still declares the first one, so from this "
                "moment the page exits from one address and WebRTC "
                "announces another - the disagreement detectors look "
                "for. This proxy does not hold the session sticky for "
                "the required duration: use one that guarantees it, or "
                "shorten the session."
                % (self._webrtc_egress_ip, current))
        if outcome == _session.USCITA_NON_MISURABILE:
            # This is NOT "holds", which is why there are three outcomes. A
            # probe that fails ONCE is the network; one that always fails is
            # blindness: from that moment the engine keeps declaring an
            # address on every page that nobody is confirming anymore. It is
            # counted, instead of ignored, and rejected only if it repeats.
            self._uscite_non_misurabili += 1
            if self._uscite_non_misurabili >= self._MAX_USCITE_NON_MISURABILI:
                raise _session.ProxyEgressNonVerificabile(
                    "the egress IP could not be verified for %d checks in "
                    "a row. It is not drift - the probe did not respond at "
                    "all - but it is not agreement either: from here on "
                    "the engine would be declaring an address on every "
                    "page that nobody confirms anymore. Check that the "
                    "proxy is reachable, then relaunch the session."
                    % self._uscite_non_misurabili)
            return
        self._uscite_non_misurabili = 0

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
        loc = self._locale  # used by _recaptcha_seed for CONSENT lang+region

        async def patched(**kw):
            await self._assert_uscita_invariata()
            merged = dict(defaults)
            merged.update(kw)
            ctx = await original(**merged)
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_async
                await seed_recaptcha_cookies_async(ctx, profile, locale=loc)
            # ⛔ `context.new_page` TOO: the same gap as the sync path, the
            # same fix. The guard sat on `browser.new_context` and
            # `browser.new_page`, and not on the NORMAL way of opening a
            # tab, so a session that opened a context and then N pages did
            # only one check. Measured: at launch the egress was one, nine
            # tabs later it was another, and both showed up together on the
            # same page.
            #
            # Fixing it on only one path would have left the two APIs
            # giving different guarantees about the same thing.
            _new_page_ctx = ctx.new_page

            async def _new_page_guarded(**kw2):
                await self._assert_uscita_invariata()
                return await _new_page_ctx(**kw2)

            ctx.new_page = _new_page_guarded  # type: ignore[assignment]
            return ctx

        browser.new_context = patched  # type: ignore[assignment]

        original_page = browser.new_page

        async def patched_page(**kw):
            await self._assert_uscita_invariata()
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins, same rule as new_context
            page = await original_page(**merged)
            ctx = page.context
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_async
                await seed_recaptcha_cookies_async(ctx, profile, locale=loc)
            return page

        browser.new_page = patched_page  # type: ignore[assignment]


    async def __aexit__(self, *exc: Any) -> None:
        await self._teardown()

    async def _teardown(self) -> None:
        """Shut everything down, and finish doing it even while being cancelled.

        ⛔ `except Exception` DOES NOT CATCH A CANCELLATION, and that is the
        whole reason this is not four plain try/excepts any more. When the
        task running this is cancelled, the `await` below raises
        `asyncio.CancelledError`, which inherits from `BaseException` and not
        from `Exception` - so it escapes, teardown stops at whichever step it
        had reached, and everything after it is skipped. What survives is a
        browser nobody closes: the exact orphan this project has already
        measured twice, once as 88 stray firefox processes and once as 7,308
        leftover profile directories.

        So a cancellation is CAUGHT, kept, and re-raised at the end - the
        cancellation still propagates, as asyncio requires, but only after
        every step has had its turn. Reported by DatGuy1 as #104 against the
        published `main` and fixed here in the same shape.

        ⛔ AND THE REAPING IS IN A `finally`, not last in the body: it is the
        one step that must run even if the loop above dies in a way this does
        not model. Nothing carrying this session's token may outlive it.
        """
        cancelled: Optional[BaseException] = None

        async def close(step: Any) -> None:
            nonlocal cancelled
            try:
                await step
            except asyncio.CancelledError as stop:
                cancelled = stop
            except Exception:
                pass

        try:
            if self._persistent_context is not None:
                await close(self._persistent_context.close())
                self._persistent_context = None
            if self._browser is not None:
                await close(self._browser.close())
                self._browser = None
            if self._pw is not None:
                await close(self._pw.stop())
                self._pw = None
            if self._virtual_display is not None:
                # Synchronous, so it cannot be cancelled mid-call the way the
                # three awaits above can.
                try:
                    self._virtual_display.stop()
                except Exception:
                    pass
                self._virtual_display = None
        finally:
            if self._session_token:
                try:
                    self._lifetime_guard.reap(self._session_token)
                except Exception:
                    pass
                self._session_token = SessionToken()

        if cancelled is not None:
            raise cancelled





# The three classes a caller can catch, exported the way Playwright's own
# `async_api` exports its errors. `TargetClosedError` is the one that matters
# here: it is what every call on a page, context or browser that is GONE
# raises - a disposed object or a closed pipe alike, since 0.15.0 - so a
# caller that has to tell "the page refused" from "the browser died" catches
# the type instead of matching sentences.
from invisible_playwright._pw._impl._errors import (  # noqa: E402
    Error, TargetClosedError, TimeoutError)

__all__ = ["InvisiblePlaywright", "Error", "TargetClosedError", "TimeoutError"]
