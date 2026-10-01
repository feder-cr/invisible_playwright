"""Trusted `input`/`change` for controls inside an open shadow root.

`select_option`, and `fill` on a field whose value is SET rather than typed
(`date`, `color`, `range`...), ask the engine for the events with
`Page.dispatchTrustedInputEvents`.
Up to firefox-34 the engine delivered them through
`nsIDOMWindowUtils.dispatchDOMEventViaPresShellForTesting`, which takes the
target's uncomposed document: null for any node inside a shadow root, so every
`<select>` or field in a web component failed with NS_ERROR_UNEXPECTED. Found
on the State field of a State Farm contact form (an `sf-select` element).

The fix is in the engine (juggler `PageAgent.js`): the events are dispatched
from chrome straight at the element, which Gecko marks trusted, and with the
init dict Firefox itself uses. The `select_option`, set-value and flag tests need an
engine carrying it: point `INVPW_BINARY_PATH` at one. On firefox-34 the shadow-root
cases fail with the error above and the flag case fails on `cancelable`.

`fill('')` used the same request and never emptied the field at all: the
injected script only selects the text. It now presses `Delete`, as Playwright
does, which needs nothing from the engine and works on firefox-34.

The page records what a listener can see, from inside the shadow root and from
the document: an untrusted event here would be the [B175] tell.
"""
from __future__ import annotations

import pytest

PAGE = """<!doctype html>
<select id="light"><option value="">-</option><option value="TX">Texas</option></select>
<input id="light-input" value="abc">
<input id="light-file" type="file">
<script>
customElements.define('sf-select', class extends HTMLElement {
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
<sf-select id="state"></sf-select>
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

#: What Firefox 151 itself fires when a user changes a select or a field,
#: measured with keyboard input on this engine: both bubble, neither is
#: cancelable, only `input` is composed (HTML spec says the same).
NATIVE = {"input": {"bubbles": True, "cancelable": False, "composed": True},
          "change": {"bubbles": True, "cancelable": False, "composed": False}}


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


def _assert_native(events):
    for e in events:
        assert e["trusted"] is True, e
        assert e["cls"] == "Event", e
        flags = {k: e[k] for k in ("bubbles", "cancelable", "composed")}
        assert flags == NATIVE[e["type"]], e


def _assert_shadow_delivery(page, target):
    inside = _seen(page, "shadow")
    assert [(e["type"], e["target"]) for e in inside] == [
        ("input", target), ("change", target)]
    _assert_native(inside)
    # From the document, `input` is retargeted to the host and `change`,
    # which is not composed, never leaves the shadow root.
    outside = _seen(page, "document")
    assert [(e["type"], e["target"]) for e in outside] == [("input", "state")]
    _assert_native(outside)


@pytest.mark.e2e
@pytest.mark.parametrize("how", ["value", "label"])
def test_select_option_inside_open_shadow_root(page, how):
    selected = page.select_option("#state >> #select", **{how: "TX" if how == "value" else "Texas"})

    assert selected == ["TX"]
    assert page.eval_on_selector("#state >> #select", "el => el.value") == "TX"
    _assert_shadow_delivery(page, "select")


@pytest.mark.e2e
def test_fill_set_value_inside_open_shadow_root(page):
    page.fill("#state >> #date", "2026-09-30")

    assert page.eval_on_selector("#state >> #date", "el => el.value") == "2026-09-30"
    _assert_shadow_delivery(page, "date")


@pytest.mark.e2e
@pytest.mark.parametrize("selector", ["#light-input", "#state >> #input"])
def test_fill_empty_clears_with_a_keystroke(page, selector):
    page.fill(selector, "")

    assert page.eval_on_selector(selector, "el => el.value") == ""
    events = _seen(page, "shadow" if ">>" in selector else "document")
    # What a user's Delete gives, and nothing more: `change` waits for blur.
    assert [(e["type"], e["cls"], e["inputType"], e["trusted"]) for e in events] == [
        ("input", "InputEvent", "deleteContentForward", True)]


@pytest.mark.e2e
def test_light_dom_events_carry_native_flags(page, tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("x")

    page.select_option("#light", value="TX")
    page.set_input_files("#light-file", str(sample))

    events = _seen(page, "document")
    assert [(e["type"], e["target"]) for e in events] == [
        ("input", "light"), ("change", "light"),
        ("input", "light-file"), ("change", "light-file")]
    _assert_native(events)
