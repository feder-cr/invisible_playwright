"""A click has a duration, and a double click has an interval.

⛔ WHAT IT LOOKED LIKE BEFORE. `mousedown` and `mouseup` left together, so what
a page measured as the hold was one protocol round trip. And the two presses of
a double click had nothing between them either - delivered as a `dblclick`
regardless, because that event is born from `clickCount` and not from the
interval, so the page saw a double click no operating system would have accepted
as one.

It is the same class the keyboard had, one device over: a press with no
duration is a press no hand made.
"""
from __future__ import annotations

import statistics

import pytest

from invisible_playwright._behaviour import PointerPersona, plan_click
from invisible_playwright._juggler.actions import Actions


class _Conn:
    def __init__(self):
        self.sent: list = []

    def send(self, method, params=None, **kw):
        p = params or {}
        self.sent.append((p.get("type"), p.get("clickCount")))
        return {}


@pytest.fixture()
def slept(monkeypatch):
    """Every wait the actions ask for, in SECONDS, without taking any."""
    taken: list = []
    monkeypatch.setattr("invisible_playwright._juggler.actions.time.sleep",
                        taken.append)
    return taken


class _Keyboard:
    """A mouse event carries whatever the keyboard is really holding, so the
    double has to answer that even when nothing is held."""

    def modifier_mask(self) -> int:
        return 0


def _actions(seed=42):
    a = Actions.__new__(Actions)
    a.c = _Conn()
    a.session = "session"
    a.keyboard = _Keyboard()
    a.position = (0.0, 0.0)
    a._click_nonce = 0
    a.pointer_persona = None if seed is None else PointerPersona.from_seed(seed)
    return a


def test_without_a_persona_a_click_still_has_no_duration(slept):
    """Turning humanising off keeps meaning what it meant."""
    a = _actions(seed=None)
    a._click_at_point((10.0, 10.0))
    assert slept == []


def test_a_click_is_held_for_as_long_as_a_finger_holds_it(slept):
    a = _actions()
    a._click_at_point((10.0, 10.0))
    assert len(slept) == 1, "a click waited %d times" % len(slept)
    held_ms = slept[0] * 1000.0
    assert 20.0 < held_ms < 400.0, "held for %.1f ms" % held_ms


def test_a_double_click_has_an_interval_between_its_two_presses(slept):
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE. To watch it fail, drop the gap from
    `_click_plan`: the two presses go out together again and the page receives a
    `dblclick` with an interval no system would accept."""
    a = _actions()
    a._click_at_point((10.0, 10.0), clicks=2)
    # dwell, gap, dwell - and no trailing pause, because the wait after a click
    # belongs to whatever happens next
    assert len(slept) == 3, "a double click waited %d times" % len(slept)
    gap_ms = slept[1] * 1000.0
    assert 30.0 < gap_ms < 600.0, "the two presses were %.1f ms apart" % gap_ms


def test_the_delay_a_caller_passes_is_MILLISECONDS(slept):
    """Playwright documents `delay` as the wait between mousedown and mouseup.
    It used to be dropped here with `button` and `modifiers`."""
    a = _actions()
    a._click_at_point((10.0, 10.0), delay_ms=150.0)
    assert slept == [0.150]


def test_two_clicks_in_a_session_are_not_the_same_length(slept):
    """One stream per Actions. Two identical durations in a row is a signature
    of its own."""
    a = _actions()
    a._click_at_point((10.0, 10.0))
    first = list(slept)
    slept.clear()
    a._click_at_point((10.0, 10.0))
    assert first != slept


def test_the_events_still_come_out_right(slept):
    """⛔ A rhythm that broke the events would be worse than no rhythm, and a
    test that only measures waits would not see it. `clickCount` grows 1 then 2,
    which is what gives birth to `dblclick`."""
    a = _actions()
    a._click_at_point((10.0, 10.0), clicks=2)
    assert a.c.sent == [("mousedown", 1), ("mouseup", 1),
                        ("mousedown", 2), ("mouseup", 2)]


def test_the_plan_is_the_seeds_and_not_a_constant():
    def median_dwell(seed):
        p = PointerPersona.from_seed(seed)
        return statistics.median(
            d for n in range(50) for d, _ in plan_click(p, 1, nonce=n))

    assert median_dwell(1) != median_dwell(2)


def test_the_last_press_owns_no_pause():
    p = PointerPersona.from_seed(42)
    assert plan_click(p, 1)[0][1] == 0.0
    assert plan_click(p, 2)[-1][1] == 0.0
    assert plan_click(p, 2)[0][1] > 0.0


# ── with a browser ──────────────────────────────────────────────────────────

@pytest.mark.e2e
def test_a_real_page_measures_a_click_with_a_duration(firefox_binary):
    """The proof, and the only thing that exercises the whole chain.

    Before the fix the hold was the cost of one protocol round trip.
    """
    import urllib.parse
    from invisible_playwright import InvisiblePlaywright

    html = ("<button id='b' style='width:200px;height:60px'>go</button>"
            "<script>window.__e=[];"
            "for (const t of ['mousedown','mouseup'])"
            " document.getElementById('b').addEventListener(t,"
            "  e => window.__e.push([t, e.timeStamp]));</script>")
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto("data:text/html," + urllib.parse.quote(html))
        for _ in range(4):
            page.click("#b")
        events = page.evaluate("window.__e")

    downs = [e for e in events if e[0] == "mousedown"]
    ups = [e for e in events if e[0] == "mouseup"]
    assert len(downs) == 4 and len(ups) == 4
    holds = [u[1] - d[1] for d, u in zip(downs, ups)]
    median = statistics.median(holds)
    assert median > 20.0, (
        "median hold was %.1f ms, which is a protocol round trip and not a "
        "finger" % median)
