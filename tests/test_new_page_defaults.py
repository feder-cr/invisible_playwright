"""`browser.new_page()` must give the same context as `browser.new_context()`.

The wrapper installs the profile's viewport, screen, DPR, colour scheme, locale
and timezone as defaults on `browser.new_context`. `new_page` did not go through
it, and `new_page` is the call in this package's README, in the class docstring
and in every example:

    with InvisiblePlaywright(seed=42) as browser:
        page = browser.new_page()

Playwright's `Browser.new_page` forwards to the IMPLEMENTATION object, whose own
`new_page` does `context = await self.new_context(**params)` - itself, the impl -
so a wrapper installed on the api object is never consulted. The page came up
with Playwright's stock 1280x720 viewport, the host's colour scheme and no locale
or timezone override, while the fingerprint claimed the profile's screen. A
viewport that contradicts `screen.width` is not a missing feature; it is the kind
of internal inconsistency this package exists to remove.

The e2e test at the bottom is the one that proves it in a browser. The unit tests
above it are what keeps CI honest without one.
"""
from __future__ import annotations

import asyncio

import pytest

from invisible_playwright import InvisiblePlaywright
from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright


class _StubContext:
    def __init__(self):
        self.cookies = []

    def new_page(self, **kw):
        return _StubPage(self)

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)


class _StubPage:
    def __init__(self, ctx):
        self.context = ctx


class _StubBrowser:
    """Records what each entry point was ultimately called with."""

    def __init__(self):
        self.context_kwargs = []
        self.page_kwargs = []

    def new_context(self, **kw):
        self.context_kwargs.append(kw)
        return _StubContext()

    def new_page(self, **kw):
        self.page_kwargs.append(kw)
        return _StubPage(_StubContext())


class _AsyncStubBrowser(_StubBrowser):
    async def new_context(self, **kw):          # type: ignore[override]
        self.context_kwargs.append(kw)
        return _AsyncStubContext()

    async def new_page(self, **kw):             # type: ignore[override]
        self.page_kwargs.append(kw)
        return _StubPage(_AsyncStubContext())


class _AsyncStubContext(_StubContext):
    async def new_page(self, **kw):             # type: ignore[override]
        return _StubPage(self)

    async def add_cookies(self, cookies):       # type: ignore[override]
        self.cookies.extend(cookies)


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    """The 0.4s about:newtab settle, out of the unit tests.

    Not removed from the code path: `_NEWTAB_SETTLE` is asserted separately.
    """
    import invisible_playwright.launcher as launcher

    monkeypatch.setattr(launcher._time, "sleep", lambda _s: None)


@pytest.mark.unit
def test_new_page_gets_the_same_defaults_as_new_context():
    ip = InvisiblePlaywright(seed=42)
    browser = _StubBrowser()
    ip._patch_new_context_defaults(browser)

    browser.new_page()
    browser.new_context()

    assert browser.page_kwargs, "browser.new_page was never wrapped"
    assert browser.page_kwargs[0] == browser.context_kwargs[0], (
        "the two entry points disagree about the session's own context")
    assert browser.page_kwargs[0] == ip._default_context_kwargs()


@pytest.mark.unit
def test_explicit_kwargs_still_win_on_new_page():
    """Same rule as new_context: the defaults are defaults."""
    ip = InvisiblePlaywright(seed=42)
    browser = _StubBrowser()
    ip._patch_new_context_defaults(browser)

    browser.new_page(viewport={"width": 800, "height": 600}, locale="fr-FR")

    assert browser.page_kwargs[0]["viewport"] == {"width": 800, "height": 600}
    assert browser.page_kwargs[0]["locale"] == "fr-FR"
    # and the rest of the profile still arrived
    assert browser.page_kwargs[0]["screen"] == ip._default_context_kwargs()["screen"]


@pytest.mark.unit
def test_the_async_api_wraps_new_page_too():
    """The two APIs drifted apart once already - `_bind_process_tree` existed
    only on the sync one and the Windows leak survived a release on this side."""
    ip = AsyncInvisiblePlaywright(seed=42)
    browser = _AsyncStubBrowser()
    ip._patch_new_context_defaults(browser)

    async def drive():
        await browser.new_page()
        await browser.new_context()

    asyncio.run(drive())

    assert browser.page_kwargs, "async browser.new_page was never wrapped"
    assert browser.page_kwargs[0] == browser.context_kwargs[0]
    assert browser.page_kwargs[0] == ip._default_context_kwargs()


@pytest.mark.unit
def test_the_settle_is_one_value_shared_by_both_apis():
    """The about:newtab race needs the same wait wherever a tab is created, and
    a second literal is a second value."""
    import invisible_playwright.async_api as async_api
    import invisible_playwright.launcher as launcher

    assert launcher._NEWTAB_SETTLE == 0.4
    assert async_api._NEWTAB_SETTLE is launcher._NEWTAB_SETTLE
    assert "0.4" not in async_api._patch_new_page_sleep.__code__.co_consts.__str__()


@pytest.mark.e2e
def test_new_page_and_new_context_report_the_same_environment(firefox_binary):
    """The claim, in a browser. Nothing here is readable from the wiring: these
    are the values the PAGE sees, which is what a detector reads."""
    probe = """() => ({
        innerWidth: window.innerWidth,
        screenWidth: window.screen.width,
        screenHeight: window.screen.height,
        dpr: window.devicePixelRatio,
        language: navigator.language,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
    })"""

    # dark_theme pinned on purpose: `color_scheme` travels in the same kwargs
    # dict as the viewport, and a light profile on a light host would agree by
    # accident. The viewport is the difference that shows up for every profile
    # (1280 vs 1906 when this was measured); this makes a second one visible.
    with InvisiblePlaywright(seed=42, headless=True,
                             binary_path=str(firefox_binary),
                             pin={"dark_theme": True},
                             locale="en-US", timezone="America/New_York") as browser:
        direct = browser.new_page().evaluate(probe)
        viacontext = browser.new_context().new_page().evaluate(probe)

    assert direct == viacontext, (
        "browser.new_page() and browser.new_context().new_page() gave the page "
        f"two different environments:\n  new_page:    {direct}\n"
        f"  new_context: {viacontext}")
    # And it is the PROFILE's environment, not Playwright's default: a test that
    # only compared the two would pass if both regressed together.
    assert direct["timezone"] == "America/New_York"
    assert direct["language"] == "en-US"
    assert direct["innerWidth"] != 1280, "the stock Playwright viewport is back"
    assert direct["dark"] is True, "the pinned dark profile did not reach the page"
