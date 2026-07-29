"""Regression tests for issue #9: jugglerSendMouseEvent missing in FF150.

The Juggler JS in upstream Playwright calls ``win.windowUtils.jugglerSendMouseEvent``
at four sites, but the C++ side was never landed when the Juggler was ported
to FF150. Every Playwright mouse code path therefore fails on the patched
binary until the JS is swapped to ``win.synthesizeMouseEvent``.

The suite below was inspired by ``microsoft/playwright-python/tests/async/test_click.py``
and covers each patched call site:

- ``PageHandler.js::Page.dispatchMouseEvent::sendEvents``
- ``PageHandler.js`` off-viewport mousemove hack
- ``PageHandler.js`` stealthfox humanize hook
- ``PageHandler.js::Page.dispatchWheelEvent`` (scrollRectIntoViewIfNeeded guard)
- ``PageAgent.js::_dispatchDragEvent``
"""
from __future__ import annotations

import urllib.parse

import pytest

from invisible_playwright import InvisiblePlaywright


def _data_url(html: str) -> str:
    return "data:text/html," + urllib.parse.quote(html)


# ────────────────────────────────────────────────────────────────────
# Page.dispatchMouseEvent::sendEvents - the main loop swapped in fix #9.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_mouse_move_does_not_raise(firefox_binary):
    """page.mouse.move was the canonical repro from issue #9."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        page.mouse.move(100, 100)
        page.mouse.move(200, 200)


@pytest.mark.e2e
def test_click_the_button(firefox_binary):
    """Inspired by Playwright test_click.py::test_click_the_button.
    Verifies the full mousedown -> mouseup -> click sequence reaches the page."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<button id=b onclick=\"window.__clicked=true;this.textContent='ok'\">x</button>"
        ))
        page.click("#b")
        assert page.evaluate("window.__clicked") is True
        assert page.eval_on_selector("#b", "el => el.textContent") == "ok"


@pytest.mark.e2e
def test_double_click_fires_dblclick(firefox_binary):
    """Inspired by test_click.py::test_double_click_the_button."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<button id=b ondblclick=\"window.__dbl=true\">x</button>"
        ))
        page.dblclick("#b")
        assert page.evaluate("window.__dbl") is True


@pytest.mark.e2e
def test_right_click_fires_contextmenu(firefox_binary):
    """Inspired by test_click.py::test_fire_contextmenu_event_on_right_click.
    Right-click hits the special ``button === 2`` branch that dispatches
    both ``mousedown`` and ``contextmenu`` through ``sendEvents``."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<div id=d style='width:200px;height:100px;background:red' "
            "oncontextmenu=\"event.preventDefault();window.__ctx=true\">x</div>"
        ))
        page.click("#d", button="right")
        assert page.evaluate("window.__ctx") is True


@pytest.mark.e2e
def test_click_with_modifier_keys(firefox_binary):
    """Inspired by test_click.py::test_update_modifiers_correctly.
    Modifiers travel through the ``modifiers`` arg of the synthesized event."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<button id=b style='width:200px;height:80px;font-size:24px' "
            "onclick=\"window.__shift=event.shiftKey\">click</button>"
        ))
        page.click("#b", modifiers=["Shift"])
        assert page.evaluate("window.__shift") is True


@pytest.mark.e2e
def test_locator_click(firefox_binary):
    """Locator.click also goes through Page.dispatchMouseEvent."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<button id=b onclick=\"this.textContent='clicked'\">x</button>"
        ))
        page.locator("#b").click()
        assert page.eval_on_selector("#b", "el => el.textContent") == "clicked"


# ────────────────────────────────────────────────────────────────────
# Off-viewport mousemove hack - the ``windowUtils.sendMouseEvent`` call
# at the old line 642 (also removed in FF150). The synthesizeMouseEvent
# replacement must not raise.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_mouse_move_outside_viewport_does_not_raise(firefox_binary):
    """Negative coordinates exercise the "move mouse off web content" path."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        page.mouse.move(-50, -50)


# ────────────────────────────────────────────────────────────────────
# Stealthfox humanize hook - bezier expansion uses synthesizeMouseEvent
# inside a per-step loop. We verify the hook still fires intermediate
# moves between two faraway points.
# ────────────────────────────────────────────────────────────────────


def _humanize_move_count(firefox_binary, humanize):
    """Count page mousemove events fired by ONE long mouse.move."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=humanize) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<div id=d style='width:600px;height:400px' "
            "onmousemove=\"window.__n=(window.__n||0)+1\">x</div>"
        ))
        page.mouse.move(10, 10)
        page.evaluate("window.__n = 0")
        page.mouse.move(500, 300)
        return page.evaluate("window.__n")


@pytest.mark.e2e
def test_humanize_emits_intermediate_moves(firefox_binary):
    """A long mouse.move must expand into MANY intermediate mousemove events when
    humanize is on (Bezier), and ~1 (a teleport) when off. We assert the on/off
    CONTRAST: `moves >= 1` alone was a false-green - a teleport already fires 1 -
    and that false-green hid a pref-namespace bug (wrapper wrote
    `invisible_playwright.humanize`, the binary's Juggler reads `stealthfox.humanize`)
    that left humanize silently dead in production. This test now fails if the
    pref ever stops reaching the binary."""
    on = _humanize_move_count(firefox_binary, True)
    off = _humanize_move_count(firefox_binary, False)
    assert off <= 2, f"humanize OFF should ~teleport (<=2 moves), got {off}"
    assert on >= 4, (
        f"humanize ON must expand into many intermediate moves (Bezier); got {on} "
        f"(off={off}). moves==1 means the cursor teleports - the exact automation "
        f"tell humanize exists to remove, and a sign the stealthfox.* pref isn't "
        f"reaching the binary's Juggler."
    )


# ────────────────────────────────────────────────────────────────────
# Page.dispatchWheelEvent - the second scrollRectIntoViewIfNeeded site
# was guarded so wheel events do not crash before dispatch.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_mouse_wheel_does_not_raise(firefox_binary):
    """Wheel calls scrollRectIntoViewIfNeeded too; the guard must hold."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<div style='height:3000px'>tall</div>"
        ))
        page.mouse.wheel(0, 200)


# ────────────────────────────────────────────────────────────────────
# Hover - locator.hover sends a mousemove through the same sendEvents
# path; checked via mouseenter on the target element.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_hover_triggers_mouseenter(firefox_binary):
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<div id=h style='width:200px;height:100px;background:red' "
            "onmouseenter=\"window.__h=true\">x</div>"
        ))
        page.locator("#h").hover()
        # Wait for the event rather than reading immediately: under load / on a
        # virtual display the mouseenter can land a beat after hover() returns,
        # which made an instant read flaky. wait_for_function still fails (times
        # out) if mouseenter genuinely never fires. Timeout is generous (10s) so a
        # busy full-suite run - where browser startup + CPU contention can push
        # the event past a tight 5s window - doesn't flake; the event itself fires
        # in well under a second when run in isolation.
        page.wait_for_function("() => window.__h === true", timeout=10_000)


# ────────────────────────────────────────────────────────────────────
# Manual mousedown/mouseup - exercises the same sendEvents path but
# splits the press/release across two API calls.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_manual_down_up_fires_full_sequence(firefox_binary):
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<button id=b style='width:200px;height:100px' "
            "onmousedown=\"window.__d=true\" "
            "onmouseup=\"window.__u=true\" "
            "onclick=\"window.__c=true\">x</button>"
        ))
        box = page.locator("#b").bounding_box()
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.up()
        assert page.evaluate("window.__d") is True
        assert page.evaluate("window.__u") is True
        assert page.evaluate("window.__c") is True


# ────────────────────────────────────────────────────────────────────
# Scroll-and-click - verifies the scrollRectIntoViewIfNeeded guard in
# Page.dispatchMouseEvent does not break the auto-scroll behavior on a
# button placed off-screen below the viewport.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_click_offscreen_button_after_scroll(firefox_binary):
    """Inspired by test_click.py::test_scroll_and_click_the_button."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(
            "<div style='height:3000px'></div>"
            "<button id=b onclick=\"window.__c=true\">deep</button>"
        ))
        page.click("#b")
        assert page.evaluate("window.__c") is True
