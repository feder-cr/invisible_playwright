"""The engine keyboard obeys a rhythm instead of emitting at pipe speed.

⛔ WHAT IT LOOKED LIKE BEFORE. `press` sent `keydown` and `keyup` back to back,
so a page measured a hold of about 2.4 ms - the cost of one protocol round trip
- against the 60-120 ms a finger takes. `type` did the same per character with
nothing in between, so eight letters went by in about 40 ms instead of a second
or more. No probe is needed to collect that: a site only has to listen.
"""
from __future__ import annotations

import statistics

import pytest

from invisible_playwright._behaviour import TypingPersona
from invisible_playwright._juggler.keyboard import Keyboard


class _Conn:
    def __init__(self):
        self.sent: list = []

    def send(self, method, params=None, **kw):
        self.sent.append((method, (params or {}).get("type")))
        return {}


@pytest.fixture()
def slept(monkeypatch):
    """Every sleep the keyboard asks for, in SECONDS, without taking any."""
    taken: list = []
    monkeypatch.setattr("invisible_playwright._juggler.keyboard.time.sleep",
                        taken.append)
    return taken


def _keyboard(persona=None):
    return Keyboard(_Conn(), "session", persona)


def test_without_a_persona_nothing_waits(slept):
    """Turning humanising off has to keep meaning what it meant: no rhythm,
    not a default one."""
    k = _keyboard()
    k.type("hello")
    assert slept == []


def test_a_key_is_held_for_as_long_as_a_finger_holds_it(slept):
    k = _keyboard(TypingPersona.from_seed(42))
    k.press("a")
    assert len(slept) == 1, "a press waited %d times" % len(slept)
    held_ms = slept[0] * 1000.0
    assert 20.0 < held_ms < 400.0, "held for %.1f ms" % held_ms


def test_typing_waits_between_every_pair_and_inside_every_key(slept):
    """N dwells and N-1 gaps: the pause after the last key belongs to whatever
    happens next, not to typing."""
    k = _keyboard(TypingPersona.from_seed(42))
    k.type("hello")
    assert len(slept) == 5 + 4
    total_ms = sum(slept) * 1000.0
    assert 250.0 < total_ms < 6000.0, "five letters took %.0f ms" % total_ms


def test_the_delay_a_caller_passes_is_MILLISECONDS(slept):
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE, and it was a live defect.

    The public API documents `delay` in milliseconds and the one server path
    that forwarded it handed the number straight to `time.sleep`, which takes
    SECONDS. `type("abc", delay=100)` therefore waited five minutes instead of
    0.3 seconds. The six paths that dropped the value were accidentally
    protected from the bug the seventh had.

    To watch this fail, divide by one instead of by a thousand.
    """
    k = _keyboard(TypingPersona.from_seed(42))
    k.type("abc", delay_ms=120.0)
    gaps = [s for s in slept if abs(s - 0.120) < 1e-9]
    assert len(gaps) == 2, "the caller's gap appeared %d times" % len(gaps)
    assert sum(slept) < 2.0, (
        "three characters took %.1f seconds: the unit is wrong" % sum(slept))


def test_a_caller_who_asks_for_nothing_gets_the_session_hand(slept):
    """The override is an override. Before, the parameter WAS the defence, so
    every install that passed the same number shared one flat rhythm."""
    k = _keyboard(TypingPersona.from_seed(42))
    k.type("abc")
    gaps = slept[1::2]
    assert len(set(gaps)) == len(gaps), "the gaps are all the same value"


def test_a_press_dwell_can_be_overridden_and_is_also_milliseconds(slept):
    k = _keyboard(TypingPersona.from_seed(42))
    k.press("a", dwell_ms=250.0)
    assert slept == [0.250]


def test_two_words_are_not_typed_with_the_same_intervals(slept):
    """⛔ One stream per keyboard, not one per call. Without the nonce a page
    could match the sequence of gaps between two form fields and know the two
    were typed by the same automation."""
    k = _keyboard(TypingPersona.from_seed(42))
    k.type("hello")
    first = list(slept)
    slept.clear()
    k.type("hello")
    assert first != slept


def test_the_keys_still_come_out_in_the_right_order(slept):
    """⛔ A rhythm that broke the events would be worse than no rhythm, and a
    test that only measures waits would not notice."""
    k = _keyboard(TypingPersona.from_seed(42))
    k.type("ab")
    kinds = [t for _, t in k.c.sent]
    assert kinds == ["keydown", "keyup", "keydown", "keyup"]


def test_the_rhythm_is_the_seeds_and_not_a_constant():
    """Two sessions, two hands. Measured on the medians so one lucky draw
    cannot make it pass."""
    def medians(seed):
        taken: list = []
        k = Keyboard(_Conn(), "s", TypingPersona.from_seed(seed))
        import invisible_playwright._juggler.keyboard as mod
        real, mod.time.sleep = mod.time.sleep, taken.append
        try:
            for _ in range(40):
                k.type("the quick brown fox")
        finally:
            mod.time.sleep = real
        return statistics.median(taken)

    assert medians(1) != medians(2)


# ── with a browser ──────────────────────────────────────────────────────────

@pytest.mark.e2e
def test_a_real_page_measures_a_human_rhythm(firefox_binary):
    """The proof, and the only thing that exercises the whole chain.

    Everything above runs against doubles: it shows the keyboard obeys a plan.
    This shows the plan reaches a browser at all - launcher, prefs, op_launch,
    browser, page, actions, keyboard - and that what a page measures is a hand.

    Before the fix the dwell was the cost of one protocol round trip, about
    2.4 ms, and the gaps about 5 ms.
    """
    import urllib.parse
    from invisible_playwright import InvisiblePlaywright

    html = ("<input id='f'>"
            "<script>window.__e=[];"
            "for (const t of ['keydown','keyup'])"
            " document.getElementById('f').addEventListener(t,"
            "  e => window.__e.push([t, e.key, e.timeStamp]));</script>")
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto("data:text/html," + urllib.parse.quote(html))
        page.click("#f")
        page.type("#f", "password123")
        events = page.evaluate("window.__e")

    downs = [e for e in events if e[0] == "keydown"]
    ups = [e for e in events if e[0] == "keyup"]
    assert len(downs) == 11 and len(ups) == 11, (
        "expected eleven of each, saw %d and %d" % (len(downs), len(ups)))

    dwells = [u[2] - d[2] for d, u in zip(downs, ups)]
    gaps = [downs[i + 1][2] - ups[i][2] for i in range(len(downs) - 1)]

    median_dwell = statistics.median(dwells)
    median_gap = statistics.median(gaps)
    assert median_dwell > 20.0, (
        "median hold was %.1f ms, which is a protocol round trip and not a "
        "finger" % median_dwell)
    assert median_gap > 20.0, (
        "median gap was %.1f ms" % median_gap)
    # ⛔ And a spread, because a constant rhythm is still a tell.
    assert statistics.pstdev(gaps) > 5.0, (
        "the gaps vary by %.1f ms: that is a metronome"
        % statistics.pstdev(gaps))
