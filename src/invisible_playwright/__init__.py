"""invisible_playwright — Playwright wrapper for a patched Firefox with stealth profile.

Quickstart:

    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright() as browser:        # random seed
        page = browser.new_page()
        page.goto("https://example.com")

    with InvisiblePlaywright(seed=42) as browser: # deterministic
        ...

    with InvisiblePlaywright(humanize=True) as browser:  # human-like cursor motion
        page = browser.new_page()
        page.click("#submit")   # expanded into a Bezier trajectory
"""
from .config import get_default_args, get_default_stealth_prefs
from .constants import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION
from .download import ensure_binary
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
    "get_default_stealth_prefs",
    "get_default_args",
    "BINARY_VERSION",
    "FIREFOX_UPSTREAM_VERSION",
    "__version__",
]
