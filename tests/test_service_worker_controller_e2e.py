"""A page driven by the wrapper is controlled by its service worker, as in
retail Firefox. Its own module: it opens a fresh browser per case, and the sync
API allows one Playwright per thread, so it cannot share a module with
test_service_worker.py's module-scoped page."""
from __future__ import annotations

import pytest

from invisible_playwright import InvisiblePlaywright

from test_service_worker import fixture_server  # noqa: F401 - the same SW origin


@pytest.mark.e2e
@pytest.mark.parametrize("routed", [False, True])
def test_a_reloaded_page_is_controlled_by_its_service_worker(firefox_binary, fixture_server, routed):
    """Known-bad, measured on firefox-35 (B255): with the wrapper driving, no
    page was ever controlled by its service worker - `controller` stayed null
    after the reload, with or without a route, where retail Firefox and the
    same binary launched by hand report the worker. A worker that lets a
    request through reset the interception, Juggler resumed it, and resuming
    cleared the controller. A page can read that in one line."""
    with InvisiblePlaywright(seed=43, binary_path=firefox_binary, headless=True) as browser:
        ctx = browser.new_context()
        if routed:
            ctx.route("**/*", lambda route: route.continue_())
        page = ctx.new_page()
        page.goto(f"{fixture_server}/", timeout=15_000)
        page.wait_for_function("window.__swState === 'registered'", timeout=10_000)
        page.evaluate("navigator.serviceWorker.ready.then(() => true)")
        page.reload(timeout=15_000)
        page.wait_for_timeout(500)
        assert page.evaluate("navigator.serviceWorker.controller !== null"), (
            "the reloaded page is not controlled by its service worker")
        assert page.evaluate("fetch('/from-sw').then(r => r.text())") == "hello from SW"
