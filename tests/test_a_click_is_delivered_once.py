"""One call to `click` presses once, whatever the press does to the page.

⛔ THIS IS THE DEFECT, AND IT WAS MEASURED ON THE PUBLISHED 0.22.0 BEFORE IT WAS
UNDERSTOOD. Three buttons on one page, differing only in what their handler
does, thirty calls each, with the page counting the clicks it received:

    does nothing        30 calls -> 30 clicks, 30 reported success
    moves itself        30 calls -> 30 clicks, 30 reported success
    hides itself        30 calls -> 1068 clicks, 30 reported FAILURE

and, on the interface's own sessions panel, one call opened it and closed it
again - two complete gestures 167 ms apart - and reported success.

The cause is not in `click`. `_act_on_target` used to read the hit target again
AFTER the press and raise `WrongHitTarget` on what it found, and `_retry`
absorbs that exception by starting over. Starting over presses again. The
observation it raised on cannot distinguish a click that missed from a click
that worked and moved the control out from under the pointer, so on every
control whose effect changes what is under the pointer, the loop repeated a
thing it cannot un-do.

`test_hit_target_check.py` holds the unit shape - one read, between approach and
press. This holds the consequence on the real path, because that is the part a
reader cares about: a press is not idempotent and must happen once.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.actions import (ActionMissed, Actions,
                                                   ElementNotActionable)

MAIN = "frame-main"
POINT = (40.0, 60.0)


class _Keyboard:
    def modifier_mask(self):
        return 0


class _Lifecycle:
    main_frame = MAIN


class _Connection:
    """The engine: records what the driver sends, answers the geometry, and
    DELIVERS the events to the page.

    ⛔ The delivery is the part a shim forgets. Without it the driver can be
    counted but the page never reacts, so every arm here reports zero presses
    and a suite of them is green on a driver that clicks a hundred times.
    """

    def __init__(self, page=None):
        self.sent: list = []
        self.page = page
        # The engine numbers every mouse event it dispatches and acks it by
        # that number once the renderer has handled it. The number is what
        # the landing question must carry, or it is asked about the wrong
        # moment.
        self.dispatched = 0

    def send(self, method, params=None, session=None, timeout=None):
        self.sent.append((method, params))
        if method == "Page.dispatchMouseEvent" and self.page is not None:
            self.dispatched += 1
            kind = (params or {}).get("type")
            # ⛔ WHERE AN EVENT LANDS IS DECIDED WHEN IT IS DISPATCHED, and
            # the engine remembers it: that is what `Page.pointerLanded` reads
            # back. A page that moves while the pointer travels changes where
            # the MOVE lands; a handler on the press changes where the RELEASE
            # lands; a handler on the release changes nothing about the
            # release itself, which is why a button that hides on click is
            # still a hit.
            if kind == "mousemove":
                self.page.on_move(self.page)
            self.page.landed[kind] = self.page.hittable
            if kind == "mousedown":
                self.page.on_down(self.page)
            # A click completes on the release, which is when a page's
            # handler runs - if the release reached the button at all.
            if kind == "mouseup" and self.page.landed["mouseup"]:
                self.page.press()
            return {"eventId": self.dispatched}
        if method == "Page.pointerLanded":
            return {"landings": [
                {"type": t, "landed": self.page.landed.get(t, False),
                 "on": "" if self.page.landed.get(t, False)
                 else "<div id='something-else'>"}
                for t in (params or {}).get("types", [])]}
        if method == "Page.getContentQuads":
            x, y = POINT
            return {"quads": [{"p1": {"x": x - 10, "y": y - 5},
                               "p2": {"x": x + 10, "y": y - 5},
                               "p3": {"x": x + 10, "y": y + 5},
                               "p4": {"x": x - 10, "y": y + 5}}]}
        return {}

    def presses(self):
        return [m for m, _ in self.sent if m == "Page.dispatchMouseEvent"
                ].count("Page.dispatchMouseEvent")

    def downs(self):
        return [p.get("type") for m, p in self.sent
                if m == "Page.dispatchMouseEvent"].count("mousedown")


class _Page:
    """A page with one button. Pressing it runs `on_press`, and everything the
    driver asks about the element is answered from THIS state - so the test
    describes a page, not a sequence of canned verdicts."""

    def __init__(self, on_press=None, on_down=None, on_move=None):
        self.hittable = True
        self.visible = True
        self.on_press = on_press or (lambda page: None)
        # Runs after the press has landed, before the release: a page that
        # rearranges itself on `mousedown`.
        self.on_down = on_down or (lambda page: None)
        # Runs before the move lands: the layout shifting while the pointer
        # is still travelling.
        self.on_move = on_move or (lambda page: None)
        self.presses = 0
        self.landed: dict = {}

    def press(self):
        self.presses += 1
        self.on_press(self)


class _Injected:
    def __init__(self, page: _Page):
        self.page = page
        self.checks = 0

    def query_selector(self, frame, selector, strict=False):
        return "element" if self.page.visible else None

    def element_states(self, frame, element, states):
        if self.page.visible:
            return {"ok": True}
        return {"ok": False, "missing": "visible"}

    def check_hit_target(self, frame, element, point):
        self.checks += 1
        return "done" if self.page.hittable else "<div id='something-else'>"

    def scroll_into_view(self, frame, element):
        return False

    def dispose(self, frame, element):
        pass

    def evaluate(self, frame, expression, **kw):
        return {"w": 1000, "h": 800}


def _actions(page: _Page) -> Actions:
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected(page)
    actions.c = _Connection(page)
    actions.session = "s"
    actions.keyboard = _Keyboard()
    actions.position = (0.0, 0.0)
    # No persona: the rhythm of a press is not what this file is about, and a
    # real one would put real sleeps between the events of every test here.
    actions.pointer_persona = None
    actions._click_nonce = 0
    return actions


def test_a_button_that_does_nothing_is_pressed_once():
    """The control arm. Without it the two below could pass on a driver that
    never presses at all."""
    page = _Page()
    actions = _actions(page)
    actions.click("#b", timeout=2.0)
    assert page.presses == 1
    assert actions.c.downs() == 1


def test_a_button_that_hides_itself_is_pressed_once_and_the_call_SUCCEEDS():
    """⛔ THE KNOWN-BAD. Measured on 0.22.0 as thirty-nine presses and a
    failure, for one call, on a button whose handler hides it - which is what a
    modal's close, a cookie banner's accept and a "load more" all do.

    To watch this fail, put a second `check_hit_target` after `commit()` in
    `_act_on_target` and raise `WrongHitTarget` on it.
    """
    def hides(page):
        page.hittable = False
        page.visible = False

    page = _Page(on_press=hides)
    actions = _actions(page)

    actions.click("#b", timeout=2.0)

    assert page.presses == 1, (
        "one call pressed the button %d times; a press cannot be un-done"
        % page.presses)
    assert actions.c.downs() == 1


def test_a_toggle_is_flipped_once_and_not_back_again():
    """⛔ THE SILENT FACE OF THE SAME DEFECT, and the more dangerous one: the
    control is still there, so the repeat SUCCEEDS and the call reports success
    on a state it put back the way it found it. Measured on the interface's own
    sessions panel: opened, then closed, one call.
    """
    def toggles(page):
        page.open = not getattr(page, "open", False)
        # It stays hittable; what changed is underneath it.
        page.hittable = False

    page = _Page(on_press=toggles)
    actions = _actions(page)

    actions.click("#b", timeout=2.0)

    assert page.presses == 1, "the toggle was flipped %d times" % page.presses
    assert page.open is True, "the call put the control back where it found it"


def test_a_point_that_was_never_the_element_still_refuses_without_pressing():
    """The other direction, so the fix above cannot be had by removing the
    check altogether: an element that is covered BEFORE the press must still
    refuse, and refuse without pressing anything."""
    page = _Page()
    page.hittable = False
    actions = _actions(page)

    with pytest.raises(ElementNotActionable, match="landed elsewhere"):
        actions.click("#b", timeout=0.3)

    assert page.presses == 0, "it pressed a point that belonged to something else"
    assert actions.c.downs() == 0


def test_a_target_that_leaves_between_press_and_release_is_REPORTED_not_repeated():
    """⛔ THE SILENCE THIS FILE USED TO LEAVE. The check before the press
    passes - the pointer is on the button - then the button moves on
    `mousedown`, the release lands elsewhere, and no click is ever born. The
    driver said "done". Measured on a moving target, 8 times out of 8
    ([B217]).

    The answer is not a second read of the geometry, which cannot tell this
    from the button that hides itself above: it is where the release LANDED,
    which the engine recorded when it dispatched it. And it is reported, not
    retried: the press happened, and pressing again is the defect the rest of
    this file exists to prevent.
    """
    def leaves(page):
        page.hittable = False

    page = _Page(on_down=leaves)
    actions = _actions(page)

    with pytest.raises(ActionMissed, match="mouseup landed on"):
        actions.click("#b", timeout=2.0)

    assert page.presses == 0, "the release never reached the button"
    assert actions.c.downs() == 1, "it pressed again after a miss"


def test_a_hover_whose_target_left_during_the_travel_is_REPORTED():
    """`hover` has no approach: its one event IS the move, so the check runs
    while the pointer is still elsewhere and the travel that follows is the
    whole gap. Measured: 2 silent misses out of 8 on a moving target ([B217]).
    """
    def leaves(page):
        page.hittable = False

    page = _Page(on_move=leaves)
    actions = _actions(page)

    with pytest.raises(ActionMissed, match="mousemove landed on"):
        actions.hover("#b", timeout=2.0)


def test_the_landing_question_names_the_LAST_event_the_commit_sent():
    """⛔ THE QUESTION AND THE INPUT DO NOT SHARE A QUEUE. A `mousemove` is
    coalesced and dispatched at the next refresh tick, so a question sent
    right after it can be answered first - measured two times in four, from an
    empty record, while the page had already seen the very move it was asked
    about. The engine acks each event by the id it returned; the question
    carries the id of the LAST event this commit sent, and the engine waits
    for that ack before it looks. Here a click sends approach, press and
    release: the question must name the release, not the approach.
    """
    page = _Page()
    actions = _actions(page)

    actions.click("#b", timeout=2.0)

    asked = [p for m, p in actions.c.sent if m == "Page.pointerLanded"]
    assert len(asked) == 1
    assert asked[0]["afterEventId"] == actions.c.dispatched
    assert actions.c.dispatched == 3, "approach, press, release"


def test_force_makes_a_landing_elsewhere_the_request_and_not_a_miss():
    """`force` means "send it where the pointer is", and an overlay receiving
    it is then what was asked for: the landing is not checked, as the hit
    target before it is not."""
    def leaves(page):
        page.hittable = False

    page = _Page(on_down=leaves)
    actions = _actions(page)

    actions.click("#b", timeout=2.0, force=True)

    assert actions.c.downs() == 1
