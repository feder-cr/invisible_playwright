"""Sync Playwright launcher for invisible_playwright."""
from __future__ import annotations

import secrets
from pathlib import Path
import time as _time
from typing import Any, Dict, Optional, Union

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

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


#: Settle after a tab is created, in seconds. Named and module-level because
#: `browser.new_page()` needs the same wait and a second literal is a second
#: value: the two drifted apart for as long as one of them did not exist.
_NEWTAB_SETTLE = 0.4


def _patch_sync_new_page_sleep(ctx: Any) -> None:
    """Wrap ctx.new_page() to add a brief settle after tab creation.

    FF150 with Fission emits an about:newtab navigation ~100ms after a tab
    is created.  If goto() is called immediately, it races with that internal
    navigation and raises "Navigation interrupted by about:newtab".  A short
    sleep breaks the race without requiring every call-site to know about it.
    """
    original_new_page = ctx.new_page

    def patched_new_page(**kw):
        page = original_new_page(**kw)
        _time.sleep(_NEWTAB_SETTLE)
        return page

    ctx.new_page = patched_new_page  # type: ignore[assignment]


# Window-chrome and taskbar offsets measured empirically on a headed
# Firefox 150 (no compositor). Used to derive the default new_context
# viewport so it fits inside the spoofed screen without out-of-bounds.
_CHROME_W  = 14
_CHROME_H  = 91
_TASKBAR_H = 40

# The IANA -> POSIX TZ table moved to `_session` on 2026-07-27, so the async
# class no longer has to import it FROM the sync module. Re-exported under the
# original names because tests and `async_api` import them from here.
_IANA_TO_POSIX_TZ = _session._IANA_TO_POSIX_TZ
_tz_env = _session.tz_env


class InvisiblePlaywright:
    """Context manager launching a patched Firefox with a deterministic profile.

    Usage:

        from invisible_playwright import InvisiblePlaywright

        # random seed (different fingerprint each call)
        with InvisiblePlaywright() as browser:
            page = browser.new_page()
            page.goto("https://example.com")

        # explicit seed → same profile every time
        with InvisiblePlaywright(seed=42) as browser:
            ...

        # human-like cursor motion, on by default: the ordinary Playwright
        # pointer calls move the cursor along a path drawn from `seed`
        with InvisiblePlaywright(humanize=True) as browser:
            page = browser.new_page()
            page.click("#submit")   # the pointer travels there, it does not jump

    Optional ``pin`` forces specific fingerprint fields while the rest still
    varies with ``seed``::

        with InvisiblePlaywright(seed=42, pin={"screen.width": 2560}) as browser:
            ...

    After construction, the chosen seed is available as ``self.seed`` - useful
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
        locale: str = "auto",
        timezone: str = "",
        extra_prefs: Optional[Dict[str, Any]] = None,
        binary_path: Optional[str] = None,
        profile_dir: Optional[Union[str, Path]] = None,
        prep_recaptcha: bool = False,
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
            humanize: Move the pointer along a curved, paced path instead of
                teleporting it. Applies to ``page.click`` / ``page.hover`` /
                ``locator.click`` / ``page.mouse.move`` and the rest of the
                ordinary Playwright pointer API - there is nothing new to
                call. Default ``True`` (~1.5 s cap per movement); ``False``
                disables; a float caps a movement in seconds. The path is
                drawn from ``seed``, so the same seed replays the same motion.
                Set ``INVPW_CURSOR_ENGINE=binary`` to go back to letting the
                browser draw it, or ``=off`` to disable motion process-wide.
            locale: BCP-47 tag (e.g. ``"en-US"``) or ``"auto"`` (default).
                ``"auto"`` derives the locale from the egress country - the proxy
                egress IP, or the host's public IP without a proxy - exactly like
                ``timezone="auto"``, keeping the browser language consistent with the
                exit country (a French proxy → ``fr-FR``). Drives
                ``intl.accept_languages`` → both ``navigator.language``/``languages``
                AND the q-valued ``Accept-Language`` header (the patched binary builds
                the header from the pref, never from the raw Playwright locale override,
                so the two never diverge - see nsHttpHandler STEALTHFOX note).
            timezone: IANA zone (e.g. ``"America/New_York"``) - used as-is
                when set, the only way to force a specific zone. ``""``
                (default) or ``"auto"`` ALWAYS resolves from the egress IP:
                through the proxy when one is set, otherwise from the host's
                own public IP (one lookup + an offline mmdb). On failure: with
                a proxy it raises (a foreign proxy on the host TZ is the
                ``timezone_mismatch`` signal); without a proxy it falls back to
                the host TZ so a transient lookup failure can't break launch.
            extra_prefs: Optional dict of Firefox prefs overlayed on top
                of the generated profile - useful for niche tweaks
                without monkey-patching the package.
            profile_dir: Path to a persistent Firefox profile directory.
                When set, the session uses ``launch_persistent_context()``
                so cookies, localStorage, sessionStorage, extensions, cache
                and prefs are kept on disk between runs. ``__enter__``
                returns a ``BrowserContext`` (not a ``Browser``) - use it
                directly: ``with InvisiblePlaywright(profile_dir=p) as ctx:
                page = ctx.new_page()``. First run creates the dir;
                subsequent runs reuse it. Pair with a stable ``seed=`` to
                also pin the fingerprint identity across runs.
        """
        # Constrain to int31 - Firefox's `zoom.stealth.fpp.hw_seed` and
        # related stealth prefs are declared as ``int32_t`` in
        # ``StaticPrefList.yaml``. A 32-bit seed risks the high bit being
        # interpreted as negative on the C++ side, where the noise hooks
        # bail out on ``seed <= 0`` - which produces bit-identical audio
        # / canvas fingerprints across half the sessions.
        self.seed: int = int(seed) if seed is not None else secrets.randbits(31)
        self._pin = pin
        self._headless = headless
        self._proxy = proxy
        self._extra_args = list(extra_args or [])
        self._humanize = humanize
        # Who draws the cursor path: this package (default), the browser
        # (``INVPW_CURSOR_ENGINE=binary``, the way back for anyone depending on
        # the old behaviour), or nobody (``humanize=False``). Resolved once,
        # here, because the prefs handed to the browser depend on the answer.
        self._cursor_engine = resolve_cursor_engine(humanize)
        self._locale = locale
        self._timezone = timezone
        self._extra_prefs = extra_prefs
        self._binary_path = binary_path
        self._profile_dir: Optional[Path] = Path(profile_dir) if profile_dir else None
        # reCAPTCHA cookie pre-seed - opt-in. Gated server-side: if a
        # persistent profile_dir is in use, respect its existing cookies
        # and DON'T enable pre-seed (the profile owns its own state).
        self._prep_recaptcha = bool(prep_recaptcha) and self._profile_dir is None
        self._profile: Profile = generate_profile(
            self.seed, pin=self._pin, fixed_gpu_class=forced_gpu_class(self.seed)
        )
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._persistent_context: Optional[BrowserContext] = None
        self._virtual_display: Any = None
        # Identity for this session's browser tree, and the guard that ties
        # that tree to this process's lifetime. Declared here rather than in
        # __enter__ so that _teardown - which runs on the failure path too,
        # before __enter__ has got anywhere - always finds them.
        self._session_token = SessionToken()
        self._lifetime_guard = guard_for()
        # Proxy egress IP, discovered at launch (see __enter__). Feeds the
        # WebRTC srflx override so the candidate matches the proxy IP, not the
        # real host IP. None when no proxy is set.
        self._webrtc_egress_ip: Optional[str] = None

    def __enter__(self) -> Union[Browser, BrowserContext]:
        # Resolve timezone="auto" (and the proxy-set-but-unset default) to a
        # concrete IANA zone AND discover the proxy egress IP - one round-trip,
        # before anything reads self._timezone or builds prefs/env. Fail-early
        # if a proxy is set but the egress can't be resolved.
        _geo = prepare_session_geo(self._timezone, self._proxy)
        self._timezone = _geo.timezone
        self._webrtc_egress_ip = _geo.egress_ip
        # Geo-aware locale: "auto" derives the language from the egress country (reusing
        # the egress IP already discovered above), like timezone="auto". Keeps the browser
        # language consistent with the proxy's country instead of a fixed en-US.
        if (self._locale or "").strip().lower() == "auto":
            from invisible_core import resolve_session_locale
            self._locale = resolve_session_locale(_geo.egress_ip, self._proxy)
        # binary_path= never reaches ensure_binary(), so the engine check lives
        # on the resolved executable rather than inside the fetcher.
        executable = resolve_executable(self._binary_path)
        prefs = self._build_prefs()
        playwright_proxy = _configure_proxy_shared(self._proxy, prefs)
        pw_headless = self._resolve_headless()
        self._session_token = SessionToken.mint()
        env = self._build_env(prefs)

        try:
            self._pw = sync_playwright().start()
            if self._profile_dir is not None:
                # Persistent context - cookies / localStorage / extensions /
                # prefs all live on disk between runs. Stealth prefs are
                # re-injected via firefox_user_prefs on every launch (Playwright
                # writes them to user.js, which overrides anything in
                # prefs.js inside the persistent dir).
                self._profile_dir.mkdir(parents=True, exist_ok=True)
                self._persistent_context = self._pw.firefox.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    executable_path=str(executable),
                    headless=pw_headless,
                    firefox_user_prefs=prefs,
                    proxy=playwright_proxy,
                    args=self._extra_args,
                    env=env,
                    **self._persistent_context_kwargs(),
                )
                _patch_sync_new_page_sleep(self._persistent_context)
                self._bind_process_tree()
                self._arm_cursor_engine(self._persistent_context)
                return self._persistent_context
            self._browser = self._pw.firefox.launch(
                executable_path=str(executable),
                headless=pw_headless,
                firefox_user_prefs=prefs,
                proxy=playwright_proxy,
                args=self._extra_args,
                env=env,
            )
            # Free post-launch wire check: browser.version is a cached property
            # from the connection initializer, so it costs no round trip and no
            # pref can spoof it. Inside the try so a refusal tears the browser
            # down instead of leaking the process we just refused.
            assert_wire_version(self._browser)
            self._bind_process_tree()
        except BaseException:
            # Python doesn't call __exit__ when __enter__ raises - clean up
            # the virtual display + Playwright manually so we don't leak Xvfb
            # / desktop handles into the user's process.
            self._teardown()
            raise
        self._patch_new_context_defaults(self._browser)
        self._arm_cursor_engine(self._browser)
        return self._browser

    def _bind_process_tree(self) -> None:
        """Tie the browser tree to this process's lifetime, at the OS level.

        MEASURED before being written, because the first attempt at this fixed
        a path that was not broken: an exception out of the `with` block does
        NOT leak - __exit__ runs and Playwright cleans up, zero survivors over
        an interleaved A/B. The leak is the killed-runner path, where __exit__
        never executes at all: launch, kill the runner, and eight processes
        were still alive; twelve on the second attempt. Nothing written inside
        _teardown can reach that, so the guarantee comes from a Windows job
        object that the kernel empties when this process's handle closes,
        however this process ends.

        Best-effort by construction: a failure here leaves the pre-existing
        behaviour rather than breaking a launch that is otherwise fine.
        """
        try:
            self._lifetime_guard.bind(self._session_token)
        except Exception:
            pass

    def _arm_cursor_engine(self, owner: Any) -> None:
        """Register this session so its pages move through the Python generator.

        Registered on the browser (or on the persistent context, which is all
        there is in that mode) rather than on each page: pages appear by
        several routes we do not control - ``browser.new_page()`` builds its
        context inside the driver, and a site can open a popup on its own - and
        every one of them can find its way back to this owner. The seed is the
        session seed, so a replayed seed replays the cursor exactly as it
        replays the fingerprint.
        """
        if self._cursor_engine != ENGINE_PYTHON:
            return
        _enable_cursor_engine(
            owner, seed=self.seed, max_seconds=_cursor_max_seconds(self._humanize)
        )

    def _persistent_context_kwargs(self) -> Dict[str, Any]:
        """Context-level kwargs accepted by launch_persistent_context.

        Identical to ``_default_context_kwargs``: viewport / screen / DPR /
        color-scheme / locale / timezone_id. Up to firefox-4 we had to drop
        locale and timezone_id because Playwright's per-realm overrides
        called IDL methods (``docShell.languageOverride``,
        ``docShell.overrideTimezone``) that weren't exposed by our patched
        build, causing launch_persistent_context to hang for 180s. From
        firefox-5 (C7 chiusura), the C++ ``overrideTimezone`` method is
        present and ``languageOverride`` was already there, so the
        per-realm overrides land and the persistent context starts in
        ~20s like the non-persistent path.
        """
        return self._default_context_kwargs()

    def _patch_new_context_defaults(self, browser: Browser) -> None:
        """Wrap ``browser.new_context`` AND ``browser.new_page`` so their
        defaults derive from the profile (viewport, screen, DPR, color-scheme,
        locale, timezone). Users get a coherent context for free; explicit
        kwargs still override.

        BOTH, because ``new_page`` does not go through the patched
        ``new_context``. Playwright's sync ``Browser.new_page`` forwards to the
        IMPLEMENTATION object, whose own ``new_page`` calls ``self.new_context``
        - itself, the impl - so a wrapper installed on the sync-api object is
        never consulted. Read on the installed Playwright, and it is the call in
        this package's own README and docstring: ``browser.new_page()`` was
        opening a page with Playwright's stock 1280x720 viewport, the host's
        colour scheme and no locale or timezone override, against a fingerprint
        claiming the profile's screen. A viewport that contradicts
        ``screen.width`` is not a missing feature, it is an inconsistency, which
        is the one thing this package exists to avoid.

        Written through the public ``new_page`` rather than by reaching into the
        impl: that keeps Playwright's own ownership wiring (closing the page
        closes the context it owns) instead of reimplementing it against private
        attributes.
        """
        original = browser.new_context
        defaults = self._default_context_kwargs()
        prep = self._prep_recaptcha
        profile = self._profile  # pass the whole Profile (seed + browsing_history)
        tz = self._timezone  # used by _recaptcha_seed for CONSENT lang+region

        def patched(**kw):
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins
            ctx = original(**merged)
            _patch_sync_new_page_sleep(ctx)
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_sync
                seed_recaptcha_cookies_sync(ctx, profile, timezone=tz)
            return ctx

        browser.new_context = patched  # type: ignore[assignment]

        original_page = browser.new_page

        def patched_page(**kw):
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins, same rule as new_context
            page = original_page(**merged)
            ctx = page.context
            # The settle new_context installs for later tabs, applied to THIS
            # tab too: it is the same about:newtab race, and browser.new_page()
            # creates a tab the caller is about to goto() immediately.
            _time.sleep(_NEWTAB_SETTLE)
            _patch_sync_new_page_sleep(ctx)
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_sync
                seed_recaptcha_cookies_sync(ctx, profile, timezone=tz)
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
        # Pass timezone via Playwright's per-realm override (docShell.overrideTimezone
        # → JS::SetRealmTimezoneOverride). The juggler.timezone.override pref path
        # uses JS::SetTimeZoneOverride globally, which is broken on Windows ICU for
        # no-DST IANA names (America/Phoenix, Pacific/Honolulu, ...) - those silently
        # fall back to the host system TZ. The per-realm path works for every zone.
        if self._timezone:
            kwargs["timezone_id"] = self._timezone
        if self._locale:
            kwargs["locale"] = self._locale
        return kwargs

    def __exit__(self, *exc: Any) -> None:
        self._teardown()

    def _teardown(self) -> None:
        if self._persistent_context is not None:
            try:
                self._persistent_context.close()
            except Exception:
                pass
            self._persistent_context = None
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
        # Last, and unconditionally: whatever Playwright's close() did or did
        # not manage, nothing carrying this session's token may outlive it. Each
        # step above is individually wrapped in `except: pass`, so before this
        # existed a browser that refused to close was swallowed and leaked in
        # silence. Only processes positively identified as ours are touched.
        if self._session_token:
            try:
                self._lifetime_guard.reap(self._session_token)
            except Exception:
                pass
            self._session_token = SessionToken()

    # ── helpers ─────────────────────────────────────────────────────────

    def _build_prefs(self) -> Dict[str, Any]:
        """Fingerprint prefs plus humanize toggle (always set explicitly).

        The body lives in `_session.build_prefs`, which the async class calls
        too. It used to be twenty lines here and the SAME twenty inlined into
        `async_api.__aenter__` - identical calls in identical order, differing
        only in their comments, which is how the two entry points drift.
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

    def _build_env(self, prefs: Dict[str, Any]) -> Dict[str, str]:
        """Env for the Firefox subprocess, then stamped with this session's token.

        The body is `_session.build_env`, shared with the async class - it was
        written twice, identically, and the WebRTC pair is a contract with the
        binary, so two landing sites meant two chances to miss a change.

        The token stamp stays here because it is the only genuinely per-session
        part: children inherit the environment, so every process in the tree
        carries it and teardown can find its own tree and only its own.
        """
        return self._session_token.stamp(
            _session.build_env(timezone=self._timezone,
                               egress_ip=self._webrtc_egress_ip))

    def _resolve_headless(self) -> bool:
        """Translate the user's ``headless`` flag.

        When ``True``, Firefox stays in headed mode (real rendering pipeline →
        coherent fingerprint) and the window is hidden: on Linux via a fresh
        Xvfb spawned here; on Windows/macOS via the binary's own window cloak
        (the ``zoom.stealth.cloak_windows`` pref added in ``_build_prefs``), so
        ``make_virtual_display()`` returns ``None`` and nothing is spawned.
        """
        if not self._headless:
            return False
        # Opt-in TRUE headless, shared with the async class. It existed on the
        # async API ONLY until 2026-07-27: a documented env var that worked
        # depending on which entry point the caller happened to pick, which is
        # the same drift that shipped the process-leak fix to half the users.
        if _session.true_headless_requested():
            return True
        vd = make_virtual_display()
        if vd is not None:
            vd.start()
            self._virtual_display = vd
        return False

