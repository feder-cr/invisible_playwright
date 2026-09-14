"""Sync Playwright launcher for invisible_playwright."""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

from invisible_playwright._pw.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from . import _session
from ._cursor import resolve_cursor_engine
from invisible_core._fpforge import Profile, generate_profile
from invisible_core import prepare_session_geo
from ._engine import assert_wire_version, resolve_executable
from invisible_core import configure_proxy as _configure_proxy_shared
from ._reaper import SessionToken, guard_for


# ⛔ `_NEWTAB_SETTLE = 0.4` AND ITS WRAPPER AROUND `ctx.new_page` USED TO BE
# HERE, REMOVED 2026-08-23 BECAUSE THE CAUSE WAS CLOSED IN THE ENGINE.
#
# They waited 0.4s after every new tab so the first `goto()` would not get
# hijacked by an internal navigation to `about:newtab`. That navigation was
# not the browser's: it was the PREALLOCATED browser of the new tab grabbing
# the page's channel, because `JugglerFrameParent` identified the target by
# comparing `browserId` against a BrowsingContext's `id` - two different
# counters, which collided. The fix lives there (`juggler/
# JugglerFrameParent.sys.mjs`, the rebuttal with the `<browser>` element), and
# `70-known-bugs.md` [B166] carries the numbers.
#
# Keeping the wait here too would have been a second truth about the same
# fact: the delay had nothing to do with the cause and did not cover it -
# measured the same day, with the wait and without the fix `goto` still died
# anyway, 0 times out of 9 successful. With the fix and no wait at all: 10 out
# of 10.


# The window chrome is NOT a wrapper constant either, for the same reason the
# taskbar below stopped being one, and for one more: the 14 was WRONG. Measured
# against stock Firefox 151 on 2026-08-09, a real browser answers
# outerWidth - innerWidth = 0 and outerHeight - innerHeight = 85; we answered 14
# and 91, identically on both platforms, because a value invented once agrees
# with itself forever and no cross-check can see it. The declaration lives in
# the core as Profile.screen.chrome_w / chrome_h, pinnable like every other
# surface. These names survive only because async_api imports them.

# The taskbar is NOT a wrapper constant. It was one, at 40, while the core
# declared 48 and the engine's compiled floor was 48 - so the viewport was
# derived from one number and screen.availHeight from another, which is a
# disagreement a page can read off two properties of the same window. It is a
# field of the profile now (ScreenProfile.taskbar_px), pinnable like any other,
# and the use sites below read it from there. This name survives only because
# async_api imports it, and it resolves to the same declaration.

# The IANA -> POSIX TZ table moved to `_session` on 2026-07-27, so the async
# class no longer has to import it FROM the sync module. Re-exported under the
# original names because tests and `async_api` import them from here.
_IANA_TO_POSIX_TZ = _session._IANA_TO_POSIX_TZ
_tz_env = _session.tz_env


class InvisiblePlaywright(_session.CommonLaunch):
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
        show_cursor: Optional[bool] = None,
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
            show_cursor: Draw the pointer where the automation is, so a
                person watching the screen can follow the session - the
                Windows arrow, with the package logo's green halo around it.
                Default ``None``, meaning "not specified": the value is
                decided once, by ``invisible_core.prefs.DEFAULT_SHOW_CURSOR``,
                and today that is on. Pass ``False`` to draw nothing. What
                makes it safe either way is that it is drawn in the BROWSER'S
                OWN chrome window, which the page
                cannot reach - it is absent from ``page.screenshot()``,
                invisible to ``document.elementFromPoint``, and not a DOM
                node any site can enumerate. So it changes nothing a
                detector sees, and everything a PERSON sees: a dot gliding
                across a window with nobody touching the mouse reads as
                "this is a bot" to anyone glancing at the monitor. It is a
                demo and debugging switch, not a stealth one.
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
        # ⛔ Stored raw and NOT folded into `_cursor_engine`. The two are
        # unrelated: `humanize` decides WHO draws the path, `show_cursor`
        # decides whether the chrome window draws a dot on top of it. A session
        # with humanize off and the dot on is a legitimate combination - it is
        # how you watch a teleporting cursor - and any code that derived one
        # from the other would forbid it.
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
        # reCAPTCHA cookie pre-seed - opt-in. Gated server-side: if a
        # persistent profile_dir is in use, respect its existing cookies
        # and DON'T enable pre-seed (the profile owns its own state).
        self._prep_recaptcha = bool(prep_recaptcha) and self._profile_dir is None
        # No `fixed_gpu_class=`. It used to be passed here as
        # `forced_gpu_class(self.seed)`, restating what `generate_profile`
        # already derives from the persona it chooses, so this was one of six
        # places that had to stay in step about which GPU a session presents -
        # and a `pin` reached some of them and not the ones that decide what the
        # browser is told. The choice lives in `invisible_core`, once.
        self._profile: Profile = generate_profile(self.seed, pin=self._pin)
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
        #: The DECISION about the srflx, distinct from the fact above.
        #: Starts at None like it: `_build_env` can be called before the
        #: geo path has resolved, and in that case nothing is declared.
        self._srflx_dichiarato: Optional[str] = None
        #: When the egress was last rechecked. Starts at 0 rather than at
        #: `time.monotonic()`: the first context must be able to check
        #: right away, because time may already have passed between launch
        #: and the first page (a slow egress lookup, a binary to download).
        self._ultimo_controllo_uscita: float = 0.0
        #: Consecutive probes that did not respond. Reset by every
        #: successful check, so it counts the bursts and not the total.
        self._uscite_non_misurabili: int = 0

    def __enter__(self) -> Union[Browser, BrowserContext]:
        # Resolve timezone="auto" (and the proxy-set-but-unset default) to a
        # concrete IANA zone AND discover the proxy egress IP - one round-trip,
        # before anything reads self._timezone or builds prefs/env. Fail-early
        # if a proxy is set but the egress can't be resolved.
        _geo = prepare_session_geo(self._timezone, self._proxy)
        self._timezone = _geo.timezone
        self._webrtc_egress_ip = _geo.egress_ip
        # ⛔ TWO DIFFERENT THINGS, and before they were a single field.
        # `_webrtc_egress_ip` is the FACT: where we exit from. It serves the
        # guard against drift, which compares now's egress against the one
        # at launch.
        # `_srflx_dichiarato` is the DECISION: what the engine must
        # announce. It is None when the egress has proven, consistent UDP,
        # because there the real srflx is already born correct and
        # declaring one would add a candidate with no matching
        # allocation - the signal a detector running its own TURN reads.
        # The core reads it in one place only.
        self._srflx_dichiarato = _geo.srflx_da_dichiarare()
        # Geo-aware locale: "auto" derives the language from the egress country (reusing
        # the egress IP already discovered above), like timezone="auto". Keeps the browser
        # language consistent with the proxy's country instead of a fixed en-US.
        if (self._locale or "").strip().lower() == "auto":
            from invisible_core import resolve_session_locale
            self._locale = resolve_session_locale(_geo.egress_ip, self._proxy)
        # binary_path= never reaches ensure_binary(), so the engine check lives
        # on the resolved executable rather than inside the fetcher.
        executable = resolve_executable(self._binary_path)
        # the REAL result of _resolve_headless (did it create an alternate
        # desktop or not?) must be known BEFORE composing the prefs, not
        # after: B172, 2026-08-24.
        pw_headless = self._resolve_headless()
        prefs = self._build_prefs()
        playwright_proxy = _configure_proxy_shared(self._proxy, prefs)
        self._session_token = SessionToken.mint()
        env = self._build_env(prefs)

        try:
            self._pw = sync_playwright().start()
            if self._profile_dir is not None:
                # Persistent context - cookies / localStorage / extensions /
                # prefs all live on disk between runs.
                #
                # ⛔ The line that used to be here said the stealth prefs are
                # "re-injected via firefox_user_prefs on every launch (Playwright
                # writes them to user.js, which overrides anything in prefs.js)".
                # That is FALSE on the Juggler path, and the false belief is what
                # made the first-launch/second-launch asymmetry look impossible.
                # Verified 2026-08-14 in the bundled driver: writePreferences(),
                # the function that creates user.js, lived in
                # server/bidi/third_party/firefoxPrefs.ts and was called only by
                # BidiFirefox.prepareUserDataDir; the BASE prepareUserDataDir was
                # empty and the Juggler Firefox type did not override it. The
                # prefs travelled over the protocol instead: Browser.enable applied
                # them with Services.prefs.setBoolPref / setStringPref /
                # setIntPref - USER-branch setters - after
                # `await this._startCompletePromise`, so Firefox persisted them
                # into prefs.js. Measured: 39 zoom.stealth prefs in prefs.js after
                # the first launch, and no user.js on disk at all.
                #
                # Consequence: launch 1 initialised gfx/fonts with the
                # DEFAULTS, launch 2+ with the stealth prefs already active. Two
                # different code paths, and every gate in this project starts from
                # a fresh profile - which is how the relaunch hang lived unseen.
                #
                # ⛔ THE WHOLE PARAGRAPH ABOVE IS PAST TENSE AS OF firefox-21,
                # and should be read as history. The driver's fork now WRITES
                # the prefs into `user.js` inside the profile and passes
                # `-profile`, and sends `Browser.enable` WITHOUT the userPrefs
                # field; the engine, for its part, REFUSES that field instead
                # of applying it late:
                #
                #     Browser.enable no longer applies preferences. They are
                #     written into the profile before startup.
                #
                # So the first-launch/second-launch asymmetry no longer
                # exists: the prefs are already there when gfx and the fonts
                # initialise, a single path. `firefox_user_prefs=` below
                # remains the way to deliver them - only who writes them, and
                # where, has changed.
                #
                # And the consequence for WHOEVER does not use this fork: a
                # script that launches the binary with UPSTREAM Playwright and
                # passes `firefox_user_prefs` now gets that refusal. It
                # happened to `scripts/ci_font_gate.py` on the first
                # firefox-21, and there the fix is to write a `user.js` into
                # the profile and use a persistent context - not to put the
                # field back in the request.
                # Cause and fix: `70-known-bugs.md` [B150]. The fix is a pref
                # applied by invisible_core to every session, not code here: a
                # geometry-scrubbing remedy lived in this file for a few hours and
                # was REMOVED once the origin was found, so that only one place
                # knows the fact.
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

    #: At most how often the egress is rechecked. The check costs one
    #: request THROUGH the proxy, i.e. the user's bandwidth: doing it on
    #: every page of a scraper would be noisier than the problem it guards.
    _INTERVALLO_CONTROLLO_USCITA_S = 120.0

    #: How many times in a row the probe can fail to respond before the
    #: session gets refused. Just one would be too strict - a timeout
    #: happens - but unlimited would be declared blindness: after three
    #: silent checks at 120s each, that is six minutes in which nobody is
    #: confirming the address the engine announces on every page.
    _MAX_USCITE_NON_MISURABILI = 3

    def _assert_uscita_invariata(self) -> None:
        """Refuses if the egress IP has changed since launch.

        A change mid-session cannot be recovered from: the IP we declare to
        the engine for the srflx was photographed at launch, so from that
        moment the page exits from one address and WebRTC announces
        another - the disagreement detectors look for. Updating it on the
        fly would not help: it would make the WebRTC IP change under the
        site's eyes, which is just as unnatural. If the egress does not
        hold for the session's duration, that proxy is not sticky and is
        not fit for this purpose.
        """
        if not self._proxy or not self._webrtc_egress_ip:
            return
        now = time.monotonic()
        if now - self._ultimo_controllo_uscita < self._INTERVALLO_CONTROLLO_USCITA_S:
            return
        self._ultimo_controllo_uscita = now
        outcome, current = _session.egress_ancora_valido(
            self._proxy, self._webrtc_egress_ip)
        if outcome == _session.USCITA_DERIVATA:
            raise _session.ProxyEgressDrifted(
                "the proxy's egress IP changed during the session: "
                "it was %s at launch, now it is %s. The WebRTC srflx "
                "candidate still declares the first one, so from this "
                "moment the page exits from one address and WebRTC "
                "announces another - the disagreement detectors look for. "
                "This proxy does not hold the session sticky for the "
                "required duration: use one that guarantees it, or "
                "shorten the session."
                % (self._webrtc_egress_ip, current))
        if outcome == _session.USCITA_NON_MISURABILE:
            # It is NOT "holding", and that is why there are three outcomes.
            # A probe that fails ONCE is the network; one that always fails
            # is blindness: from that moment the engine keeps declaring an
            # address on every page that nobody is confirming anymore. It
            # is counted, instead of ignored, and refused only if it
            # repeats.
            self._uscite_non_misurabili += 1
            if self._uscite_non_misurabili >= self._MAX_USCITE_NON_MISURABILI:
                raise _session.ProxyEgressNonVerificabile(
                    "the egress IP was not verifiable for %d checks in a "
                    "row. It is not drift - the probe did not respond at "
                    "all - but it is not parity either: from here on the "
                    "engine would keep declaring an address on every page "
                    "that nobody confirms anymore. Check that the proxy is "
                    "reachable, then relaunch the session."
                    % self._uscite_non_misurabili)
            return
        self._uscite_non_misurabili = 0

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
        loc = self._locale  # used by _recaptcha_seed for CONSENT lang+region

        def patched(**kw):
            self._assert_uscita_invariata()
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins
            ctx = original(**merged)
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_sync
                seed_recaptcha_cookies_sync(ctx, profile, locale=loc)
            # ⛔ ALSO `context.new_page`, which is the NORMAL way to open a
            # tab and was the only one left unguarded. The guard sat on
            # `browser.new_context` and `browser.new_page`, so a session
            # that opens a context and then N pages from there did ONE
            # check only, at the first instant, and stopped watching from
            # then on.
            #
            # Measured 2026-08-25 in a manual session: at launch the egress
            # was `82.40.95.144` and that is what landed in the srflx; nine
            # tabs later the page was exiting from `130.12.17.118`, and the
            # two showed up together on screen - `PUBLIC IP` against
            # `WEBRTC CLIENT SIDE IP OFFER`. The guard existed, it was
            # correct, and it was never asked.
            #
            # The cost stays the same as before: `_assert_uscita_invariata`
            # limits itself to one check every `_INTERVALLO_CONTROLLO_USCITA_S`,
            # so opening ten tabs in a row does not make ten requests.
            _new_page_ctx = ctx.new_page

            def _new_page_guarded(**kw2):
                self._assert_uscita_invariata()
                return _new_page_ctx(**kw2)

            ctx.new_page = _new_page_guarded  # type: ignore[assignment]
            return ctx

        browser.new_context = patched  # type: ignore[assignment]

        original_page = browser.new_page

        def patched_page(**kw):
            self._assert_uscita_invariata()
            merged = dict(defaults)
            merged.update(kw)  # user-supplied wins, same rule as new_context
            page = original_page(**merged)
            ctx = page.context
            if prep:
                from ._recaptcha_seed import seed_recaptcha_cookies_sync
                seed_recaptcha_cookies_sync(ctx, profile, locale=loc)
            return page

        browser.new_page = patched_page  # type: ignore[assignment]


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




