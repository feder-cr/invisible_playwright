"""`fill` and `type` wait the typist's pause between the focus and the first key.

⛔ WHAT IT REPLACES. Both actions focused the field and pressed the first key in
the same breath, a few milliseconds apart, which no hand does. A page that
answers the focus a moment later - a store writing its stored answer back into
the input, a formatter, a field loading its suggestions - then wrote over what
was already arriving. The MCP server measured it on a real form: "30" gone a
second after it was typed, an email keeping only its last letters. It put a
pause of its own in front of `fill`, importing private names of this package to
draw it and writing the hesitation's spread a second time. The pause now lives
in the action that types, for every caller, and the spread is one field of the
persona.

The unit half drives the shipped `fill` and `type` against an engine double on
a clock the test moves; the e2e half asks a real page whose store rewrites the
field after the focus.
"""
from __future__ import annotations

import dataclasses
import http.server
import threading
import time

import pytest

import invisible_playwright
from invisible_playwright._behaviour import (
    PageActs, TypingPersona, hesitation, plan_hesitation, plan_typing,
)
from invisible_playwright._juggler import actions as actions_mod
from invisible_playwright._juggler.actions import Actions
from invisible_playwright._juggler.keyboard import Keyboard

MAIN = "frame-main"
SEED = 4242


# ── the persona ────────────────────────────────────────────────────────────

def test_the_spread_of_a_hesitation_is_a_field_of_the_persona():
    """One source for the spread. Known-bad: the literal 0.55 inside
    `plan_typing`, copied into another package to draw the pause there."""
    p = TypingPersona.from_seed(SEED)
    flat = dataclasses.replace(p, hesitation_sigma=0.0)
    assert plan_hesitation(flat, "field", 1) == pytest.approx(p.hesitation_median_ms)
    # And `plan_typing` reads the same field: with no spread, a hesitation
    # inside a word is exactly the median. The first gap is drawn the same way
    # with and without a stop, so the two differ by the stop alone.
    def first_gap(persona, rate):
        return plan_typing("ab", dataclasses.replace(persona, hesitation_rate=rate))[0][1]

    assert first_gap(flat, 1.0) - first_gap(flat, 0.0) == pytest.approx(p.hesitation_median_ms)
    assert first_gap(p, 1.0) - first_gap(p, 0.0) != pytest.approx(p.hesitation_median_ms)


def test_adding_the_spread_moved_no_other_field():
    """Drawn LAST from the persona's stream, so a seed keeps the hand it had.
    The values are the ones 0.25.8 drew for this seed before the field existed."""
    p = TypingPersona.from_seed(SEED)
    assert dataclasses.astuple(p)[:-1] == pytest.approx((
        4242, 117.29778153862051, 0.23549162197369367, 179.8827637123419,
        0.378474916075495, 0.8051275040073749, 1.2122088235761808,
        1.3267310852314942, 0.024756001244626846, 1039.740068421786))


def test_the_public_hesitation_is_the_sessions_own_and_varies_per_act():
    assert invisible_playwright.hesitation is hesitation
    assert "hesitation" in invisible_playwright.__all__
    one = [hesitation(SEED, "file-chooser", nonce=n) for n in range(1, 6)]
    assert one == [hesitation(SEED, "file-chooser", nonce=n) for n in range(1, 6)]
    assert len(set(one)) == 5
    assert one != [hesitation(7, "file-chooser", nonce=n) for n in range(1, 6)]
    assert one != [hesitation(SEED, "reading", nonce=n) for n in range(1, 6)]
    assert all(0.05 < s < 10 for s in one), one
    assert hesitation(SEED, "x", nonce=3, times=2) == pytest.approx(
        plan_hesitation(TypingPersona.from_seed(SEED), "x", 3, times=2) / 1000.0)


def test_no_seed_means_no_pause():
    """Humanising off keeps meaning no rhythm, not a default one."""
    assert hesitation(None, "file-chooser", nonce=1, times=2) == 0.0


# ── the actions, on a clock the test moves ────────────────────────────────

class _Clock:
    """`time` for the actions module: sleeping moves the clock, nothing waits."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)


class _Field:
    """The page: one field, whose value is a function of the time since the
    focus, and a log of what happened when."""

    def __init__(self, clock, value=lambda since: ""):
        self.clock = clock
        self.value = value
        self.focused_at = None
        self.log: list = []

    def call(self, frame, declaration, *args, **kw):
        if "focusNode" in declaration:
            self.focused_at = self.clock.now
            self.log.append(("focus", self.clock.now))
            return "done"
        if declaration == Actions._FIELD_TEXT_JS:
            return self.value(self.clock.now - self.focused_at)
        if "injected.fill" in declaration:
            self.log.append(("select", self.clock.now))
            return "needsinput"
        raise AssertionError("unexpected call: %s" % declaration)

    def query_selector(self, frame, selector, **kw):
        return "element"

    def element_states(self, frame, element, states):
        return {"ok": True}

    def evaluate(self, frame, expression, **kw):
        return {"w": 1280, "h": 800}

    def scroll_into_view(self, frame, element):
        return False

    def dispose(self, frame, element):
        pass


class _Engine:
    def __init__(self, field):
        self.field = field

    def send(self, method, params=None, **kw):
        if method == "Page.getContentQuads":
            q = {"p1": {"x": 10, "y": 10}, "p2": {"x": 50, "y": 10},
                 "p3": {"x": 50, "y": 30}, "p4": {"x": 10, "y": 30}}
            return {"quads": [q]}
        if method == "Page.dispatchKeyEvent" and params.get("type") == "keydown":
            self.field.log.append(("key", self.field.clock.now))
        return {}


class _Lifecycle:
    main_frame = MAIN


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(actions_mod, "time", c)
    return c


def _actions(clock, persona, value=lambda since: ""):
    field = _Field(clock, value)
    a = Actions.__new__(Actions)
    a.lifecycle = _Lifecycle()
    a.inj = field
    a.c = _Engine(field)
    a.session = "session"
    a.typing_persona = persona
    # The keyboard keeps no rhythm here, so the only time that passes on the
    # clock is the pause under test.
    a.acts = PageActs()
    a.keyboard = Keyboard(a.c, "session", None, acts=a.acts)
    return a, field


def _first(log, kind):
    return next(t for k, t in log if k == kind)


def _pause(nonce=1):
    return plan_hesitation(TypingPersona.from_seed(SEED), "field", nonce) / 1000.0


@pytest.mark.parametrize("act", [
    lambda a: a.fill("#f", "30", timeout=30),
    lambda a: a.type_text("#f", "30", timeout=30),
], ids=["fill", "type"])
def test_the_first_key_comes_a_persons_pause_after_the_focus(clock, act):
    """Known-bad, before: the first key 0 s after the focus, on this clock."""
    a, field = _actions(clock, TypingPersona.from_seed(SEED))
    act(a)
    waited = _first(field.log, "key") - _first(field.log, "focus")
    assert waited == pytest.approx(_pause(), abs=1e-6), (
        "the first key came %.3f s after the focus; this session's pause is "
        "%.3f s" % (waited, _pause()))


def test_fill_selects_what_the_field_holds_after_the_pause_not_before(clock):
    """What gets replaced is what the page left there once it answered."""
    a, field = _actions(clock, TypingPersona.from_seed(SEED))
    a.fill("#f", "30", timeout=30)
    assert _first(field.log, "select") - _first(field.log, "focus") >= _pause()


def test_the_pause_starts_again_while_the_field_changes(clock):
    """A store that writes its answer back halfway through the pause: the
    first key waits a whole pause after that write."""
    p = _pause()
    a, field = _actions(clock, TypingPersona.from_seed(SEED),
                        value=lambda since: "stored" if since >= p / 2 else "")
    a.fill("#f", "30", timeout=30)
    waited = _first(field.log, "key") - _first(field.log, "focus")
    assert waited >= p / 2 + p, "typed %.3f s after the focus" % waited
    # Seen within an eighth of the pause, as the docstring promises.
    assert waited <= p / 2 + p / 8 + p + 1e-9, "typed %.3f s after the focus" % waited


def test_a_field_that_never_settles_is_typed_when_the_action_time_is_up(clock):
    a, field = _actions(clock, TypingPersona.from_seed(SEED),
                        value=lambda since: repr(since))
    a.fill("#f", "30", timeout=4)
    waited = _first(field.log, "key") - _first(field.log, "focus")
    assert waited == pytest.approx(4.0, abs=0.01), "typed %.3f s after the focus" % waited


def test_two_fields_get_two_pauses(clock):
    a, field = _actions(clock, TypingPersona.from_seed(SEED))
    a.fill("#f", "a", timeout=30)
    a.fill("#g", "b", timeout=30)
    focus = [t for k, t in field.log if k == "focus"]
    keys = [t for k, t in field.log if k == "key"]
    assert [k - f for f, k in zip(focus, keys)] == pytest.approx([_pause(1), _pause(2)])
    assert _pause(1) != _pause(2)


def test_without_a_persona_the_first_key_follows_the_focus(clock):
    """Humanising off: no pause, and the field is not even read."""
    a, field = _actions(clock, None, value=lambda since: pytest.fail("read"))
    a.fill("#f", "30", timeout=30)
    assert _first(field.log, "key") == _first(field.log, "focus")


# ── against a real engine ──────────────────────────────────────────────────

#: A field whose store answers the focus by writing its stored answer, empty,
#: back into the input `answer` ms later - the shape the MCP server measured.
STORE_PAGE = b"""<!doctype html><html><body>
<input id="store">
<script>
window.__events = [];
for (const k of ['focus', 'keydown'])
  document.addEventListener(k, e => __events.push([k, performance.now()]), true);
const answer = +(new URLSearchParams(location.search).get('answer') || 600);
store.addEventListener('focus', () => setTimeout(() => { store.value = ''; }, answer));
</script></body></html>"""

TEXT = "someone@example.com"
ANSWER_MS = 600


@pytest.fixture(scope="module")
def store_url():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(STORE_PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/?answer=%d" % (srv.server_port, ANSWER_MS)
    srv.shutdown()


def _seed_whose_first_field_pause_is_longer_than(seconds):
    """A seed whose first pause is known, so the test asserts the mechanism
    and does not depend on a draw. Read through the PUBLIC function, with the
    act and nonce its docstring names."""
    for seed in range(1, 5000):
        if hesitation(seed, "field", nonce=1) > seconds:
            return seed, hesitation(seed, "field", nonce=1)
    raise AssertionError("no seed found")


@pytest.mark.e2e
@pytest.mark.parametrize("how", ["fill", "press_sequentially"])
def test_a_store_that_answers_the_focus_does_not_eat_the_text(firefox_binary, store_url, how):
    """Known-bad, measured on 0.25.8 before this change: the store's write at
    600 ms lands while the address is being typed, and the field keeps only
    its tail."""
    from invisible_playwright import InvisiblePlaywright

    seed, pause = _seed_whose_first_field_pause_is_longer_than(ANSWER_MS / 1000 + 0.1)
    with InvisiblePlaywright(seed=seed, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto(store_url)
        started = time.monotonic()
        getattr(page.locator("#store"), how)(TEXT)
        took = time.monotonic() - started
        value = page.input_value("#store")
        events = page.evaluate("__events")
    assert value == TEXT, "the field holds %r after %s" % (value, how)
    focus = next(t for k, t in events if k == "focus")
    first = next(t for k, t in events if k == "keydown")
    assert (first - focus) / 1000 >= ANSWER_MS / 1000, (
        "the first key came %.0f ms after the focus, before the store's "
        "answer at %d ms" % (first - focus, ANSWER_MS))
    # The pause is a pause, not a stall: one hesitation (restarted once, by
    # the store's write) on top of the typing itself.
    assert took < 2 * pause + ANSWER_MS / 1000 + 0.4 * len(TEXT), (
        "%s took %.1f s" % (how, took))
