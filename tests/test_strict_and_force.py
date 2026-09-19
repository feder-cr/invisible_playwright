"""`strict` refuses an ambiguous selector, `force` skips the checks.

⛔ THE DEFECT THIS FILE EXISTS FOR, and it is the same shape as `trial` one
layer along. Both options are accepted across the public API and neither
reached `_juggler/`: the server read `params.get("trial")` and nothing else, so
`strict` and `force` fell off the wire.

`strict` is the expensive one, because nobody passes it by hand. Every Locator
method sends `strict=True` - 28 call sites in `_pw/_impl/_locator.py` - so
`page.locator("button").click()` on a page with two buttons clicked the FIRST
one instead of refusing. Not a missing error message: the automation acted on
an element nobody had chosen, and said nothing.

`force` promised to "bypass the actionability checks" and bypassed none of
them. Both halves are needed for that promise - the state checks AND the hit
target - because "receives events" is one of the checks it names, and an
overlay intercepting the pointer is the ordinary reason somebody passes it.

The fix is one reader (`_act_opts`), not one reader per option: seven copies of
`params.get(...)` is how the eighth action gets written without one, which is
exactly how these two came to be accepted everywhere and read nowhere. The
structural test below is the one that keeps that true.
"""
from __future__ import annotations

import ast
import inspect
import urllib.parse
from pathlib import Path

import pytest

from invisible_playwright import InvisiblePlaywright
from invisible_playwright._juggler import server as server_module
from invisible_playwright._juggler.actions import Actions, ElementNotActionable

MAIN = "frame-main"

#: The options a caller sets per action. They travel together or one of them
#: gets forgotten, which is the whole history of this file.
ACT_OPTIONS = ("trial", "strict", "force")


class _Injected:
    """Enough of the injected script for the retry loop to reach a point."""

    def __init__(self, hit="done", matches=1, states_ok=True):
        self.hit = hit
        self.matches = matches
        self.states_ok = states_ok
        self.strict_asked: list = []
        self.states_asked = 0
        self.checks = 0
        self.disposed: list = []

    def query_selector(self, frame, selector, *, strict=False):
        # The double follows the REAL signature. A double taking `**kwargs`
        # would have swallowed the parameter this file is about and stayed
        # green while the product dropped it.
        self.strict_asked.append(strict)
        if strict and self.matches > 1:
            raise _StrictViolation(
                "strict mode violation: %r resolved to %d elements"
                % (selector, self.matches))
        return "element"

    def element_states(self, frame, element, states):
        self.states_asked += 1
        if self.states_ok:
            return {"ok": True}
        return {"ok": False, "missing": "visible"}

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


class _StrictViolation(Exception):
    """What the injected script raises. Named here so the test can catch it
    without asserting on the product's exception hierarchy."""


class _Conn:
    def __init__(self):
        self.sent: list = []

    def send(self, method, params=None, **kw):
        self.sent.append(method)
        if method == "Page.getContentQuads":
            return {"quads": [{"p1": {"x": 10.0, "y": 20.0},
                               "p2": {"x": 50.0, "y": 20.0},
                               "p3": {"x": 50.0, "y": 30.0},
                               "p4": {"x": 10.0, "y": 30.0}}]}
        if method == "Page.pointerLanded":
            # This engine has a static page: whatever was sent landed. The
            # landing that MISSES is modelled in test_a_click_is_delivered_once.
            return {"landings": [{"type": t, "landed": True, "on": ""}
                                 for t in (params or {}).get("types", [])]}
        return {}


class _Lifecycle:
    main_frame = MAIN


def _actions(hit="done", matches=1, states_ok=True) -> Actions:
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected(hit, matches, states_ok)
    actions.c = _Conn()
    actions.session = "session"
    return actions


# ── strict reaches the resolution ───────────────────────────────────────────

def test_strict_reaches_the_place_that_can_refuse():
    """⛔ THE KNOWN-BAD INPUT: drop `strict=strict` from the `query_selector`
    call in `_retry` and this goes red. Nothing else in the product can see
    that, because the option was accepted all the way down and read at the end
    by nobody."""
    actions = _actions()
    actions._retry("#target", lambda f, el, p: "ok", timeout=5, strict=True)
    assert actions.inj.strict_asked == [True], (
        "the resolution was asked without `strict`, so an ambiguous selector "
        "acts on the first match instead of refusing")


def test_without_strict_the_resolution_is_asked_without_it():
    """The other half: the assertion above must not be satisfied by a product
    that passes `strict=True` always, which would refuse selectors the public
    API deliberately allows to be ambiguous."""
    actions = _actions()
    actions._retry("#target", lambda f, el, p: "ok", timeout=5)
    assert actions.inj.strict_asked == [False]


def test_an_ambiguous_selector_under_strict_does_not_retry_until_timeout():
    """A strict violation is a statement about the PAGE, not a condition that
    might clear: retrying it would turn an immediate, accurate refusal into a
    thirty-second wait ending in the wrong message."""
    actions = _actions(matches=3)
    with pytest.raises(_StrictViolation, match="resolved to 3"):
        actions._retry("button", lambda f, el, p: "ok", timeout=30, strict=True)
    assert len(actions.inj.strict_asked) == 1, (
        "the loop retried a strict violation %d times"
        % len(actions.inj.strict_asked))


# ── force skips the checks ──────────────────────────────────────────────────

def test_force_skips_the_actionability_states():
    """⛔ THE KNOWN-BAD INPUT: restore `elif states:` in `_retry` and this goes
    red. The double answers "missing visible" to every state query, so without
    `force` the loop can only time out."""
    actions = _actions(states_ok=False)
    out = actions._retry("#target", lambda f, el, p: "acted",
                         states=["visible"], timeout=5, force=True)
    assert out == "acted"
    assert actions.inj.states_asked == 0, (
        "force asked the actionability checks it exists to bypass")


def test_without_force_the_same_element_is_refused():
    """The control arm. Without it the test above passes against a product
    that never checks actionability at all."""
    actions = _actions(states_ok=False)
    with pytest.raises(ElementNotActionable, match="visible"):
        actions._retry("#target", lambda f, el, p: "acted",
                       states=["visible"], timeout=0.3)


def test_force_skips_the_hit_target_check_too():
    """⛔ "Receives events" is one of the checks `force` names, and clicking
    through an overlay is the reason the option exists. Honouring it on the
    states alone leaves the option half wired - which is indistinguishable,
    from the caller's side, from not honouring it at all."""
    actions = _actions(hit="<div id='overlay'> intercepts the pointer")
    ran: list = []

    out = actions._act_on_target(MAIN, "element", (10.0, 20.0),
                                 approach=lambda: ran.append("approached"),
                                 commit=lambda: ran.append("acted") or "ok",
                                 force=True)

    assert out == "ok"
    # Forced skips the CHECK, not the approach: a press with no pointer
    # movement before it is the most recognisable shape a click has.
    assert ran == ["approached", "acted"]
    assert actions.inj.checks == 0


def test_without_force_a_covered_element_still_refuses():
    """The control arm for the one above."""
    from invisible_playwright._juggler.actions import WrongHitTarget

    actions = _actions(hit="<div id='overlay'> intercepts the pointer")
    with pytest.raises(WrongHitTarget, match="overlay"):
        actions._act_on_target(MAIN, "element", (10.0, 20.0),
                               approach=lambda: None, commit=lambda: "ok")


def test_a_forced_trial_does_not_check_the_hit_target():
    """A trial answers the question the caller would actually ask, and
    `trial=True, force=True` asks whether a FORCED action would go through.
    Checking the target there would answer a question nobody asked and refuse
    the case the caller is about to force."""
    actions = _actions(hit="<div id='overlay'> intercepts the pointer")
    out = actions._retry("#target", lambda f, el, p: "acted",
                         timeout=5, trial=True, force=True)
    assert out is None
    assert actions.inj.checks == 0


# ── the structural claim: they travel together ──────────────────────────────

def _server_source() -> ast.Module:
    return ast.parse(Path(inspect.getfile(server_module)).read_text(
        encoding="utf-8"))


def test_only_one_place_in_the_server_names_these_options():
    """⛔ THE RULE THIS FILE PROTECTS, and the reason `strict` and `force` were
    dead for so long: a reader per option is a shape where the next action is
    written with one of them and without the others.

    `_act_opts` is the one reader. A second `params.get("force")` somewhere is
    not a bug today - it is the first of the copies that diverge.
    """
    tree = _server_source()
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == "_act_opts":
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value in ACT_OPTIONS):
                offenders.append("%s reads %r" % (node.name,
                                                  call.args[0].value))
    assert not offenders, (
        "these read a per-action option outside `_act_opts`, which is the "
        "shape that lost `strict` and `force`: " + "; ".join(offenders))


def test_every_operation_that_can_carry_these_options_does():
    """⛔ DERIVED FROM THE CODE, NOT FROM A LIST HERE. A hand-written list of
    operations is a second place to update, and the one that does not get
    updated is the new action - which is the failure this whole file is about.

    So: ask `Actions` which methods accept the options bag, then require every
    server operation calling one of them to hand it `_act_opts`. Adding an
    action without wiring it turns this red on the day it is written.
    """
    carriers = {name for name, fn in inspect.getmembers(Actions,
                                                        inspect.isfunction)
                if any(p.kind is inspect.Parameter.VAR_KEYWORD
                       for p in inspect.signature(fn).parameters.values())}
    assert carriers, (
        "no action takes the options bag, so this test is green because it "
        "checked nothing")

    tree = _server_source()

    # An operation may pass the bag itself or through a helper that carries it.
    # Which helpers those are is computed, not listed: a helper named here
    # would be a second place to update, and this test exists because that
    # shape loses options.
    carriers_of_the_bag = {"_act_opts"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and "_act_opts" in ast.dump(node):
            carriers_of_the_bag.add(node.name)

    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("op_"):
            continue
        body = ast.dump(node)
        called = {call.func.attr for call in ast.walk(node)
                  if isinstance(call, ast.Call)
                  and isinstance(call.func, ast.Attribute)
                  and isinstance(call.func.value, ast.Attribute)
                  and call.func.value.attr == "actions"}
        if called & carriers and not any(h in body for h in carriers_of_the_bag):
            missing.append("%s calls %s" % (node.name,
                                            ", ".join(sorted(called & carriers))))
    assert not missing, (
        "these operations reach an action that takes trial/strict/force and "
        "never pass them, so the option dies on the wire: " + "; ".join(missing))


def test_the_reader_answers_every_option():
    """The reader is the single place, so what it forgets nobody catches."""
    frame = server_module.__dict__
    reader = None
    for value in frame.values():
        if inspect.isclass(value) and hasattr(value, "_act_opts"):
            reader = value._act_opts
            break
    assert reader is not None, "nothing in the server reads the action options"
    got = reader(object(), {"trial": True, "strict": True, "force": True})
    assert set(got) == set(ACT_OPTIONS), (
        "the reader answers %r, so an option the public API accepts is dropped "
        "before it reaches an action" % sorted(got))
    assert all(got.values())
    assert not any(reader(object(), {}).values()), (
        "an absent option is not False, so every action behaves as if the "
        "caller had asked for it")


# ── with a browser ──────────────────────────────────────────────────────────

def _data_url(html: str) -> str:
    return "data:text/html," + urllib.parse.quote(html)


@pytest.mark.e2e
def test_a_locator_refuses_an_ambiguous_selector(firefox_binary):
    """The proof, through the real client, the real wire and the real injected
    script.

    Everything above runs against doubles, so it can only show that the branch
    is taken. This is the joint: the Locator sets `strict=True`, the server has
    to read it, and the injected script has to raise. Before the fix this
    clicked the first button and reported success.
    """
    html = ("<button onclick='window.__a=1'>one</button>"
            "<button onclick='window.__b=1'>two</button>")
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(html))

        with pytest.raises(Exception) as caught:
            page.locator("button").click(timeout=5000)
        assert "strict mode violation" in str(caught.value), (
            "the locator resolved to two buttons and did not refuse: %s"
            % caught.value)
        assert page.evaluate("window.__a || 0") == 0, (
            "it refused AND clicked, which is worse than either")

        # The control arm: the ambiguous selector still works where the public
        # API allows it to, so the refusal above is about `strict` and not
        # about the page.
        page.click("button")
        assert page.evaluate("window.__a || 0") == 1


@pytest.mark.e2e
def test_force_turns_a_refusal_into_an_attempt(firefox_binary):
    """An overlay over the button. Without `force` the action cannot happen
    and says so; with `force` the event goes out at the point, which is what
    the option promises - the overlay receives it, because that is what is
    there.
    """
    html = ("<button id='b' style='position:absolute;left:20px;top:20px;"
            "width:200px;height:60px' onclick='window.__n=(window.__n||0)+1'>"
            "go</button>"
            "<div id='over' style='position:absolute;left:0;top:0;"
            "width:400px;height:200px' onclick='window.__o=(window.__o||0)+1'>"
            "</div>")
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(_data_url(html))

        with pytest.raises(Exception):
            page.click("#b", timeout=3000)
        assert page.evaluate("window.__o || 0") == 0, (
            "the refused click still reached the page")

        page.click("#b", force=True, timeout=5000)
        assert page.evaluate("window.__o || 0") == 1, (
            "force did not dispatch the event, so the option still means "
            "nothing to a caller")
