"""stealthfox — Playwright wrapper for a patched Firefox with stealth profile.

Quickstart:

    from stealthfox import Stealthfox

    with Stealthfox() as browser:        # random seed
        page = browser.new_page()
        page.goto("https://example.com")

    with Stealthfox(seed=42) as browser: # deterministic
        ...

    with Stealthfox(humanize=True) as browser:  # human-like cursor motion
        page = browser.new_page()
        page.click("#submit")   # expanded into a Bezier trajectory
"""
from .launcher import Stealthfox
from .constants import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION

__version__ = "0.1.0"
__all__ = ["Stealthfox", "BINARY_VERSION", "FIREFOX_UPSTREAM_VERSION", "__version__"]
