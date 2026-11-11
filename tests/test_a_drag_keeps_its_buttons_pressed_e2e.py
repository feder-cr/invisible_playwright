"""In a real browser, a drag reaches the page with the button it holds:
`event.buttons` is 1 on every `mousemove` between `mouse.down()` and
`mouse.up()` (left), 2 for the right button, and 0 on the move after the
release.

The core's unit test holds the events the engine is sent; this asks the page,
the only reader that matters. A slider or a canvas tests `event.buttons` on
`mousemove`, and stock Playwright gives them 1 on a left-button drag; the
driver once gave them 0, so the drag never followed the pointer and the zero
was a tell on its own.
"""
from __future__ import annotations

import pytest

PAGE = """<!doctype html><html><body style="margin:0;height:100vh">
<script>
window.log = [];
for (const t of ['mousedown', 'mousemove', 'mouseup'])
    document.addEventListener(t, e => window.log.push([t, e.buttons]));
document.addEventListener('contextmenu', e => e.preventDefault());
</script></body></html>"""


def _drag(page, button):
    page.mouse.move(100, 100)
    page.mouse.down(button=button)
    page.mouse.move(150, 120)
    page.mouse.move(220, 180, steps=5)
    page.mouse.move(300, 200)
    page.mouse.up(button=button)
    page.mouse.move(320, 210)
    return page.evaluate("window.log")


def _check(log, held):
    kinds = [t for t, _ in log]
    down, up = kinds.index("mousedown"), kinds.index("mouseup")
    during = [b for t, b in log[down + 1:up] if t == "mousemove"]
    after = [b for t, b in log[up + 1:] if t == "mousemove"]
    assert len(during) >= 7, log  # 1 + 5 + 1 at least, a walk adds more
    assert set(during) == {held}, ("moves during the drag said buttons=%s, "
                                   "expected %s: %r" % (sorted(set(during)), held, log))
    assert log[down] == ["mousedown", held], log
    assert log[up] == ["mouseup", 0], log
    assert after and set(after) == {0}, log


@pytest.mark.e2e
@pytest.mark.parametrize("humanize", [True, False])
@pytest.mark.parametrize("button,held", [("left", 1), ("right", 2)])
def test_a_drag_moves_with_the_button_held(firefox_binary, button, held, humanize):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4261, binary_path=firefox_binary, headless=True,
                             humanize=humanize) as browser:
        page = browser.new_context().new_page()
        page.set_content(PAGE)
        _check(_drag(page, button), held)
