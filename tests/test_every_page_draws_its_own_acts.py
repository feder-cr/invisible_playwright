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

The page's number now comes with the page (`PageDispatcher.number`: the number
the client reserved at the `new_page()` call, [B237]) and is part of every
nonce. The same seed still gives the same sequence: replaying a
session replays its acts.

The unit half builds pages the way the server does, through
`BrowserDispatcher.actions_for_page`; the e2e half reads the rhythm a real page
sees in two tabs of one session.
"""
from __future__ import annotations

import http.server
import statistics
import threading

import pytest

from invisible_playwright._behaviour import (
    TypingPersona, act_nonce, plan_hesitation, plan_typing,
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


def _page(browser, number):
    return browser.actions_for_page("session", None, _Field(), number)


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
    one = _first_acts(_page(browser, 0), clock)
    two = _first_acts(_page(browser, 1), clock)
    assert one["pause"] != two["pause"]
    assert one["typing"] != two["typing"]
    assert one["click"] != two["click"]


def test_the_same_seed_replays_the_same_sequence_across_pages(clock):
    """Reproducibility: two sessions with one seed, the same acts in the same
    pages, the same numbers."""
    runs = []
    for _ in range(2):
        browser = _browser()
        runs.append([_first_acts(_page(browser, n), clock) for n in range(3)])
    assert runs[0] == runs[1]
    other = _browser(SEED + 1)
    assert runs[0] != [_first_acts(_page(other, n), clock) for n in range(3)]


def test_the_first_page_keeps_the_plain_count(clock):
    """The first field of a session is nonce 1 on the first page: the page
    number goes in the high bits, so page 0 keeps the plain count."""
    a = _page(_browser(), 0)
    assert _first_acts(a, clock)["pause"] == pytest.approx(
        plan_hesitation(TypingPersona.from_seed(SEED), "field", 1) / 1000.0)


def test_the_keyboard_and_the_drag_number_their_acts_on_the_page():
    """One numbering per page: the keyboard holds the page's, not one of its
    own, and the pages of one session are numbered apart."""
    browser = _browser()
    a, b = _page(browser, 0), _page(browser, 1)
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

#: ⛔ LONG ENOUGH FOR THE TWO PLANS TO DIFFER BY MORE THAN A LOADED MACHINE
#: ADDS. With "hello" the two tabs' four intervals differed by 426 ms in all,
#: and four browsers at once on four CPUs (the CI runner's `-n 4`) delay a
#: single key by up to 480 ms: measured on Linux, 0 tabs out of 440 got the
#: other tab's page number, and the test still failed 2 runs out of 48 and 3
#: out of 12, each time on one late step. With "helloworld" the plans differ
#: by 3.6 s more, because the second tab's typist stops to think at the sixth
#: key and the first tab's does not.
TEXT = "helloworld"


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
    """A page's first fill as the page should see it, in ms: the pause from
    the field's focus to the first key, then each keydown-to-keydown
    interval."""
    persona = TypingPersona.from_seed(seed)
    nonce = act_nonce(page, 1)
    plan = plan_typing(TEXT, persona, nonce=nonce)
    return ([plan_hesitation(TypingPersona.from_seed(seed), "field", nonce)]
            + [dwell + gap for dwell, gap in plan[:-1]])


def _distance(seen, plan):
    """How far what a page saw is from a plan, once the delay common to every
    step is taken out.

    ⛔ ONE TIMELINE AND ONE JUDGE, NOT A VERDICT PER STEP. What a page measures
    is the plan plus the machine's latency, and under load the latency of a
    single step reaches a second: measured with four browsers on four CPUs, one
    protocol round trip inside the pause took 1.28 s. A verdict on the pause
    alone then has only the gap between the two tabs' pauses to stand on, and
    a verdict per step lets one late step decide. Summed over the whole
    timeline, a late step can cost no more than the evidence of that one step,
    and the other steps still carry the verdict.

    ⛔ AND A COMMON DELAY FAVOURS THE SLOWER PLAN. Latency only adds, so a tab
    typed with the faster plan drifts toward the slower one by the same amount
    on every step, and raw nearness then calls it the other tab's. The median
    of the differences is that common delay; the median rather than the mean,
    so that the one late step cannot move it."""
    d = [x - y for x, y in zip(seen, plan)]
    common = statistics.median(d)
    return sum(abs(x - common) for x in d)


@pytest.mark.e2e
def test_two_tabs_of_one_session_are_typed_with_two_rhythms(firefox_binary, page_url):
    """Each tab's first fill matches ITS plan, not the other tab's. Before,
    the second tab replayed the first tab's plan, so it sat nearer to that.

    Judged by nearness rather than by equality: what a page measures is the
    plan plus the machine's own latency, which varies under load."""
    from invisible_playwright import InvisiblePlaywright

    plans = [_planned(SEED, 0), _planned(SEED, 1)]
    # The seed and the text are ones whose two tabs differ by more than the
    # latency can hide. One late step costs the verdict at most twice that
    # step's evidence, and never more than twice its own lateness, so the
    # two plans must differ by more than twice the worst step measured (1.6 s
    # on a pause, four browsers on four CPUs and a build running beside them).
    assert _distance(plans[0], plans[1]) > 2 * 1600

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
            seen.append([keys[0] - focus]
                        + [b - a for a, b in zip(keys, keys[1:])])

    for tab, timeline in enumerate(seen):
        own, other = plans[tab], plans[1 - tab]
        assert _distance(timeline, own) < _distance(timeline, other), (
            "tab %d paused, then typed, with %s ms; its plan is %s, the other "
            "tab's %s" % (tab, [round(i) for i in timeline],
                          [round(i) for i in own], [round(i) for i in other]))

