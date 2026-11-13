"""``Control+A`` selects all in an <input> and a <textarea>, in-process and served to the stock Playwright client, as it does on the stock Firefox.

Measured on the published wrapper 0.50.1 and on the engine of the giro 7 (Docker Linux, a field with 11 characters, focus, the selection collapsed
before each press): ``press("Control+A")``, ``press("ControlOrMeta+A")``, ``down("Control")`` + ``press("A")`` + ``up("Control")`` and
``locator.press("Control+A")`` left ``selectionStart == selectionEnd == 0``; ``press("Control+a")`` selected all (0, 11); the stock Firefox of
Playwright selected all with every one of them. Cause: the keyboard of invisible_core held Shift for the capital ``A`` (a character that only
exists with Shift), so the browser got Control+Shift+A, which is not the select-all command (juggler/keyboard.py, ``_shortcut_letter``).

What a page reads here is only ``selectionStart`` and ``selectionEnd`` after the shortcut: the same on every machine.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

_HTML = '<html><body><input id="i" value="hello world"><br><textarea id="t">hello world</textarea></body></html>'
_LENGTH = len("hello world")

_VARIANTS = ["Control+A", "Control+a", "ControlOrMeta+A", "separate", "locator.press"]


@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("field", ["i", "t"])
def test_control_a_selects_all_in_the_field(browser, field, variant):
    page = browser.new_page()
    try:
        page.set_content(_HTML)
        page.evaluate("(id) => { const e = document.getElementById(id); e.focus(); e.setSelectionRange(0, 0); }", field)
        if variant == "separate":
            page.keyboard.down("Control")
            page.keyboard.press("A")
            page.keyboard.up("Control")
        elif variant == "locator.press":
            page.locator("#" + field).press("Control+A")
        else:
            page.keyboard.press(variant)
        got = page.evaluate(
            "(id) => { const e = document.getElementById(id); return [e.selectionStart, e.selectionEnd, document.activeElement.id]; }",
            field)
    finally:
        page.close()
    assert got == [0, _LENGTH, field], (
        "%s in #%s: selection %r, expected (0, %d) in the same field" % (variant, field, got, _LENGTH))
