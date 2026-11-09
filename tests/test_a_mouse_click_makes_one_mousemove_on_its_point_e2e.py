"""In a real browser, `page.mouse.click` on a button reaches it with ONE
`mousemove` on the click point: the page counts no two consecutive `mousemove`
with the same coordinates.

The unit test (`test_a_click_makes_one_mousemove_at_its_point`) holds the
sequence the engine is sent; this asks the page, which is the only reader that
matters. One browser, humanising on, closed at the end of the test.
"""
from __future__ import annotations

import pytest

PAGE = """<!doctype html><html><body style="margin:0">
<button id="b" style="position:absolute;left:300px;top:200px;width:200px;height:80px">b</button>
<script>
window.moves = []; window.clicks = 0;
document.addEventListener('mousemove', e => window.moves.push([e.clientX, e.clientY]));
document.getElementById('b').addEventListener('click', () => window.clicks++);
</script></body></html>"""


@pytest.mark.e2e
def test_a_humanised_mouse_click_gives_the_page_no_repeated_mousemove(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4251, binary_path=firefox_binary, headless=True,
                             humanize=True) as browser:
        page = browser.new_context().new_page()
        page.set_content(PAGE)
        page.mouse.click(400, 240)
        moves = page.evaluate("window.moves")
        assert page.evaluate("window.clicks") == 1
        assert len(moves) > 10, "no stroke reached the page: %r" % moves
        assert moves[-1] == [400, 240], moves[-3:]
        repeated = [(i, m) for i, m in enumerate(moves) if i and m == moves[i - 1]]
        assert repeated == [], "the page saw the same mousemove twice in a row: %r" % repeated


@pytest.mark.e2e
def test_a_stroke_from_standstill_does_not_open_with_the_point_it_stands_on(firefox_binary):
    """After a move that stopped on (500, 300) and a pause, the click's stroke
    begins at its first real step: the page's next `mousemove` is not (500, 300)
    again."""
    import time
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4252, binary_path=firefox_binary, headless=True,
                             humanize=True) as browser:
        page = browser.new_context().new_page()
        page.set_content(PAGE)
        page.mouse.move(500, 300)
        time.sleep(0.3)
        seen = len(page.evaluate("window.moves"))
        assert page.evaluate("window.moves")[-1] == [500, 300]
        page.mouse.click(400, 240)
        moves = page.evaluate("window.moves")
        assert len(moves) > seen + 5, moves[seen:]
        assert moves[seen] != [500, 300], "the stroke repeated the point it stood on"
