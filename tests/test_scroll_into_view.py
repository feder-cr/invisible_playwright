"""`scroll_into_view_if_needed()` brings the element into view. [B184]

⛔ THIS METHOD NEVER WORKED, AND NO TEST SAID SO. Measured 2026-08-27 on the
shipped binary at four positions - including an element ALREADY IN VIEW - it
timed out every single time, while `bounding_box()` on the same element answered
with a correct box. The cause is in the engine: `Page.scrollIntoViewIfNeeded`
calls `unsafeObject.scrollRectIntoViewIfNeeded`, which is declared in no binding
of `Element` anywhere in the tree, so the `else` branch throws for every caller.

⛔ THE FIX IS NOT IN THE ENGINE. `Actions` has scrolled before every click since
a click below the fold was found to miss, using `InjectedScript.scroll_into_view`
- the native `scrollIntoView` reached from the utility world. So the package
already knew how to scroll; only the public method sent the command that cannot
work. The dispatcher now calls the same helper, which is why this file can exist.

The four positions below are the four rows of the bug's own table, so a reader
can compare them to what was measured. Each asserts that the element ENDS UP
inside the viewport, not merely that the call returned: a method that silently
does nothing would pass a "did not raise" test, and that is exactly the shape
being replaced here.
"""
from __future__ import annotations

import os

import pytest

from invisible_playwright._juggler import transport as factory

IN_VIEW = "<div id=x style='width:120px;height:40px'>target</div>"
FAR_BELOW = ("<div style='height:3000px'></div>"
             "<div id=x style='width:120px;height:40px'>target</div>")
JUST_BELOW = ("<div style='height:400px'></div>"
              "<div id=x style='width:120px;height:40px'>target</div>")
INSIDE_A_SCROLLER = ("<div style='height:200px;overflow:auto'>"
                     "<div style='height:900px'></div>"
                     "<div id=x style='width:120px;height:40px'>target</div>"
                     "</div>")

IS_IN_VIEWPORT = """el => {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight
        && r.right > 0 && r.left < window.innerWidth;
}"""


@pytest.mark.e2e
@pytest.mark.parametrize("html", [IN_VIEW, FAR_BELOW, JUST_BELOW,
                                  INSIDE_A_SCROLLER],
                         ids=["already in view", "3000px below the fold",
                              "400px below the fold", "inside a scroller"])
def test_scroll_into_view_puts_the_element_in_the_viewport(firefox_binary, html):
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.set_content(html)
            # ⛔ A SHORT TIMEOUT ON PURPOSE. The defect this covers was a 30 s
            # timeout, so the default would let a regression come back as a
            # slow pass on a loaded machine instead of a red test.
            page.locator("#x").scroll_into_view_if_needed(timeout=5000)
            assert page.eval_on_selector("#x", IS_IN_VIEWPORT), (
                "the call returned and the element is still out of view")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)


IS_IN_FULL_VIEW = """el => {
    const r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= window.innerHeight
        && r.left >= 0 && r.right <= window.innerWidth;
}"""

CENTRE_OFFSET = """el => {
    const r = el.getBoundingClientRect();
    return Math.abs((r.top + r.bottom) / 2 - window.innerHeight / 2);
}"""


@pytest.mark.e2e
def test_it_scrolls_ONLY_IF_NEEDED_and_then_centres(firefox_binary):
    """What the method does with the page: nothing for an element in full view,
    and centring for one that is not.

    ⛔ IT CENTRED EVERY TIME, and this test used to assert that. The core helper
    is called before every click as well, so a click on a button in plain view
    moved the page under it, which neither Chromium's `scrollIntoViewIfNeeded`
    nor a person does. Since core 54.48.0 the helper leaves an element in full
    view where it is, and centres only one that is not (the centre keeps it
    clear of a sticky header along the top edge).

    Both halves run on the same page, where centring and "the least" give
    different answers: the element sits LOW in the viewport, so a helper that
    still centred would move the page in the first half, and one that scrolled
    the minimum would leave it on the bottom edge in the second.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            # `80vh` rather than a pixel count: the viewport height is a
            # property of the profile, and this must hold whatever it is.
            page.set_content(
                "<div style='height:80vh'></div>"
                "<div id=x style='width:120px;height:40px'>target</div>"
                "<div style='height:3000px'></div>")
            assert page.eval_on_selector("#x", IS_IN_FULL_VIEW), (
                "the element starts partly out of view: the test proves nothing")
            page.locator("#x").scroll_into_view_if_needed(timeout=5000)
            assert page.evaluate("window.scrollY") == 0, (
                "the element was in full view, and the page moved anyway")

            # Half of it below the bottom edge: now it is needed.
            page.set_content(
                "<div style='height:calc(100vh - 20px)'></div>"
                "<div id=x style='width:120px;height:40px'>target</div>"
                "<div style='height:3000px'></div>")
            assert not page.eval_on_selector("#x", IS_IN_FULL_VIEW), (
                "the element starts in full view: the test proves nothing")
            page.locator("#x").scroll_into_view_if_needed(timeout=5000)
            assert page.evaluate("window.scrollY") > 0, (
                "the element was cut by the bottom edge, and the page did not move")
            assert page.eval_on_selector("#x", CENTRE_OFFSET) <= 1, (
                "the page moved, but not to centre the element")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
