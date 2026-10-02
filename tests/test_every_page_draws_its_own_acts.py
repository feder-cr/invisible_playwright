"""Every page of a session draws its own pauses, keys and clicks.

⛔ WHAT IT REPLACES. The nonces the server draws the rhythm of an act with -
the pause before a field, the intervals between keys, how long a click is held,
the curve of a drag - were bare counters on each page's `Actions` and
`Keyboard`, so they started again from 1 on every new page. With one seed, the
first field of EVERY tab waited the same pause and was typed with the same
intervals, and the first click of every tab was held for the same time.
Measured on 0.25.8 with seed 106 and three tabs: a pause of 2.14-2.16 s on all
three, and keydown intervals within a few milliseconds of each other. A site
that sees two tabs of one session saw the same numbers twice.

The page's number now comes from the session (`BrowserDispatcher.acts`) and is
part of every nonce. The same seed still gives the same sequence: replaying a
session replays its acts.

The unit half builds pages the way the server does, through
`BrowserDispatcher.actions_for_page`; the e2e half reads the rhythm a real page
sees in two tabs of one session.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from invisible_playwright._behaviour import (
    TypingPersona, act_nonce, hesitation, plan_typing,
)
from invisible_playwright._juggler import actions as actions_mod
from invisible_playwright._juggler.connection import EventListeners
from invisible_playwright._juggler.server import BrowserDispatcher, Server

SEED = 106


class _Connection(EventListeners):
    def send(self, method, params=None, session=None, timeout=30):
        return {}


class _Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class _Field:
    """The utility-world reads `_reach_field` makes: a field that holds
    nothing and never changes."""

    def call(self, frame, expression, *args, **kw):
        return ""


def _browser(seed=SEED):
    return BrowserDispatcher(Server(), None, _Connection(), "151.0",
                             session_seed=seed)


def _page(browser):
    return browser.actions_for_page("session", None, _Field())


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(actions_mod, "time", c)
    return c


def _first_acts(actions, clock):
    """What a page draws for its first field, first typed string and first
    click, in that order."""
    # From zero every time, so the same pause reads as the same float.
    clock.t = 0.0
    actions._reach_field("frame", "field", deadline=1e9)
    pause = clock.t
    return {"pause": pause,
            "typing": actions.keyboard._plan("hello"),
            "click": actions._click_plan(1)}


def test_the_first_acts_of_two_pages_are_two_different_draws(clock):
    """Known-bad, before: the second page's first field, string and click
    drew exactly what the first page's did."""
    browser = _browser()
    one = _first_acts(_page(browser), clock)
    two = _first_acts(_page(browser), clock)
    assert one["pause"] != two["pause"]
    assert one["typing"] != two["typing"]
    assert one["click"] != two["click"]


def test_the_same_seed_replays_the_same_sequence_across_pages(clock):
    """Reproducibility: two sessions with one seed, the same acts in the same
    pages, the same numbers."""
    runs = []
    for _ in range(2):
        browser = _browser()
        runs.append([_first_acts(_page(browser), clock) for _ in range(3)])
    assert runs[0] == runs[1]
    assert runs[0] != [_first_acts(_page(_browser(SEED + 1)), clock)
                       for _ in range(3)]


def test_the_first_page_keeps_the_count_the_public_function_documents(clock):
    """`hesitation(seed, "field", nonce=1)` is the first field of a session,
    as its docstring says; a caller who predicts that pause still can."""
    a = _page(_browser())
    assert _first_acts(a, clock)["pause"] == pytest.approx(
        hesitation(SEED, "field", nonce=1))


def test_the_keyboard_and_the_drag_number_their_acts_on_the_page():
    """One numbering per page: the keyboard holds the page's, not one of its
    own, and the pages of one session are numbered apart."""
    browser = _browser()
    a, b = _page(browser), _page(browser)
    assert a.keyboard.acts is a.acts
    assert (a.acts.page, b.acts.page) == (0, 1)
    assert a.acts.next("drag") != b.acts.next("drag")


# -- against a real engine ---------------------------------------------------

PAGE = b"""<!doctype html><html><body>
<input id="f">
<script>
window.__ev = [];
for (const k of ['focus', 'keydown'])
  document.addEventListener(k, e => __ev.push([k, performance.now(), e.target.id || '']), true);
</script></body></html>"""

TEXT = "hello"


@pytest.fixture(scope="module")
def page_url():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_port
    srv.shutdown()


def _planned(seed, page):
    """The pause and keydown-to-keydown intervals of a page's first fill."""
    persona = TypingPersona.from_seed(seed)
    nonce = act_nonce(page, 1)
    plan = plan_typing(TEXT, persona, nonce=nonce)
    return (hesitation(seed, "field", nonce=nonce),
            [dwell + gap for dwell, gap in plan[:-1]])


def _distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


@pytest.mark.e2e
def test_two_tabs_of_one_session_are_typed_with_two_rhythms(firefox_binary, page_url):
    """Each tab's first fill matches ITS plan, not the other tab's. Before,
    the second tab replayed the first tab's plan, so it sat nearer to that.

    Judged by nearness rather than by equality: what a page measures is the
    plan plus the machine's own latency, which varies under load."""
    from invisible_playwright import InvisiblePlaywright

    plans = [_planned(SEED, 0), _planned(SEED, 1)]
    # The seed is one whose two tabs differ by more than the latency can hide.
    assert abs(plans[0][0] - plans[1][0]) > 0.5
    assert _distance(plans[0][1], plans[1][1]) > 150

    seen = []
    with InvisiblePlaywright(seed=SEED, binary_path=firefox_binary,
                             headless=True) as browser:
        pages = [browser.new_page(), browser.new_page()]
        for p in pages:
            p.goto(page_url)
        for p in pages:
            # A person types in the tab in front. In a tab left behind, the
            # field's focus event waits for the tab to come forward, so the
            # page would date the pause from the wrong moment.
            p.bring_to_front()
            p.fill("#f", TEXT)
            events = p.evaluate("__ev")
            # The field's focus: bringing a tab to the front focuses its
            # document first, which is not the pause before the field.
            focus = next(t for k, t, on in events if k == "focus" and on == "f")
            keys = [t for k, t, _ in events if k == "keydown"]
            seen.append(((keys[0] - focus) / 1000,
                         [b - a for a, b in zip(keys, keys[1:])]))

    for tab, (pause, intervals) in enumerate(seen):
        own, other = plans[tab], plans[1 - tab]
        assert abs(pause - own[0]) < abs(pause - other[0]), (
            "tab %d paused %.2f s; its plan is %.2f s, the other tab's %.2f s"
            % (tab, pause, own[0], other[0]))
        assert _distance(intervals, own[1]) < _distance(intervals, other[1]), (
            "tab %d typed with intervals %s; its plan is %s, the other tab's %s"
            % (tab, [round(i) for i in intervals],
               [round(i) for i in own[1]], [round(i) for i in other[1]]))

