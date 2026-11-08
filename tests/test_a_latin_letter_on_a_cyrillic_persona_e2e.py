"""A Latin letter on a persona whose keyboard has none is typed on the second
keyboard Windows installs with the language, with its key events.

⛔ KNOWN-BAD, measured on firefox-57 with 0.46.3: a session in Russian or Greek
(the runner's address decides the language) answered `press("x")` with
"Locator.press: unknown key: 'x'", and `type("cafe")` put every letter in
without a key event, which a real user never produces. A Russian user has
English (US) too, which Windows installs with Russian, and types the `x` on it
with KeyX. Where Windows installs no keyboard with Latin letters (Nepali in
India), the letter is inserted as text, as for any character no key makes.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PAGE = ("<!doctype html><input id=i><script>window.log = [];"
        "for (const t of ['keydown', 'input']) document.getElementById('i')"
        ".addEventListener(t, e => log.push([t, e.key || '', e.code || '', e.data || '']));"
        "</script>")


def _typed(firefox_binary, locale, act):
    from invisible_playwright import InvisiblePlaywright
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True,
                             locale=locale) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        act(page.locator("#i"))
        return page.input_value("#i"), page.evaluate("window.log")


def test_a_russian_persona_presses_x_on_english(firefox_binary):
    value, log = _typed(firefox_binary, "ru-RU", lambda field: field.press("x"))
    assert value == "x"
    assert [e for e in log if e[0] == "keydown"] == [["keydown", "x", "KeyX", ""]], log


def test_a_greek_persona_types_latin_text_with_its_keys(firefox_binary):
    value, log = _typed(firefox_binary, "el-GR",
                        lambda field: field.press_sequentially("cafe"))
    assert value == "cafe"
    assert [e[2] for e in log if e[0] == "keydown"] == ["KeyC", "KeyA", "KeyF", "KeyE"], log


def test_a_letter_no_default_keyboard_has_is_inserted(firefox_binary):
    value, log = _typed(firefox_binary, "ne-IN",
                        lambda field: field.press_sequentially("x"))
    assert value == "x"
    assert not [e for e in log if e[0] == "keydown"], log
