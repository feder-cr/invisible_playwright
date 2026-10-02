"""Trusted `input`/`change` for form controls, in the document and inside an
open shadow root.

`fill('')` (and `clear()`, which is `fill('')`) used to ask the engine for bare
`input`/`change` events after the injected script had only SELECTED the text:
the field kept its value while the page was told it changed, a contenteditable
received a `change` no user can produce, and inside a shadow root the request
failed outright with NS_ERROR_UNEXPECTED. It now presses `Delete`, as
Playwright does: the page gets the trusted `InputEvent`
(`deleteContentForward`) a user's Ctrl+A, Delete gives, and `change` waits for
blur as it does for a user. That needs nothing from the engine.

The page records what a listener can see, from inside the shadow root and from
the document: an untrusted event here would be the [B175] tell.
"""
from __future__ import annotations

import pytest

PAGE = """<!doctype html>
<select id="light"><option value="">-</option><option value="TX">Texas</option></select>
<input id="light-input" value="abc">
<textarea id="light-area">abc</textarea>
<div id="light-editable" contenteditable="true">abc</div>
<input id="light-file" type="file">
<script>
customElements.define('x-select', class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'}).innerHTML =
      '<select id="select"><option value="">-</option>'
      + '<option value="TX">Texas</option></select>'
      + '<input id="input" value="abc">'
      + '<input id="date" type="date">';
  }
});
</script>
<x-select id="state"></x-select>
<script>
window.seen = [];
const record = where => e => seen.push({
  where, type: e.type, target: e.target.id, trusted: e.isTrusted,
  cls: e.constructor.name, inputType: e.inputType ?? null,
  bubbles: e.bubbles, cancelable: e.cancelable, composed: e.composed,
});
for (const type of ['input', 'change']) {
  document.addEventListener(type, record('document'), true);
  state.shadowRoot.addEventListener(type, record('shadow'), true);
}
</script>"""


@pytest.fixture
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_context().new_page()
        page.set_default_timeout(5000)
        page.set_content(PAGE)
        yield page


def _seen(page, where=None):
    events = page.evaluate("seen")
    return [e for e in events if where is None or e["where"] == where]


def _content(page, selector):
    return page.eval_on_selector(
        selector, "el => el.isContentEditable ? el.textContent : el.value")


@pytest.mark.e2e
@pytest.mark.parametrize("selector", ["#light-input", "#light-area",
                                      "#light-editable", "#state >> #input"])
@pytest.mark.parametrize("how", ["fill", "clear"])
def test_fill_empty_clears_with_a_keystroke(page, selector, how):
    if how == "fill":
        page.fill(selector, "")
    else:
        page.locator(selector).clear()

    assert _content(page, selector) == ""
    events = _seen(page, "shadow" if ">>" in selector else "document")
    # What a user's Delete gives, and nothing more: `change` waits for blur,
    # and a contenteditable never fires one.
    assert [(e["type"], e["cls"], e["inputType"], e["trusted"]) for e in events] == [
        ("input", "InputEvent", "deleteContentForward", True)]
