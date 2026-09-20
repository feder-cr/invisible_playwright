"""Session logic shared by the sync and async entry points.

WHY THIS FILE EXISTS. There are two `InvisiblePlaywright` classes, and the only
thing keeping them equal was somebody remembering. Measured 2026-07-27: 203 of
252 normalised lines of `async_api.py` also appear in `launcher.py` - 80.6% -
and `__init__`, `_teardown`, `_build_env` and `_default_context_kwargs` are 100%
identical once the word `await` is deleted. So the duplication is not the price
of async; async was the excuse for it.

That is a defect GENERATOR, and it has already produced three:

  * the 0.4.0 process-leak fix reached the sync class only, and shipped to the
    other half of the users under a release note saying it was fixed;
  * `INVPW_TRUE_HEADLESS`, a documented env var, worked on the async API alone;
  * `_build_prefs` was a method on one side and twenty inline lines on the other.

Anything here must stay free of Playwright imports: it is the part that has
nothing to do with which API a caller picked. What legitimately differs between
the two classes is `await`, and nothing else belongs in this file.
"""
from __future__ import annotations

import os
from ._cursor import (ENGINE_BINARY,
                      ENGINE_PYTHON,
                      enable_for as _enable_cursor_engine,
                      max_seconds_for as _cursor_max_seconds)
from ._engine import resolve_executable
from ._juggler.server import MOTION_BUDGET_PREF, SESSION_SEED_PREF
from typing import Any, Dict, Optional

from invisible_core import (DESKTOP_ENV, compose_session_prefs,
                            make_virtual_display)
from invisible_core.launch import (FontManifestMismatch,
                                   cached_font_manifest_path,
                                   verify_font_manifest)

__all__ = ["build_prefs", "true_headless_requested", "TRUE_HEADLESS_ENV",
           "ProxyEgressDrifted", "egress_ancora_valido"]

#: Opt-in to real headless. Read HERE rather than in one of the two classes,
#: because reading it in one of them is exactly how it came to work on the async
#: API only.
#:
#: ⛔ THE REASON THIS USED TO BE A WORKAROUND IS NOW KNOWN, AND FIXED. This
#: comment said: "the default headful+cloak path intermittently hangs
#: launch_persistent_context on Windows (~40%, a window/compositor race with a
#: persistent profile); true headless ... is reliable". It had the right
#: neighbourhood - window and compositor - and no mechanism, so it stood as an
#: escape hatch for months.
#:
#: The mechanism, measured 2026-08-14: Windows declared the chrome window FULLY
#: OCCLUDED and in the hanging runs the verdict was never revoked
#: (`nsWindow::NotifyOcclusionState() mIsFullyOccluded 1` with no later 0). An
#: inactive BrowsingContext never paints, so delayed startup never finishes,
#: `browser-delayed-startup-finished` never fires, no target is created and the
#: launch times out at 180 s. True headless was reliable for a reason nobody had
#: named: there is no window to occlude.
#:
#: Fixed by `widget.windows.window_occlusion_tracking.enabled=false`, applied to
#: EVERY session by invisible_core - 24 relaunches out of 24 against 6 hangs in 9.
#: So this variable is no longer a workaround for that hang; it remains as a
#: deliberate choice of a different rendering path. Full account:
#: `71-bug-archive.md` [B150].
TRUE_HEADLESS_ENV = "INVPW_TRUE_HEADLESS"


def true_headless_requested(env: Optional[Dict[str, str]] = None) -> bool:
    return (env if env is not None else os.environ).get(TRUE_HEADLESS_ENV) == "1"


#: The IANA -> POSIX conversion comes from the core, which is where the table
#: lives. It was duplicated here byte-for-byte - ten entries, including the
#: Phoenix row that exists because mapping Arizona to MST7MDT made libc apply
#: DST and an identification service deduce a Denver origin. The core's own
#: comment admitted its copy had been "copied verbatim from the wrapper", so
#: the two had a documented keep-in-sync obligation and nothing enforcing it.
#: `tz_env` was made public in the core for exactly this reason. (No version
#: number here on purpose: the pin lives in pyproject.toml and a test
#: forbids a second copy of it anywhere in src/, prose included.)
#
#: GUARDED, because an import that fails must say WHY. Taking these names
#: unguarded meant that an environment holding an older core died with
#: `ImportError: cannot import name 'IANA_TO_POSIX_TZ' from 'invisible_core'` -
#: a message about a symbol, from a package the user did not choose the version
#: of, on the browser launch path. The pin machinery exists to explain exactly
#: this, and a bare module-level import walks straight past it. Reported by a
#: user within minutes of the change.
try:
    from invisible_core import (  # noqa: F401 - the import IS the probe
        IANA_TO_POSIX_TZ as _IANA_TO_POSIX_TZ,
        tz_env,
    )
except ImportError as _exc:  # pragma: no cover - exercised by the old-core probe
    from ._pin import declared_core_pin as _declared_core_pin

    try:
        _want = _declared_core_pin() or "a newer version"
    except Exception:
        _want = "a newer version"
    raise ImportError(
        f"invisible-playwright needs invisible-core {_want}: the installed one "
        f"does not provide the timezone conversion this version imports "
        f"({_exc}). Upgrade with `pip install -U invisible-playwright`, which "
        f"pulls the core it was built against."
    ) from _exc

#: The proxy egress IP fed to nICEr's bridge as the srflx override. An explicit
#: caller-supplied value wins over the one discovered at launch.
WEBRTC_IP_ENV = "STEALTHFOX_WEBRTC_PUBLIC_IP"
WEBRTC_NO_IPV6_ENV = "STEALTHFOX_WEBRTC_DISABLE_IPV6"


#: The engine reads this at startup; see build_env for why a pref cannot
#: work here. Never rename: it is part of the binary contract.
FONT_MANIFEST_ENV = "STEALTHFOX_FONT_MANIFEST"


def build_env(
    *,
    timezone: Optional[str],
    #: The address to DECLARE as srflx, or None to declare nothing and let the
    #: real one through. ⛔ This is NOT the egress IP: it is a DECISION the
    #: core makes in a single place, looking at the capabilities of the egress
    #: (`SessionGeo.srflx_to_declare`). The old name, `egress_ip`, said
    #: something else and made the rule look like "there's a proxy -> declare
    #: it", which is the wrong question.
    srflx_declared: Optional[str],
    profile: Any = None,
    executable: Optional[str] = None,
    base_env: Optional[Dict[str, str]] = None,
    #: What the session's hidden surface wants in the browser's environment
    #: (`make_virtual_display().launch_env()`), or nothing. ⛔ Applied LAST and
    #: over a cleared slot: the desktop name is a fact of THIS session, and a
    #: headed session opened while a hidden one is still alive in the same
    #: process must not inherit the hidden one's desktop through
    #: `os.environ`. `INVPW_DESKTOP` is removed unconditionally first for the
    #: same reason.
    display_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """The environment the Firefox subprocess is launched with, minus the token.

    The session token is stamped by the caller, because it is the one part that
    is genuinely per-session; everything here was written twice, identically,
    in `launcher._build_env` and `async_api._build_env`.

    Fonts DO need an env, and only an env will do. The engine builds its font
    list during app startup, inside InitSharedFontListForPlatform, and on this
    path the prefs never reach the profile at all - Playwright sends them over
    the wire and JS applies them once the browser is already running. Measured
    2026-08-08: the pref of the same name always read empty there, so the
    engine's packaged copy won and the manifest this package declares was
    inert. An environment variable is set at process creation and inherited by
    every content process.

    `profile=None` sets nothing, which leaves the engine on its own copy - the
    floor that keeps a browser launched without this package rendering.
    """
    env = dict(base_env if base_env is not None else os.environ)
    if timezone:
        env["TZ"] = tz_env(timezone)
    manifest = getattr(getattr(profile, "font", None), "manifest", "")
    if manifest:
        # Refuse rather than hand the engine metrics for faces it does not
        # carry. That failure is not an error at runtime - it is a page laid
        # out a few pixels wrong, which nothing observes. A browser that will
        # not start is at least loud, and the engine still has its own copy to
        # fall back to if this variable is simply absent.
        if executable:
            missing = verify_font_manifest(manifest, executable)
            # `None` means the engine has no fonts/ beside it, so there was
            # nothing to check against - not that the check passed. Proceeding
            # is right anyway: an engine with no bundled fonts cannot be made
            # worse by a manifest naming files it does not have either way, and
            # refusing would reject every layout that is not ours.
            if missing:
                raise FontManifestMismatch(
                    f"the font manifest this package declares names "
                    f"{len(missing)} face file(s) the engine does not carry "
                    f"(e.g. {missing[:3]}). Engine: {executable}. The metrics "
                    f"would describe fonts that are not there.")
        path = cached_font_manifest_path(manifest)
        if path is not None:
            # setdefault: an already-set value wins, same rule as the WebRTC IP.
            env.setdefault(FONT_MANIFEST_ENV, str(path))
    # WebRTC srflx override, plus dropping IPv6 from gathering.
    webrtc_ip = env.get(WEBRTC_IP_ENV) or srflx_declared
    if webrtc_ip:
        env[WEBRTC_IP_ENV] = webrtc_ip
        # ONLY behind a proxy, and the reason is a measurement.
        #
        # A retail Firefox on a dual-stack connection emits an IPv6 srflx with
        # the REAL global address, in the clear: mDNS obfuscation only covers
        # host candidates. Behind an IPv4 proxy that address would be a leak
        # and, worse, an inconsistency - HTTP goes out through the proxy and
        # WebRTC shows home. So there the filter is needed.
        #
        # Without a proxy it protects against NOTHING and costs form: measured
        # on 2026-08-25 against the retail install on the same connection, the
        # retail emits 6 candidates (host UDP x2, host TCP x2, srflx v4, srflx
        # v6) while we were emitting 3. We looked like an IPv4-only machine
        # where the reference is dual-stack.
        #
        # Before today this `if` was not involved: the filter was always
        # turned on by the pref `zoom.stealth.webrtc.disable_ipv6`, which the
        # environment only overrode. With the second source removed, the
        # condition became expressible in one line.
        env[WEBRTC_NO_IPV6_ENV] = "1"
    env.pop(DESKTOP_ENV, None)
    env.update(display_env or {})
    return env


def build_prefs(
    *,
    profile: Any,
    locale: Optional[str],
    timezone: Optional[str],
    extra_prefs: Optional[Dict[str, Any]],
    virtual_display: bool,
    cursor_engine: str,
    humanize: Any,
    show_cursor: Optional[bool] = None,
    session_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Fingerprint prefs plus the humanize toggle, which is always set explicitly.

    Takes values rather than a session object so it stays callable from a test
    without constructing either class - and so that adding a field to one class
    cannot silently change what the other one builds.
    """
    # The core composes it. This was the third of
    # three places that stacked layers on top of translate_profile_to_prefs in
    # its own order, and nothing compared the results: the one in
    # get_default_stealth_prefs never called configure_proxy at all, so a caller
    # driving Playwright themselves with a SOCKS endpoint got no network.proxy.*
    # pref and went out on the host's own address.
    #
    # What stays here is this path's DELIVERY and its two decisions:
    #
    #   humanize   The pref selects WHICH generator runs, not whether motion
    #              happens. While the wrapper draws the path it must be false,
    #              or every waypoint we send would itself be expanded into a
    #              path by the browser. `max_seconds_for` is applied HERE rather
    #              than passing `humanize` through, because a falsy-but-numeric
    #              cap with the binary engine selected has to mean the default,
    #              not "off".
    #   show_cursor  Passed through UNTRANSLATED, and that is the difference
    #              from the line above: `humanize` needs a decision here
    #              because two engines compete for it, while the visible dot
    #              has exactly one meaning and the core owns it. Anything this
    #              function computed about it would be a second place that
    #              knows, which is the defect the paragraph above is about.
    #
    # The namespace MUST be stealthfox.* - that is what the binary's Juggler
    # reads. An earlier `invisible_playwright.*` spelling was a dead no-op, so
    # humanize never fired and every click teleported the cursor.
    prefs = compose_session_prefs(
        profile,
        locale=locale,
        timezone=timezone,
        extra_prefs=extra_prefs,
        # The REAL value, not a guess: B172, 2026-08-24. The caller passes
        # whether `make_virtual_display()` actually created something - an
        # Xvfb on Linux, a Win32 desktop on Windows - and the sandbox
        # workarounds meant for that desktop apply only then. Nothing here
        # reads the platform: the fact travels, not a prediction of it.
        virtual_display=virtual_display,
        humanize=(_cursor_max_seconds(humanize)
                  if cursor_engine == ENGINE_BINARY else False),
        show_cursor=show_cursor,
    ).prefs
    # ⛔ THE TYPING SEED RIDES IN THE PREFS AND NEVER REACHES THE PROFILE.
    #
    # The engine client has no seed of its own - it is constructed by a
    # transport that knows nothing about a session - and this dict is the one
    # thing the launcher composes in full that already arrives there. The
    # server pops the key in `op_launch` before writing `user.js`, so no
    # session identifier is written to disk; the alternatives were a module
    # global (wrong the moment a process holds two sessions) and a walk through
    # four private attributes of the vendored client.
    #
    # It follows `humanize`, like the cursor: a caller who turned human motion
    # off asked for a machine, and giving them a hand on the keyboard anyway
    # would be a second answer to a question they already answered.
    # ⛔ AND THE MOTION BUDGET RIDES WITH IT, under exactly the same condition,
    # for the one pointer movement the client cannot wrap: the travel of a drag,
    # which happens with the button already down and whose far end is only
    # resolved after the press. That movement is generated inside the server,
    # so without this the caller's `humanize=<n>` would cap every click and be
    # ignored by every drag.
    #
    # The condition is shared and not merely similar: the server draws a path
    # only when it has the seed, so a budget without one would cap a movement
    # nobody is making.
    #
    # Milliseconds, and an int: Gecko has no float pref type, and `1.0` written
    # as a number arrives with the right value and the wrong type - the defect
    # `ui.textScaleFactor` had.
    if session_seed is not None and humanize:
        prefs[SESSION_SEED_PREF] = int(session_seed)
        prefs[MOTION_BUDGET_PREF] = int(round(
            _cursor_max_seconds(humanize) * 1000.0))
    return prefs


class ProxyEgressDrifted(RuntimeError):
    """The egress IP changed MID-SESSION.

    This is not a condition the product can recover from, and it is deliberate
    that it is an error instead of a silent update.

    The IP we declare to the engine for the WebRTC srflx candidate is
    discovered ONCE, at launch. If the egress changes afterwards, the page
    exits from one address and WebRTC announces another: it is exactly the
    comparison detectors make ("WebRTC IP doesn't match your Remote IP"), and
    no Firefox on a real connection produces it.

    Updating the value on the fly would be worse, not better: the site would
    see the WebRTC IP change before its own eyes mid-session, which is just as
    unnatural a signal. If the egress does not hold for the duration of the
    session, that proxy is not sticky and is not usable for this purpose.

    Measured on 2026-08-25: the two providers tried both declare sessions
    sticky by TIME (60 minutes at most for one, a sliding timeout for the
    other, which decays even earlier if the residential peer disconnects), so
    on a long enough session the drift is not a risk: it is a certainty.
    """


class ProxyEgressNonVerificabile(RuntimeError):
    """The egress could not be MEASURED, repeatedly.

    This is not drift and it is not parity: it is absence of measurement. A
    proxy that does not answer the probe for several checks in a row is not a
    proxy that holds, it is a proxy we are flying blind on while the engine
    keeps declaring to every page an address that nobody is confirming any
    more.
    """


#: The three outcomes of the check. There are THREE and not two on purpose:
#: see why in the docstring of `egress_ancora_valido`.
USCITA_REGGE = "regge"
USCITA_DERIVATA = "derivata"
USCITA_NON_MISURABILE = "non_misurabile"


def egress_ancora_valido(proxy: Optional[Dict[str, str]],
                         atteso: Optional[str],
                         *, timeout: int = 20) -> "tuple[str, Optional[str]]":
    """(outcome, current_ip), with outcome among the three `USCITA_*`.

    ⛔ THERE ARE THREE OUTCOMES BECAUSE TWO LIE. Until 2026-08-25 this function
    returned `(True, None)` when the probe FAILED, with a comment that argued
    the right half of the thing well - "a failed discovery is not a drift",
    and that is true, a network problem is not turned into an accusation
    against the proxy. But the returned value said `regge` (holds), i.e. it
    **asserted parity on the basis of a measurement that had not taken
    place**.

    It is the same class of defect this project has already paid for twice
    and fixed twice:

    - `fppro_consistency.py` printed CONSISTENCY PASS when `visitor_id` was
      mute in BOTH runs, because two `None`s come out identical. It has had a
      third outcome since 2026-08-15, exit code 2 = NOT INTERPRETABLE.
    - `repair_core` set `verdict = None` under a handler that said "a broken
      probe is not a licence", above a test `verdict is not None` that then
      let the reinstall proceed.

    The caller now distinguishes: on `USCITA_DERIVATA` it refuses right away,
    on `USCITA_NON_MISURABILE` it counts and refuses only if it repeats,
    because a probe that fails once is the network and one that always fails
    is blindness.

    The case with no proxy or no expected value stays `USCITA_REGGE`, and it
    is different from the other two: there is nothing to betray there, no
    measurement was missed.
    """
    if not proxy or not atteso:
        return USCITA_REGGE, None
    from invisible_core import _geo
    try:
        attuale = _geo.discover_egress_ip(proxy, timeout=timeout)
    except Exception:
        return USCITA_NON_MISURABILE, None
    return (USCITA_REGGE if attuale == atteso else USCITA_DERIVATA), attuale


class CommonLaunch:
    """The six methods both entry points had, written once.

    ⛔ THE DUPLICATION THIS CLOSES IS THE ONE THIS FILE WAS CREATED FOR, and it
    survived the file's own creation. `_session` was written on 2026-07-27 to
    hold what the sync and async classes share, and it did extract the
    functions - `build_env`, `build_prefs`, `true_headless_requested`. What it
    left behind were the METHODS that call them: six of them, in both classes,
    with bodies that are identical byte for byte once the docstrings are
    removed. 222 lines saying the same thing twice.

    The measurement that says it is the same thing: the two classes use the
    SAME FOURTEEN attributes of `self` across those six methods, and the ASTs
    of the bodies compare equal. What made them look different - similarity
    ratios between 32% and 69% - was entirely comments worded differently.

    ⛔ AND THE COST OF THE SPLIT IS NOT HYPOTHETICAL. The three defects listed
    at the top of this file all have the same shape, and one of them is in the
    code that moved here: `INVPW_TRUE_HEADLESS` was honoured by the async class
    alone, so a documented environment variable worked or not depending on
    which entry point the caller had picked.

    ⛔ WHAT THIS CLASS EXPECTS, said out loud because a mixin's contract is
    otherwise invisible: both subclasses set `seed`, `_binary_path`,
    `_cursor_engine`, `_extra_prefs`, `_headless`, `_humanize`,
    `_lifetime_guard`, `_locale`, `_profile`, `_session_token`, `_show_cursor`,
    `_srflx_declared`, `_timezone` and `_virtual_display` in their own
    `__init__`. They already did, identically, which is why this works at all.
    """

    def _resolve_headless(self) -> bool:
        """Translate the user's ``headless`` flag.

        When ``True``, Firefox stays in headed mode (real rendering pipeline →
        coherent fingerprint) and the window is hidden by drawing on a screen
        nobody looks at: on Linux a fresh Xvfb spawned here; on Windows a
        fresh Win32 desktop the spawner creates the browser on
        (``STARTUPINFO.lpDesktop``, read from ``INVPW_DESKTOP``). The engine
        is stock on this surface: no pref hides anything.

        ``self._virtual_display`` is the fact ``_build_prefs`` reads to decide
        whether the desktop workarounds apply (B172).
        """
        if not self._headless:
            return False
        # Opt-in TRUE headless, shared with the async class. It existed on the
        # async API ONLY until 2026-07-27: a documented env var that worked
        # depending on which entry point the caller happened to pick, which is
        # the same drift that shipped the process-leak fix to half the users.
        if true_headless_requested():
            return True
        vd = make_virtual_display()
        if vd is not None:
            vd.start()
            self._virtual_display = vd
        return False

    def _default_context_kwargs(self) -> Dict[str, Any]:
        p = self._profile
        kwargs: Dict[str, Any] = {
            "viewport":            {"width":  p.screen.width  - p.screen.chrome_w,
                                     "height": (p.screen.height
                                                - p.screen.taskbar_px
                                                - p.screen.chrome_h)},
            "screen":              {"width": p.screen.width, "height": p.screen.height},
            # ⛔ device_scale_factor and color_scheme are NO LONGER passed.
            # They were a second source for two facts invisible_core already
            # declares (layout.css.devPixelsPerPx and, since 2026-08-24,
            # layout.css.prefers-color-scheme.content-override), and this one
            # won: measured, setting the pref to a different value the
            # browser did not move. Now there is only one path.
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

    def _build_env(self, prefs: Dict[str, Any]) -> Dict[str, str]:
        """Env for the Firefox subprocess, then stamped with this session's token.

        The body is `build_env`, shared with the async class - it was
        written twice, identically, and the WebRTC pair is a contract with the
        binary, so two landing sites meant two chances to miss a change.

        The token stamp stays here because it is the only genuinely per-session
        part: children inherit the environment, so every process in the tree
        carries it and teardown can find its own tree and only its own.
        """
        vd = self._virtual_display
        return self._session_token.stamp(
            build_env(timezone=self._timezone,
                      srflx_declared=self._srflx_declared,
                      profile=self._profile,
                      executable=resolve_executable(self._binary_path),
                      display_env=vd.launch_env() if vd is not None else None))

    def _build_prefs(self) -> Dict[str, Any]:
        """Fingerprint prefs plus humanize toggle (always set explicitly).

        The body lives in `build_prefs`, which the async class calls
        too. It used to be twenty lines here and the SAME twenty inlined into
        `async_api.__aenter__` - identical calls in identical order, differing
        only in their comments, which is how the two entry points drift.
        """
        return build_prefs(
            profile=self._profile,
            locale=self._locale,
            timezone=self._timezone,
            extra_prefs=self._extra_prefs,
            virtual_display=self._virtual_display is not None,
            cursor_engine=self._cursor_engine,
            humanize=self._humanize,
            show_cursor=self._show_cursor,
            session_seed=self.seed,
        )

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
