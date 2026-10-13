"""A page's handler runs after its input event was born, as with a real
keyboard and mouse ([B299]).

A handler can read by how much: `event.timeStamp` against its own
`performance.now()`. Measured on retail Windows Firefox 151 with a real
keyboard and mouse: 3 to 18 ms below. Until firefox-45 ours were born at
their dispatch and the two were equal, and a tracker that keeps only events
from the past (`isTrusted && !repeat && 0 < timeStamp && timeStamp < now`)
dropped 25 keys of 30 and some of the mouse moves: measured 5 keydown of 15
kept on the Windows build, 1 of 15 on the Linux one.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PAGE = """<!doctype html><body style="margin:40px"><input id=q style="width:300px">
<script>
window.L = [];
for (const t of ['keydown', 'keyup', 'mousemove']) document.addEventListener(t, e => {
  const now = performance.now();
  L.push([t, e.timeStamp - now,
          e.isTrusted && !e.repeat && 0 < e.timeStamp && e.timeStamp < now]);
}, true);
</script>"""


def test_every_key_and_move_is_born_before_its_handler_runs(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        page.click("#q")
        page.keyboard.type("scarpe da corsa", delay=120)
        for i in range(20):
            page.mouse.move(100 + i * 7, 150 + i * 3)
        log = page.evaluate("window.L")
    for kind in ("keydown", "keyup", "mousemove"):
        rows = [r for r in log if r[0] == kind]
        assert rows, kind
        kept = [r for r in rows if r[2]]
        assert len(kept) == len(rows), (kind, rows)
        # Born a few ms before, never far in the past: the timer's precision
        # is 1 ms, the session's latency 2 to 30.
        assert all(-40.0 < r[1] < 0.0 for r in rows), (kind, rows)
