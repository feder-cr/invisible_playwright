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
import sys
from typing import Any, Dict, Optional

from invisible_core import cloak_prefs, translate_profile_to_prefs

from ._cursor import humanize_prefs as _humanize_prefs

__all__ = ["build_prefs", "true_headless_requested", "TRUE_HEADLESS_ENV"]

#: Opt-in to real headless. The default headful+cloak path intermittently hangs
#: `launch_persistent_context` on Windows (~40%, a window/compositor race with a
#: persistent profile); true headless applies the IDENTICAL fingerprint prefs and
#: is reliable. Read HERE rather than in one of the two classes, because reading
#: it in one of them is exactly how it came to work on the async API only.
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
    from invisible_core import IANA_TO_POSIX_TZ as _IANA_TO_POSIX_TZ, tz_env
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


def build_env(
    *,
    timezone: Optional[str],
    egress_ip: Optional[str],
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """The environment the Firefox subprocess is launched with, minus the token.

    The session token is stamped by the caller, because it is the one part that
    is genuinely per-session; everything here was written twice, identically,
    in `launcher._build_env` and `async_api._build_env`.

    Fonts need no env: the patched binary is bundle-only and self-contained.
    """
    env = dict(base_env if base_env is not None else os.environ)
    if timezone:
        env["TZ"] = tz_env(timezone)
    # WebRTC srflx override, plus dropping IPv6 from gathering behind a proxy.
    webrtc_ip = env.get(WEBRTC_IP_ENV) or egress_ip
    if webrtc_ip:
        env[WEBRTC_IP_ENV] = webrtc_ip
        env[WEBRTC_NO_IPV6_ENV] = "1"
    return env


def build_prefs(
    *,
    profile: Any,
    locale: Optional[str],
    timezone: Optional[str],
    extra_prefs: Optional[Dict[str, Any]],
    headless: bool,
    cursor_engine: str,
    humanize: Any,
) -> Dict[str, Any]:
    """Fingerprint prefs plus the humanize toggle, which is always set explicitly.

    Takes values rather than a session object so it stays callable from a test
    without constructing either class - and so that adding a field to one class
    cannot silently change what the other one builds.
    """
    prefs = translate_profile_to_prefs(
        profile,
        locale=locale,
        timezone=timezone,
        extra_prefs=extra_prefs,
        virtual_display=bool(headless and sys.platform == "win32"),
    )
    # Windows and macOS hide the headless window through the binary's own cloak
    # (DWMWA_CLOAK / NSWindow alpha), so the pref has to reach the build.
    # setdefault: an explicit user override wins.
    if headless and sys.platform in ("win32", "darwin"):
        for key, value in cloak_prefs().items():
            prefs.setdefault(key, value)
    # The namespace MUST be stealthfox.* - that is what the binary's Juggler
    # reads, and it gates its own mouse-path expansion on `stealthfox.humanize`.
    # An earlier `invisible_playwright.*` spelling was a dead no-op, so humanize
    # never fired and every click teleported the cursor.
    #
    # The pref selects WHICH generator runs, not whether motion happens. While
    # the wrapper draws the path it must be false, or each waypoint we send would
    # itself be expanded into a whole path by the browser.
    prefs.update(_humanize_prefs(cursor_engine, humanize))
    return prefs
