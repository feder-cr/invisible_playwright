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


@pytest.mark.e2e
def test_it_CENTRES_rather_than_scrolling_the_minimum(firefox_binary):
    """⛔ A NAMED DIVERGENCE FROM PLAYWRIGHT, asserted so it cannot drift.

    Upstream's `scrollIntoViewIfNeeded` scrolls the least it can and leaves a
    page alone when the element is already visible. This one always centres,
    because it reuses the helper the CLICK path uses, and that helper centres
    deliberately: an element flush against the top edge sits under the sticky
    header a great many sites paint there, and the hit-target check then
    correctly reports that the event landed on something else.

    Writing a second helper to get the "if needed" half would put one concept
    in two places and let the two scroll behaviours drift apart - which is the
    defect this whole fix removes. So the divergence stays, and this test is
    where it is written down in a form that runs: implement true "if needed"
    some day and this goes red, which is the only way anybody reads the note.

    The page below has room to scroll and the element starts visible, so the
    two behaviours give different answers. On a page with nowhere to scroll
    they agree, which is why that case cannot be the one asserted here.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.set_content(
                "<div id=x style='width:120px;height:40px'>target</div>"
                "<div style='height:3000px'></div>")
            assert page.evaluate("window.scrollY") == 0
            assert page.eval_on_selector("#x", IS_IN_VIEWPORT), (
                "the element starts out of view: the test proves nothing")
            page.locator("#x").scroll_into_view_if_needed(timeout=5000)
            assert page.evaluate("window.scrollY") == 0, (
                "an element at the very top of a scrollable page cannot be "
                "centred any further, so the page must not have moved")

            # And the same element sitting LOW in the viewport but still inside
            # it: now there is room above to scroll into, so centring moves the
            # page while "if needed" would have left it alone. `90vh` rather
            # than a pixel count, because the viewport height is a property of
            # the profile and this must hold whatever it is.
            page.set_content(
                "<div style='height:90vh'></div>"
                "<div id=x style='width:120px;height:40px'>target</div>"
                "<div style='height:3000px'></div>")
            assert page.eval_on_selector("#x", IS_IN_VIEWPORT), (
                "the element starts out of view: the test proves nothing")
            page.locator("#x").scroll_into_view_if_needed(timeout=5000)
            assert page.evaluate("window.scrollY") > 0, (
                "the helper is expected to centre a visible element, and the "
                "page did not move at all")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
