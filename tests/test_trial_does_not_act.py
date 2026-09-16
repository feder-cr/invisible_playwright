"""`trial=True` runs the checks and performs nothing.

⛔ THE DEFECT THIS FILE EXISTS FOR. `trial` is accepted on 31 public signatures
- click, dblclick, hover, tap, check, uncheck, set_checked and drag on Page,
Frame, Locator and ElementHandle - and the word did not appear anywhere in
`_juggler/`. It reached the wire and fell off it, so `click(trial=True)`
performed a real click.

The one place that read it made it worse rather than better: the cursor wrapper
took `trial` as a reason to skip the humanised approach, and then ran the action
anyway. A caller asking "would this work?" got a click with no pointer movement
in front of it, which is the most recognisable shape a click can have.

It is honoured in `Actions._retry` now, which is the only thing that knows what
actionable means. The alternative was to honour it inside each of the seven
actions, which is the same sentence written seven times and an eighth action
written without it.
"""
from __future__ import annotations

import urllib.parse

import pytest

from invisible_playwright import InvisiblePlaywright
from invisible_playwright._juggler.actions import Actions, ElementNotActionable

MAIN = "frame-main"


class _Injected:
    """Enough of the injected script for the retry loop to reach a point."""

    def __init__(self, hit="done"):
        self.hit = hit
        self.disposed: list = []
        self.checks = 0
        self.states_asked = 0

    def query_selector(self, frame, selector, **kw):
        return "element"

    def element_states(self, frame, element, states):
        self.states_asked += 1
        return {"ok": True}

    def bounding_box(self, frame, element):
        return {"x": 10.0, "y": 20.0, "width": 40.0, "height": 10.0}

    def evaluate(self, frame, expression, **kw):
        return {"w": 1280, "h": 800}

    def scroll_into_view(self, frame, element):
        return False

    def check_hit_target(self, frame, element, point):
        self.checks += 1
        return self.hit

    def dispose(self, frame, element):
        self.disposed.append(element)


class _Conn:
    """The quad comes from the engine, not from the page, so the double has to
    answer the protocol rather than the injected script."""

    def __init__(self):
        self.sent: list = []

    def send(self, method, params=None, **kw):
        self.sent.append(method)
        if method == "Page.getContentQuads":
            return {"quads": [{"p1": {"x": 10.0, "y": 20.0},
                               "p2": {"x": 50.0, "y": 20.0},
                               "p3": {"x": 50.0, "y": 30.0},
                               "p4": {"x": 10.0, "y": 30.0}}]}
        return {}


class _Lifecycle:
    main_frame = MAIN


def _actions(hit="done") -> Actions:
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected(hit)
    actions.c = _Conn()
    actions.session = "session"
    return actions


def test_a_trial_performs_the_checks_and_NOT_the_action():
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE. To watch it fail, delete the
    `elif trial:` branch in `_retry`: `run` is called, and in the real product
    that call is a click on somebody's page."""
    actions = _actions()
    ran: list = []

    out = actions._retry("#target", lambda f, el, p: ran.append("acted"),
                         timeout=5, trial=True)

    assert out is None
    assert ran == [], "the trial performed the action"
    assert actions.inj.states_asked >= 1, (
        "the trial skipped the actionability checks, which are the only thing "
        "it is supposed to do")


def test_without_trial_the_action_still_happens():
    """The other half, so the branch above cannot be satisfied by an action
    that never runs at all."""
    actions = _actions()
    ran: list = []

    out = actions._retry("#target", lambda f, el, p: ran.append("acted") or "ok",
                         timeout=5)

    assert out == "ok"
    assert ran == ["acted"]


def test_a_trial_checks_the_hit_target_too():
    """A trial that answers yes and is followed by a click landing elsewhere
    has told the caller nothing."""
    actions = _actions()
    actions._retry("#target", lambda f, el, p: None, timeout=5, trial=True)
    assert actions.inj.checks == 1


def test_a_trial_on_a_covered_element_refuses_instead_of_answering_yes():
    """⛔ The second known-bad input: make `_retry` return before the hit-target
    check and this passes for the wrong reason.

    A covered element is exactly what a caller asks a trial about, so a trial
    that says yes there would be worse than no trial at all.
    """
    actions = _actions(hit="<div id='overlay'> intercepts the pointer")
    with pytest.raises(ElementNotActionable, match="overlay"):
        actions._retry("#target", lambda f, el, p: None, timeout=0.3, trial=True)


def test_a_trial_leaves_no_handle_behind():
    """The loop disposes what it resolved, on the trial path as on the other."""
    actions = _actions()
    actions._retry("#target", lambda f, el, p: None, timeout=5, trial=True)
    assert actions.inj.disposed == ["element"]


def test_a_dragged_trial_checks_BOTH_ends_and_dispatches_nothing():
    """⛔ Checking only the source would answer half the question, and the half
    it skipped is the one that fails: a covered target is the ordinary reason a
    drag does not land."""
    actions = _actions()
    visti: list = []
    actions._mouse_event = lambda *a, **k: visti.append(a[0] if a else "?")

    out = actions.drag_and_drop("#from", "#to", timeout=5, trial=True)

    assert out is None
    assert visti == [], "a trial drag sent %s" % visti
    assert actions.inj.checks == 2, (
        "both ends have to be checked, saw %d" % actions.inj.checks)


# ── with a browser ──────────────────────────────────────────────────────────

def _data_url(html: str) -> str:
    return "data:text/html," + urllib.parse.quote(html)


@pytest.mark.e2e
def test_a_trial_click_never_reaches_the_page(firefox_binary):
    """The proof, with a real browser and a real page counting real clicks.

    Everything above this line runs against doubles, so it can only show that
    the branch is taken. This shows that nothing arrives, which is the claim a
    caller of `trial=True` is relying on. Before the fix the counter read 1
    after the trial: the page had been clicked by a call whose whole purpose
    was not to click it.
    """
    html = ("<button id='b' style='width:200px;height:60px' "
            "onclick='window.__n=(window.__n||0)+1'>go</button>")
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(html))

        page.click("#b", trial=True)
        assert page.evaluate("window.__n || 0") == 0, (
            "the trial clicked the page")

        page.click("#b")
        assert page.evaluate("window.__n || 0") == 1, (
            "the ordinary click stopped working, so the assertion above is "
            "green for the wrong reason")
