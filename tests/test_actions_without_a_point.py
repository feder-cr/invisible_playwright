"""An action that never touches the screen does not ask the element for a point.

`Actions._retry` computed a point on every turn for every action, from the
element's quad, and an element with no quad - `display:none` - answered "it
isn't visible" until the timeout. That is right for a click and wrong for the
actions whose `states` are empty, which is how each of them declares that it
needs no visibility: `set_input_files` (the commonest upload widget is a styled
button in front of a hidden `<input type=file>`), `focus`, `blur` and
`dispatch_event`. Playwright performs all four on a hidden element; here all
four timed out, 4 s out of 4, measured on firefox-34.

The unit half drives the shipped loop against an engine that has no quad for
the element; the e2e half asks a real page.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.actions import Actions, ElementNotActionable

MAIN = "frame-main"


class _Injected:
    def query_selector(self, frame, selector, **kw):
        return "element"

    def element_states(self, frame, element, states):
        return {"ok": True}

    def evaluate(self, frame, expression, **kw):
        return {"w": 1280, "h": 800}

    def call(self, frame, declaration, *args, **kw):
        return "done"

    def scroll_into_view(self, frame, element):
        return False

    def dispose(self, frame, element):
        pass


class _HiddenEngine:
    """`Page.getContentQuads` has nothing for a `display:none` element."""

    def __init__(self):
        self.sent: list = []

    def send(self, method, params=None, **kw):
        self.sent.append(method)
        if method == "Page.getContentQuads":
            return {"quads": []}
        return {}


class _Lifecycle:
    main_frame = MAIN


def _actions() -> Actions:
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected()
    actions.c = _HiddenEngine()
    actions.session = "session"
    return actions


@pytest.mark.unit
@pytest.mark.parametrize("act", [
    lambda a: a.set_input_files("#f", ["C:/x.txt"], timeout=0.3),
    lambda a: a.focus("#f", timeout=0.3),
    lambda a: a.blur("#f", timeout=0.3),
    lambda a: a.dispatch_event("#f", "click", timeout=0.3),
], ids=["set_input_files", "focus", "blur", "dispatch_event"])
def test_an_action_with_no_visibility_requirement_needs_no_quad(act):
    actions = _actions()
    act(actions)
    assert "Page.getContentQuads" not in actions.c.sent, (
        "an action that does not touch the screen asked for a point")


@pytest.mark.unit
def test_a_pointer_action_still_refuses_an_element_with_no_quad():
    """The other side of the line: a click has to land somewhere, and an
    element with no quad has nowhere to land."""
    actions = _actions()
    with pytest.raises(ElementNotActionable, match="no quad"):
        actions.click("#f", timeout=0.3)


PAGE = """<!doctype html>
<button id="b" style="display:none">hidden</button>
<input id="t" style="display:none" value="x">
<script>
window.got = [];
b.addEventListener('click', e => got.push('click'));
t.addEventListener('focus', e => got.push('focus'));
t.addEventListener('blur', e => got.push('blur'));
</script>"""


@pytest.mark.e2e
def test_hidden_elements_take_the_actions_playwright_allows(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        page.dispatch_event("#b", "click", timeout=4000)
        page.focus("#t", timeout=4000)
        page.locator("#t").blur(timeout=4000)
        # A display:none input cannot take focus, so only the dispatched
        # click reaches a listener; what matters is that none of the three
        # timed out asking a hidden element for a point.
        assert page.evaluate("got") == ["click"]
