"""`context.add_init_script` added after a page exists runs once in each document, not twice.

The context-level script was registered on both sides: in the browser (`Browser.setInitScripts`, which the engine applies to every
page of the context, the ones already open included) and, for the pages already open, again in each page's own list. A page that
existed when the script was added ran it twice in every later document; a page opened afterwards ran it once, so the test that adds the
script first passed. Playwright runs a context script once in each document of each page.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PAGE = ("<!doctype html><title>t</title><script>window.__seen = JSON.stringify(window.__o || []);</script>")


def test_a_context_script_added_after_the_page_exists_runs_once(firefox_binary, tmp_path):
    from invisible_playwright import InvisiblePlaywright

    (tmp_path / "p.html").write_text(PAGE, encoding="utf-8")
    url = (tmp_path / "p.html").as_uri()
    with InvisiblePlaywright(seed=4261, binary_path=firefox_binary, headless=True) as browser:
        context = browser.new_context()
        existing = context.new_page()                       # the page exists BEFORE the script
        context.add_init_script("(window.__o = window.__o || []).push('init');")
        later = context.new_page()                          # and a page opened AFTER it
        for page in (existing, later):
            page.goto(url, wait_until="load")
            assert page.evaluate("window.__o") == ["init"], "the script ran %r" % page.evaluate("window.__o")
            page.goto(url + "?again", wait_until="load")    # the next document of the same page, too
            assert page.evaluate("window.__o") == ["init"], "the second document ran %r" % page.evaluate("window.__o")
