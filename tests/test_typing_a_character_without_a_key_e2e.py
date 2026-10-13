"""Text with a character the US layout has no key for is typed as
Playwright types it: the keys that exist are pressed, the character is
inserted in its place.

Until invisible-core 44.39.0 the core's keyboard refused the whole text, so
`page.type("#i", "café")` and `page.keyboard.type("café")` raised "'é' has no
key on the US layout" (measured on firefox-44 with 0.35.5), where Playwright
types it.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PAGE = ("<!doctype html><input id=i><input id=j><script>window.log = [];"
        "for (const id of ['i', 'j']) for (const t of ['keydown', 'input'])"
        " document.getElementById(id).addEventListener(t, e => log.push("
        "[id, t, e.key || '', e.data || '']));</script>")


def test_text_with_a_character_without_a_key_is_typed(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        page.type("#i", "café")
        page.focus("#j")
        page.keyboard.type("été")
        assert page.input_value("#i") == "café"
        assert page.input_value("#j") == "été"
        log = page.evaluate("window.log")
    # The keys that exist are pressed, and only those: no key event is faked
    # for the accented letter, which arrives as input. (Firefox commits an
    # inserted text as a composition, two `input` events for one insertion,
    # the same on Playwright's Firefox.)
    keys = [(f, k) for f, t, k, _ in log if t == "keydown"]
    assert keys == [("i", "c"), ("i", "a"), ("i", "f"), ("j", "t")], log
    inserted = {f for f, t, _, d in log if t == "input" and d == "é"}
    assert inserted == {"i", "j"}, log
