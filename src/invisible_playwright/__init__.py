"""invisible_playwright - Playwright wrapper for a patched Firefox with stealth profile.

Quickstart:

    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright() as browser:        # random seed
        page = browser.new_page()
        page.goto("https://example.com")

    with InvisiblePlaywright(seed=42) as browser: # deterministic
        ...

    with InvisiblePlaywright(humanize=True) as browser:  # human-like cursor motion
        page = browser.new_page()
        page.click("#submit")   # the pointer travels there, it does not jump

``humanize`` is on by default and needs no new API: the ordinary Playwright
pointer calls - ``page.click`` / ``page.hover`` / ``locator.click`` /
``page.mouse.move`` - move the cursor along a path drawn from ``seed``, so the
same seed replays the same motion. ``INVPW_CURSOR_ENGINE=binary`` restores the
previous behaviour, where the browser drew the path instead.
"""
# ── Import-time core assertion, and repair ───────────────────────────────────
# Runs BEFORE every other import. Two questions, one check: is the installed
# invisible-core new enough to carry a release seal at all, and is it the exact
# version this distribution declares in pyproject? The expected version is read
# back out of our own metadata, so it is written in exactly one place.
#
# A mismatch is REPAIRED, not just reported: the declared core is installed and
# picked up in this process, so the script that just imported us keeps going.
# That is only sound here, on the first line, while invisible_core has not been
# imported by anyone yet; _pin.py captures exactly that fact before it imports
# the core, and the repair refuses itself if the snapshot says otherwise. Every
# failure of the repair falls back to the message it replaced.
#
# The comparison and the repair both live in invisible_core.pin. They were put
# there to be shared with the profile manager, so both products would diagnose
# and fix an environment the same way; that repository was deleted 2026-08-18
# and they stay in the core, which is the package that owns the pin.
# Our _pin.py is the floor: it owns the three states a module inside the core
# cannot report on (core absent, core present but unimportable, core present but
# too damaged to derive its own version) and delegates everything else.
from ._pin import enforce_core_pin as _enforce_core_pin
_enforce_core_pin()

from ._engine import assert_playwright_range as _assert_pw_range
_assert_pw_range()

from invisible_core import get_default_args, get_default_stealth_prefs
from invisible_core import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION
from invisible_core import GeoTimezoneError, resolve_session_timezone
from invisible_core import ensure_binary, ensure_geoip_mmdb
from .launcher import InvisiblePlaywright

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("invisible-playwright")
except PackageNotFoundError:
    # Editable / source checkout without an install record: fall back to a
    # marker rather than risk shipping a stale hardcoded string.
    __version__ = "0.0.0+unknown"

__all__ = [
    "InvisiblePlaywright",
    "ensure_binary",
    "ensure_geoip_mmdb",
    "get_default_stealth_prefs",
    "get_default_args",
    "resolve_session_timezone",
    "GeoTimezoneError",
    "BINARY_VERSION",
    "FIREFOX_UPSTREAM_VERSION",
    "__version__",
]
