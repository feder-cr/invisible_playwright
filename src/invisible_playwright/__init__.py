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

# ── There is no driver any more ──────────────────────────────────────────────
# ⛔ REMOVED ON 2026-08-28, and with it the last reason this package needed
# Node. What used to sit here was a note explaining that the vendored client in
# _pw/ pointed at the forked driver in _driver/ by construction. Both are gone:
# _driver/ (6 MB of JavaScript), _node.py (which downloaded a 92 MB node.exe on
# first use), _pw/_impl/_driver.py and PipeTransport.
#
# What answers the client now is the in-process Python server in _juggler/,
# which speaks the same protocol. It became the default on the same day and on
# evidence rather than on the code looking finished: 188 e2e passed on BOTH
# transports, protocol parity on methods, parameter names, object types,
# initializer fields, events and parentage, and the realness gates green on the
# Python path for the first time.
#
# ⛔ A first install no longer downloads anything but the browser.

from invisible_core import get_default_args, get_default_stealth_prefs
from invisible_core import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION
from invisible_core import GeoTimezoneError, resolve_session_timezone
from invisible_core import ensure_binary, ensure_geoip_mmdb
from .launcher import InvisiblePlaywright

# `__version__` describes the CODE that is about to run; the install record is
# a different fact and keeps a name that says so, the way `invisible_core` does.
# ⛔ It used to be `importlib.metadata.version("invisible-playwright")`, which
# describes the INSTALL: an editable one freezes that number and the code keeps
# moving, measured here at 0.16.2 against a tree declaring 0.22.1 with the
# checkout up to date. See `_version.py`.
from ._version import __install_record_version__, __version__

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
    "__install_record_version__",
]
