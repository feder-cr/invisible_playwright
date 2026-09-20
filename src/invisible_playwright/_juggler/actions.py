"""The retry loop, and the actions that run inside it.

⛔ THIS IS THE PIECE THAT FAILS SILENTLY, and the reason is the shape of the
loop: if a condition is checked ONCE and then acted on, the page can change
between the check and the action. It doesn't break: it breaks ONE TIME IN
TWENTY, when loading is slower than usual.

THE RIGHT SHAPE, and every line of this file exists to keep it:

    until it expires:
        RESOLVE THE SELECTOR FROM SCRATCH   <- don't reuse the old handle
        ask whether it is actionable
        get the point
        act
        if something says "it's not there anymore": START OVER

⛔ **The selector is resolved on EVERY turn.** Reusing the previous turn's
handle is the mistake that makes the loop pointless: if the DOM has changed,
that handle points to a detached node, and the action goes nowhere without
saying anything.

⛔ **And a timeout must say WHY.** A bare `TimeoutError` on a retry loop is
the least useful thing you can print: the reason for the last turn - "missing
visible", "the selector finds nothing", "no quad" - is the only information
that tells you what to look at.
"""
from __future__ import annotations

import time
from typing import Optional

from .. import _pacing
from .injected import EvaluationError
from .keyboard import BUTTON_MASK, Keyboard, UnknownKey

#: The states a pointer action requires, in the order Playwright asks for
#: them. `stable` is the most expensive (waits two frames) and comes first
#: because it is also the one that most often isn't true yet.
ACTION_STATES = ["visible", "stable", "enabled"]


class ElementNotActionable(TimeoutError):
    """The loop timed out. The message carries the reason for the LAST turn."""


class ActionMissed(RuntimeError):
    """The event went out and landed on something other than the element.

    ⛔ NOT RETRYABLE, AND THAT IS THE WHOLE DIFFERENCE FROM `WrongHitTarget`.
    That one is raised BEFORE anything happened, so starting over is free.
    This one is raised after: a press has been delivered to whatever was under
    the pointer, and a loop that repeated the action would be pressing twice.

    It exists because the alternative was silence. Measured 2026-09-19 with
    a target that moves (`tests/gates/hover_non_mente.py` in the workbench):
    `hover()` returned normally 2 times out of 8 with the page having seen
    no event at all, and `click()` 8 times out of 8 - the hit-target check
    before the press passed, then the element left between `mousedown` and
    `mouseup`, the two landed on different nodes and no `click` was ever
    born. Nothing after the commit looked, for a reason that is right (see
    `_act_on_target`), so the caller was told "done". [B217]
    """


class WrongHitTarget(RuntimeError):
    """The event would have landed on ANOTHER element. RETRYABLE condition.

    ⛔ It's the window that the rest of the loop doesn't close, and it was
    MEASURED, not feared. Between the actionability check and the event a
    couple of commands go by; if the layout moves in that gap - a banner
    appearing, a font finishing loading, a `setTimeout` shifting the page -
    the point calculated earlier no longer belongs to the intended element.

    The case from 2026-08-27: a page that at 1200 ms reveals a block higher
    up. `dblclick` succeeded, the page saw NO `dblclick` event at all, and
    on the same page without that timer the same code worked. No error
    anywhere: the click had landed nineteen pixels higher.
    """


def _normalize_options(options) -> list:
    """A string becomes `{"valueOrLabel": ...}`, and that's NOT a detail.

    ⛔ THE INJECTED SCRIPT'S FILTER STARTS FROM `matches = true` AND NARROWS
    it down ONLY if the criterion carries one of `valueOrLabel`, `value`,
    `label` or `index`. A bare string has none of those, so every option
    matches and **the first one** gets picked.

    Measured on 2026-08-27 on a `<select>` with A/a and B/b: `["b"]`
    answered `['a']`, leaving the value at `a`. No error, no exception, the
    operation succeeded and the option was wrong - which is worse than a
    refusal, because the failure surfaces on the page later. With
    `[{"value": "b"}]` the same call answers `['b']`.
    """
    out = []
    for o in options:
        out.append({"valueOrLabel": o} if isinstance(o, str) else dict(o))
    return out


class Actions:
    #: The id of the last mouse event dispatched, as `Page.dispatchMouseEvent`
    #: returned it. A class default so that a bench which builds this object
    #: without `__init__` still has one, and so that "no event sent yet" reads
    #: as 0 - the value that also means "the engine returned no id".
    _last_event_id = 0

    def __init__(self, connection, session: str, lifecycle, injected,
                 session_seed=None, motion_budget_s=None):
        self.c = connection
        self.session = session
        self.lifecycle = lifecycle
        self.inj = injected
        #: ⛔ THE SEED ARRIVES AND THE PERSONAE ARE BUILT HERE, rather than one
        #: ready-made object arriving per rhythm. Two of them already want the
        #: same seed - the keyboard and the pointer - and a transport carrying
        #: built objects would grow a field per rhythm while the thing that is
        #: actually session-scoped stayed the same one number.
        #:
        #: `None` throughout means humanising is off, which has to keep meaning
        #: what it meant: no rhythm, not a default one.
        self.session_seed = session_seed
        self.pointer_persona = None
        typing_persona = None
        #: ⛔ THE ONE POINTER PATH THE CLIENT CANNOT DRAW. The client's cursor
        #: wrapper does "approach THEN act", which is every action except the
        #: drag: there the movement IS the action, it happens with the button
        #: already down, and the far end is only resolved after the press. So
        #: this side needs a generator of its own - the same generator, on its
        #: own stream, because two streams that produced the same path would be
        #: a repetition a page can read.
        self.motion = None
        self.motion_budget_s = motion_budget_s
        if session_seed is not None:
            from .._behaviour import PointerPersona, TypingPersona, _sub_seed
            self.pointer_persona = PointerPersona.from_seed(session_seed)
            typing_persona = TypingPersona.from_seed(session_seed)
            try:
                from .._motion import CursorMotion
            except Exception:  # noqa: BLE001 - see `_cursor`: motion is optional
                CursorMotion = None  # type: ignore[assignment]
            if CursorMotion is not None:
                self.motion = CursorMotion(_sub_seed(session_seed, "server:drag"))
        #: One stream per Actions, so two clicks in a session do not repeat the
        #: same durations - the same reason the keyboard keeps one.
        self._click_nonce = 0
        #: ⛔ A SINGLE keyboard per page, and that's the point: it holds
        #: the state of the modifiers. Building one per action would lose
        #: "Shift is down" between a `down` and the next key, and
        #: `Shift+a` would type `a`.
        self.keyboard = Keyboard(connection, session, typing_persona)
        #: The last pointer position. Used by the wheel and by drag and
        #: drop, which start from where the mouse IS - not from 0,0.
        self.position = (0.0, 0.0)

    # ── geometry ────────────────────────────────────────────────────────────
    def _viewport(self):
        """The main frame's viewport, or ``(None, None)`` when it cannot be read.

        ⛔ Asked of the PAGE, not of a stored viewport size. The window can be
        resized, and a value cached at launch is a second source for a fact the
        page already knows.

        ⛔ And ONE reader for two callers, since 2026-09-16. `_in_viewport`
        asked this question already; the drag's path needs the same answer to
        keep its waypoints somewhere an event can land. Asking twice in two
        shapes is how the two would come to disagree.
        """
        try:
            size = self.inj.evaluate(
                self.lifecycle.main_frame,
                "({w: window.innerWidth, h: window.innerHeight})")
        except Exception:
            return None, None
        if not isinstance(size, dict):
            return None, None
        return size.get("w"), size.get("h")

    def _in_viewport(self, point) -> bool:
        """Is this main-frame point somewhere an event can actually land?"""
        w, h = self._viewport()
        # ⛔ Unknown is not "outside": answering False here would scroll on
        # every action the moment this probe broke.
        if w is None or h is None:
            return True
        return 0 <= point[0] <= w and 0 <= point[1] <= h

    def _center_point(self, frame_id: str, element: str, position=None):
        """Where the event lands: the quad's centre, or the caller's offset.

        ⛔ No quad is NOT an error to propagate: it means "not visible
        right now", i.e. a RETRYABLE condition. Raising here would turn an
        element that's about to appear into a failure.

        ⛔ `position` IS PART OF THE CONTRACT AND WAS NOT IMPLEMENTED. Every
        pointer action in Playwright takes `position={x, y}` - an offset from
        the element's top-left - and this server ignored it, so a caller who
        pinned a point got the centre and no error. Two consequences, and the
        second is the one that matters here:

          * a documented option did nothing, silently;
          * the humanised cursor aims OFF-CENTRE on purpose and passes the
            point back through exactly this option. With it dropped, the last
            event of every element-targeted action landed on the exact
            geometric centre - one number, identical in every install,
            readable from a single event. The landing feature was inert on
            this transport and nothing failed.

        ⛔ Measured from the TOP-LEFT of the quad, not from the centre. The
        quad is in main-frame coordinates like everything else on this path,
        so the offset is added to its minimum x and y rather than to the mean.
        """
        r = self.c.send("Page.getContentQuads",
                        {"frameId": frame_id, "objectId": element},
                        session=self.session, timeout=10) or {}
        quads = r.get("quads") or []
        if not quads:
            return None
        q = quads[0]
        points = [q["p1"], q["p2"], q["p3"], q["p4"]]
        if isinstance(position, dict) and "x" in position and "y" in position:
            return (min(p["x"] for p in points) + float(position["x"]),
                    min(p["y"] for p in points) + float(position["y"]))
        return (sum(p["x"] for p in points) / 4.0,
                sum(p["y"] for p in points) / 4.0)

    # ── the loop ────────────────────────────────────────────────────────────
    def _retry(self, selector: str, run, *, states=None,
               timeout: float = 30.0, frame_id: Optional[str] = None,
               position=None, element_id: Optional[str] = None,
               trial: bool = False, strict: bool = False,
               force: bool = False):
        """Resolve, check, act, and if something doesn't match, START OVER.

        ⛔ `position` travels HERE and not through each action, because the
        point is recomputed on every turn of this loop: an offset applied by
        the caller once would be stale the moment the page moved, which is
        precisely the case this loop exists to absorb.

        ⛔ `element_id` IS THE ElementHandle CASE, and it changes exactly two
        things. The node is not looked up again - a handle names one node, and
        re-querying the selector would be a different element with the same
        description, which is not what `handle.click()` means - and the node is
        NOT disposed at the end, because it belongs to the caller and disposing
        it would destroy the handle they still hold.

        Everything else is deliberately shared: states, the point, the scroll,
        the hit-target check, the retry on detachment. A second loop for handles
        would be a second definition of "actionable" and a second click path,
        and two click paths are two fingerprints.
        """
        f = frame_id or self.lifecycle.main_frame
        if f is None:
            raise RuntimeError("no main frame: the page isn't ready")
        states = ACTION_STATES if states is None else states
        deadline = time.monotonic() + timeout
        reason = "haven't tried yet"
        turns = 0
        while True:
            turns += 1
            element = None
            ours = False
            try:
                if element_id is not None:
                    # The caller's node, fixed. Not re-queried and not ours.
                    element = element_id
                else:
                    # ⛔ FROM SCRATCH on every turn. A handle from the previous
                    # turn could point to a node the DOM has since replaced.
                    # ⛔ `strict` REACHES HERE OR IT MEANS NOTHING. The
                    # injected script has always known how to raise on it and
                    # the chain accepts it, but no caller ever passed it: the
                    # default stayed false, so a selector matching MORE THAN
                    # ONE element acted on the FIRST instead of refusing. That
                    # is not only correctness - the automation was touching an
                    # element nobody had chosen.
                    element = self.inj.query_selector(f, selector, strict=strict)
                    ours = True
                if not element:
                    reason = "the selector finds nothing"
                elif states and not force:
                    result = self.inj.element_states(f, element, states)
                    if not result.get("ok"):
                        reason = "missing %s" % result.get("missing",
                                                            "a state")
                        element_ok = False
                    else:
                        element_ok = True
                else:
                    element_ok = True

                if element and element_ok:
                    # ⛔ SCROLL FIRST, and only when the point is not usable.
                    # Actionability says "visible", which is true of an element
                    # three thousand pixels down; the POINT is what has to be
                    # inside the viewport, and `getContentQuads` answers in
                    # main-frame coordinates. Scrolling unconditionally would
                    # move the page under every ordinary click for nothing, so
                    # the quad is measured first and the scroll happens only if
                    # it lands outside - then the loop recomputes, because the
                    # geometry has just changed underneath.
                    point = self._center_point(f, element, position)
                    if point is not None and not self._in_viewport(point):
                        if self.inj.scroll_into_view(f, element):
                            reason = ("the element was outside the viewport; "
                                      "scrolled it in and starting over")
                            continue
                        point = self._center_point(f, element, position)
                    if point is None:
                        reason = "the element has no quad (it isn't visible)"
                    elif trial:
                        # ⛔ TRIAL STOPS HERE, AND HERE IS THE ONLY PLACE IT CAN.
                        #
                        # It used to stop nowhere. The public API accepts it on
                        # 31 signatures and `_juggler/` never mentioned it, so
                        # `click(trial=True)` performed a real click. The only
                        # reader was the cursor wrapper, which took it as a
                        # reason to skip the approach and then ran the action
                        # anyway: the worst of the three possible behaviours,
                        # because the click went out in the most recognisable
                        # form there is, with no pointer movement before it.
                        #
                        # This loop is where it belongs. `trial` is a statement
                        # ABOUT ACTIONABILITY - run the checks, skip the act -
                        # and this loop is the only thing that knows what
                        # actionable means. Honouring it inside each action
                        # would be the same sentence written six times, and the
                        # seventh action would be written without it.
                        #
                        # The hit target is checked too: a trial that answers
                        # yes, followed by a click that lands elsewhere, has
                        # told the caller nothing. It costs one pure read.
                        #
                        # ⛔ Unless `force`, which skips that check on the real
                        # path: a trial has to answer the question the caller
                        # would actually ask, and `trial=True, force=True` asks
                        # whether a FORCED action would go through.
                        if not force:
                            verdict = self.inj.check_hit_target(
                                f, element, self._hit_point(f, point))
                            if verdict != "done":
                                raise WrongHitTarget(verdict)
                        return None
                    else:
                        return run(f, element, point)
            except EvaluationError as e:
                # ⛔ "notconnected" means the node disappeared BETWEEN the
                # resolution and the use: it's the case the loop exists to
                # absorb, not a failure. Everything else propagates.
                if "notconnected" not in str(e):
                    raise
                reason = "the node detached while I was using it"
            except WrongHitTarget as e:
                # ⛔ This too is a condition of the WORLD, not a failure: the
                # point stopped belonging to the element before we committed,
                # so nothing was done and starting over is free. The point gets
                # recalculated on the new geometry.
                #
                # ⛔ AND THAT SENTENCE IS THE INVARIANT THIS LOOP RESTS ON:
                # STARTING OVER IS SOUND ONLY WHILE NOTHING HAS HAPPENED. It
                # was broken for four versions. `_act_on_target` used to read
                # the DOM again AFTER the event and raise this same exception
                # on what it found, so "the click missed" and "the click worked
                # and the control moved out from under the pointer" arrived
                # here as the same word - and this line repeated the action.
                # Measured on 0.22.0: thirty-nine clicks delivered for one
                # call, and a toggle flipped twice and reported success.
                #
                # Nothing may raise this after committing. An action that needs
                # to report something about what it did afterwards must say so
                # in its RESULT: a retry loop cannot un-press a button, so it
                # must never be handed a reason to press one twice.
                reason = "the event would have landed elsewhere (%s)" % e
            finally:
                # Only what this loop resolved. Disposing the caller's handle
                # here would make `handle.click()` destroy the handle, and the
                # NEXT call on it would fail with the node not existing - an
                # error naming neither the cause nor the previous call.
                if element and ours:
                    self.inj.dispose(f, element)

            if time.monotonic() > deadline:
                raise ElementNotActionable(
                    "%r not actionable in %.0fs after %d attempts. Last "
                    "reason: %s" % (selector, timeout, turns, reason))
            time.sleep(0.05)

    # ── the hit target ──────────────────────────────────────────────────────
    def _hit_point(self, f, point):
        """The caller's point, expressed in `f`'s own coordinate space.

        ⛔ A POINT IS ALWAYS IN THE MAIN FRAME'S SPACE when it gets here:
        `Page.getContentQuads` answers there and `dispatchMouseEvent` wants it
        there. Handing that number to a hit test running INSIDE a nested frame
        asks the child about a coordinate that means something else, and it
        answers `<html>` - which is how a covered element used to be reported
        for something sitting right under the pointer.

        The shift is the difference between the two content origins, read from
        the engine rather than recomputed here.
        """
        if f == self.lifecycle.main_frame:
            return point
        main = self.inj.content_origin(self.lifecycle.main_frame)
        here = self.inj.content_origin(f)
        return (point[0] + main["x"] - here["x"],
                point[1] + main["y"] - here["y"])

    def _act_on_target(self, f, element, point, *, approach, commit,
                       force: bool = False,
                       lands: tuple = ("mousedown", "mouseup")):
        """Approach, check that the point still belongs to the element, and
        only then do the thing that cannot be taken back.

        ⛔ `force` SKIPS THE CHECK, AND THAT IS NOT AN EXEMPTION - IT IS WHAT
        THE OPTION MEANS. "Receives events" is one of the actionability checks
        `force` is documented to bypass, and an overlay intercepting the
        pointer is the ordinary reason somebody passes it. Honouring `force`
        on the state checks alone would leave it half wired, which is the
        defect this was: an option accepted on every public signature that
        changed nothing a caller could observe.

        ⛔ IT USED TO BE AN INTERCEPTOR, and that history is why the shape here
        matters. An interceptor watched each event WHILE it arrived and BLOCKED
        the ones whose point had stopped belonging to the element. It needed
        capture listeners for the duration of every action, and the block
        itself was a tell: a real `mousedown` that vanishes under
        `preventDefault` is not something any input stack produces.

        It was replaced by two pure DOM reads, one before the action and one
        after, and the one AFTER is what this corrects.

        ⛔ A CHECK AFTER THE EVENT CANNOT ANSWER THE QUESTION IT IS ASKED, AND
        IT DROVE A RETRY. Once the event has gone out, these two are the SAME
        observation:

            the point stopped belonging to the element BEFORE the event, so the
            click landed on something else;

            the point stopped belonging to the element BECAUSE of the event, so
            the click landed exactly where it was aimed and the control did
            what it exists to do.

        The second is not an edge case, it is most controls. Measured on the
        published 0.22.0, one `browser_click` per call: a button that hides
        itself on click received THIRTY-NINE clicks and the call then reported
        failure; a panel toggle was opened and closed by a single call, which
        reported success; a button that only moves, and one that does nothing,
        were correct thirty times out of thirty. The discriminant is not
        movement - it is the target ceasing to be hittable BY ITS OWN EFFECT.

        A check whose negative result carries no information cannot gate
        anything, so it is gone rather than narrowed. What it leaves behind is
        the invariant `_retry` depends on: starting over is sound only while
        nothing has happened, and every raise here is now before `commit`.

        ⛔ AND THE READ MOVED AFTER `approach`, WHICH IS WHERE THE RACE IS. It
        used to run before the pointer moved, so a page that rearranges itself
        on hover - a menu opening under the cursor is the ordinary case - was
        judged in the layout that existed before the thing that changed it.
        What is left is one round trip between this read and the press. Closing
        that one belongs to the engine, the only place that can verify and
        dispatch without a gap; it is not closed by reading the DOM again
        afterwards, which is what this function just stopped doing.

        Measured by `tests/gates/injected_page_surface.js`: the bundle touches
        the page's window zero times, in every phase.

        ⛔ AND WHAT CLOSES THE GAP IS NOT A THIRD READ OF THE GEOMETRY. It is
        the engine telling us where each event LANDED, recorded at dispatch by
        a privileged listener the page cannot see (`Page.pointerLanded`). That
        answer distinguishes what a read after the fact cannot: an event that
        hit the element and then saw it move away by its own effect landed ON
        the element, and says so. `lands` names the events the commit emits -
        the press and the release for a click, the one move for a hover - and
        every one of them must have landed on the element or inside it, which
        is also the condition under which the DOM composes a `click`. A miss
        raises `ActionMissed`, which `_retry` does not catch: something has
        happened, and the invariant this loop rests on is that it starts over
        only while nothing has.

        `force` skips this as it skips the check before: the caller has said
        the event is to go wherever the pointer is, and an overlay receiving it
        is then not a miss but the request. [B217]
        """
        approach()
        if not force:
            verdict = self.inj.check_hit_target(f, element,
                                                self._hit_point(f, point))
            if verdict != "done":
                raise WrongHitTarget(verdict)
        result = commit()
        if not force and lands:
            # `afterEventId` is what makes the answer about THIS commit: the
            # input and the question do not share a queue - a `mousemove` is
            # coalesced and dispatched at the next refresh tick - and without
            # it the engine answered from an empty record two times in four
            # while the page had already seen the very move it was asked about.
            answer = self.c.send("Page.pointerLanded",
                                 {"frameId": f, "objectId": element,
                                  "types": list(lands),
                                  "afterEventId": self._last_event_id},
                                 session=self.session, timeout=10)
            missed = [l for l in answer["landings"] if not l["landed"]]
            if missed:
                raise ActionMissed(
                    "the action went out but did not reach the element: "
                    + "; ".join("%s landed on %s" % (l["type"], l["on"])
                                for l in missed))
        return result

    # ── waiting ─────────────────────────────────────────────────────────────
    def wait_for_selector(self, selector: str, *, state: str = "visible",
                          timeout: float = 30.0, frame_id: Optional[str] = None):
        """Waits for a selector to reach a state, and returns its handle.

        ⛔ THE HANDLE IS NOT DISPOSED HERE, and that is deliberate: the caller
        is about to use it. `_retry` disposes what it resolves on every turn
        precisely because it re-resolves, so this cannot go through it - it
        would hand back an objectId it has just released, and a released handle
        does not raise, it answers wrong.

        ⛔ AND `state="attached"` AND `"detached"` ARE NOT ELEMENT STATES. The
        injected script knows visible / hidden / enabled / disabled / editable
        / checked; presence in the DOM is answered by the selector resolving at
        all, so those two are handled here rather than asked of a function that
        would reject them.
        """
        frame = frame_id or self.lifecycle.main_frame
        if frame is None:
            raise RuntimeError("no main frame: the page is not ready")
        deadline = time.monotonic() + timeout
        reason = "not tried yet"
        while True:
            element = self.inj.query_selector(frame, selector)
            if state == "detached":
                if not element:
                    return None
                self.inj.dispose(frame, element)
                reason = "the element is still attached"
            elif element:
                if state == "attached":
                    return element
                try:
                    if self.inj.element_state(frame, element, state):
                        return element
                    reason = "the element is not %s" % state
                except EvaluationError as failure:
                    reason = str(failure)
                self.inj.dispose(frame, element)
            else:
                reason = "the selector matches nothing"
            if time.monotonic() > deadline:
                raise ElementNotActionable(
                    "%r did not become %s in %.0fs. Last reason: %s"
                    % (selector, state, timeout, reason))
            time.sleep(0.05)

    # ── the actions ─────────────────────────────────────────────────────────
    def hover(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
              position=None, element_id: Optional[str] = None, **opts):
        def run(f, element, point):
            # The move IS the action here, so there is nothing to approach
            # first: the check sits immediately before the only event.
            return self._act_on_target(
                f, element, point,
                approach=lambda: None,
                commit=lambda: self._mouse_event("mousemove", point) or point,
                force=bool(opts.get("force")),
                # The one event a hover emits. Without this the check after
                # the commit would ask about a press that never went out.
                lands=("mousemove",))
        return self._retry(selector, run, timeout=timeout, element_id=element_id,
                           frame_id=frame_id, position=position, **opts)

    def click(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None, button: int = 0,
              delay_ms: Optional[float] = None,
              clicks: int = 1, position=None, modifiers: int = 0,
              element_id: Optional[str] = None, **opts):
        def run(f, element, point):
            def act():
                self._click_at_point(point, button=button, clicks=clicks,
                                     modifiers=modifiers, delay_ms=delay_ms)
                return point
            # The order is that of a user: approach, press, release. Skipping
            # the mousemove leaves the page without the hover, and there are
            # sites that open the menu right there - which is also why the
            # target is checked BETWEEN the two rather than before both.
            # ⛔ The move carries the modifiers too. A page that reads
            # `event.shiftKey` on `mouseover` - menus do - would otherwise
            # see an unmodified approach followed by a modified click,
            # which no real input device produces.
            return self._act_on_target(
                f, element, point,
                approach=lambda: self._mouse_event("mousemove", point,
                                                   modifiers=modifiers),
                commit=act,
                force=bool(opts.get("force")))
        return self._retry(selector, run, timeout=timeout, frame_id=frame_id,
                           position=position, element_id=element_id, **opts)

    def dblclick(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
                 delay_ms: Optional[float] = None,
                 button: int = 0, position=None, modifiers: int = 0, **opts):
        """⛔ These are NOT two `click`s in a row: the second one must carry
        `clickCount: 2`, and it's that field - not the interval between the
        two - that gives birth to the `dblclick` event. Two clicks with
        `clickCount: 1` produce two `click` events and no `dblclick`, which
        is a silent failure: the action succeeds and the site's handler
        never fires.

        ⛔ AND IT FORWARDS `frame_id`, which it used to accept and drop. A
        double click on an element inside an iframe resolved the selector in
        the MAIN frame instead, so it either found nothing or found a
        same-named element in the wrong document. The signature said the
        argument was honoured; nothing else did."""
        return self.click(selector, timeout=timeout, button=button,
                          clicks=2, frame_id=frame_id, position=position,
                          modifiers=modifiers, delay_ms=delay_ms, **opts)

    def check(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
              position=None, element_id: Optional[str] = None, **opts):
        return self._set_checked(selector, True, timeout=timeout, element_id=element_id,
                                 frame_id=frame_id, position=position, **opts)

    def uncheck(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
                position=None, element_id: Optional[str] = None, **opts):
        return self._set_checked(selector, False, timeout=timeout, element_id=element_id,
                                 frame_id=frame_id, position=position, **opts)

    def _set_checked(self, selector: str, wanted: bool, *, timeout: float,
                     frame_id: Optional[str] = None, position=None,
                     element_id: Optional[str] = None, **opts):
        """`check` / `uncheck`.

        ⛔ It CHECKS FIRST, and rechecks after. Clicking without looking
        flips a box that was already right - that's the obvious defect -
        but the second check is the one that matters: a `<label>` that
        intercepts the click, or a handler that puts the value back, make
        the action succeed while leaving the wrong state. Without the
        recheck the failure surfaces much later, elsewhere.
        """
        def run(f, element, point):
            state = "checked" if wanted else "unchecked"
            if self.inj.element_state(f, element, state):
                return "already there"

            # ⛔ GOES THROUGH THE SAME PATH AS `click`, and it isn't a
            # finishing touch: the first draft called `_click_at_point`
            # directly and failed on the very page that shifts its layout
            # at 1200 ms. One single place knows how to click; two know it
            # only until one of them learns something the other doesn't.
            self._act_on_target(
                f, element, point,
                approach=lambda: self._mouse_event("mousemove", point),
                commit=lambda: self._click_at_point(point),
                force=bool(opts.get("force")))
            if not self.inj.element_state(f, element, state):
                raise EvaluationError(
                    "clicked but the box stayed %s: someone intercepted "
                    "the click or put the value back"
                    % ("unchecked" if wanted else "checked"))
            return state
        return self._retry(selector, run, timeout=timeout, element_id=element_id,
                           frame_id=frame_id, position=position, **opts)

    def focus(self, selector: str, *, timeout: float = 30.0,
              frame_id: Optional[str] = None,
              element_id: Optional[str] = None, **opts):
        """⛔ Does NOT require `visible`: `focus()` works on an off-screen
        element, and imposing the pointer states would time out an action
        that would have succeeded. Playwright does the same."""
        def run(f, element, point):
            return self.inj.call(
                f, "(injected, el) => injected.focusNode(el, true)",
                {"objectId": element})
        return self._retry(selector, run, states=[], timeout=timeout, frame_id=frame_id,
                           element_id=element_id, **opts)

    def blur(self, selector: str, *, timeout: float = 30.0,
             frame_id: Optional[str] = None, **opts):
        def run(f, element, point):
            return self.inj.call(
                f,
                "(injected, el) => { if (!el.isConnected) return "
                "'error:notconnected'; el.blur(); return 'done'; }",
                {"objectId": element})
        return self._retry(selector, run, states=[], timeout=timeout, frame_id=frame_id,
                           **opts)

    def select_text(self, selector: str, *, timeout: float = 30.0,
                    frame_id: Optional[str] = None, **opts):
        def run(f, element, point):
            r = self.inj.call(f, "(injected, el) => injected.selectText(el)",
                              {"objectId": element})
            if isinstance(r, str) and r.startswith("error:"):
                raise EvaluationError("selectText: %s" % r)
            return r
        return self._retry(selector, run, states=["visible"],
                           timeout=timeout, frame_id=frame_id, **opts)

    def select_option(self, selector: str, options, *, timeout: float = 30.0,
                      frame_id: Optional[str] = None,
                      element_id: Optional[str] = None, **opts):
        """`select_option`. Options are given by value, label or index.

        ⛔ And the `input`/`change` events are requested from the TRUSTED
        command after the mutation, same as for `fill`: without it, a
        `<select>` changes value and the page doesn't know it - and if the
        injected script dispatched them they would come out with
        `isTrusted: false`, which is [B175].
        """
        wanted = _normalize_options(options)

        def run(f, element, point):
            r = self.inj.call(
                f, "(injected, el, o) => injected.selectOptions(el, o)",
                {"objectId": element}, wanted)
            if isinstance(r, str) and r.startswith("error:"):
                raise EvaluationError("selectOptions: %s" % r)
            self._trusted_events(f, element, ["input", "change"])
            return r
        return self._retry(selector, run,
                           states=["visible", "stable", "enabled"],
                           timeout=timeout, frame_id=frame_id,
                           element_id=element_id, **opts)

    def dispatch_event(self, selector: str, event_type: str, detail=None, *,
                       timeout: float = 30.0, frame_id: Optional[str] = None,
                       **opts):
        """`dispatch_event`.

        ⛔ This is the ONLY spot in the file where an event comes out NOT
        trusted, and it should be stated rather than discovered: the
        injected script builds it, so `isTrusted` is false. This is
        exactly what Playwright's API promises - it exists precisely to
        fabricate an arbitrary event - but it isn't a way to simulate a
        user: for that there is `click` and `type_text`, which go through
        the browser's own commands.
        """
        def run(f, element, point):
            return self.inj.call(
                f, "(injected, el, t, d) => injected.dispatchEvent(el, t, d)",
                {"objectId": element}, event_type, detail or {})
        return self._retry(selector, run, states=[], timeout=timeout, frame_id=frame_id,
                           **opts)

    def press(self, selector: str, key: str, *, timeout: float = 30.0,
              frame_id: Optional[str] = None,
              element_id: Optional[str] = None, **opts):
        """`press`: focuses and presses, with the modifiers from the name."""
        def run(f, element, point):
            self.inj.call(f, "(injected, el) => injected.focusNode(el, true)",
                          {"objectId": element})
            self.keyboard.press(key)
            return key
        return self._retry(selector, run,
                           states=["visible", "stable", "enabled"],
                           timeout=timeout, frame_id=frame_id,
                           element_id=element_id, **opts)

    def type_text(self, selector: str, text: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
                  delay: float = 0.0, element_id: Optional[str] = None,
                  **opts):
        """`type`: one key per character, WITHOUT clearing first.

        ⛔ It isn't `fill`: that one replaces the content, this one
        appends to it. Swapping them is the easiest way to end up with
        `foobar` in a field that was meant to hold `bar`.
        """
        def run(f, element, point):
            self.inj.call(f, "(injected, el) => injected.focusNode(el, true)",
                          {"objectId": element})
            self.keyboard.type(text, delay_ms=delay)
            return text
        return self._retry(selector, run,
                           states=["visible", "stable", "enabled"],
                           timeout=timeout, frame_id=frame_id,
                           element_id=element_id, **opts)

    def set_input_files(self, selector: str, files, *, timeout: float = 30.0,
                        frame_id: Optional[str] = None,
                        element_id: Optional[str] = None, **opts):
        """`set_input_files`. The paths are ABSOLUTE and the browser
        resolves them.

        ⛔ Goes through `Page.setFileInputFiles` and not the injected
        script: a page can't construct a `FileList`, and trying would
        leave the input empty with no error.

        ⛔ `element_id` WAS MISSING, AND THAT IS WHY A FILE CHOOSER COULD NOT
        UPLOAD ANYTHING. `FileChooser.set_files()` does not go through a
        selector - it holds the input element already - so it asks the
        ElementHandle for `setInputFiles`, and without this parameter there was
        nothing for that dispatcher to call. Every other action here takes the
        handle; this one was the exception nobody had needed yet.
        """
        def run(f, element, point):
            self.c.send("Page.setFileInputFiles",
                        {"frameId": f, "objectId": element,
                         "files": [str(p) for p in files]},
                        session=self.session, timeout=30)
            return list(files)
        return self._retry(selector, run, states=[], timeout=timeout,
                           frame_id=frame_id, element_id=element_id, **opts)

    def tap(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
            position=None, **opts):
        """`tap`. ⛔ Requires the context to have touch TURNED ON: without
        it, the event fires and the page has no `ontouchstart`, so it
        doesn't listen for it - it succeeds and does nothing. Touch is
        turned on with `Browser.setTouchOverride`, which is a
        context-level operation."""
        def run(f, element, point):
            self.c.send("Page.dispatchTapEvent",
                        {"x": point[0], "y": point[1],
                         "modifiers": self.keyboard.modifier_mask()},
                        session=self.session, timeout=10)
            return point
        return self._retry(selector, run, timeout=timeout,
                           frame_id=frame_id, position=position, **opts)

    def _glide(self, to_point, *, buttons: int = 0) -> int:
        """Move the pointer to *to_point* along a path a hand could have drawn.

        ⛔ WHY THIS EXISTS ON THIS SIDE AT ALL. Every other pointer action is
        approached by the client's cursor wrapper, which walks the cursor onto
        the element BEFORE calling the action. A drag cannot be served that way:
        the travel happens with the button already down, and its far end is only
        known after the press. So the movement is generated here - from the same
        generator (`_motion.CursorMotion`) and delivered under the same
        discipline (`_pacing`) as every movement the client makes. Neither is
        reimplemented here; a second copy of either is the defect this replaced.

        Returns the number of events actually delivered. Falls back to a single
        event when there is no generator (`humanize=False`, or a broken
        install), which is what "no humanising" has always meant here.
        """
        if self.motion is None:
            self._mouse_event("mousemove", to_point, buttons=buttons)
            return 1
        x0, y0 = self.position
        # ⛔ `path[1:]`: a path INCLUDES where it starts, and the pointer is
        # already there. Sending it would report a move to the point the last
        # event already reported - two identical events in a row, which is the
        # very pair this replaced. The client's walk drops it for the same
        # reason; found here by the browser arm, because in isolation there is
        # no preceding event for it to duplicate.
        path = self.motion.path(x0, y0, to_point[0], to_point[1])[1:]
        # ⛔ A curved path near an edge leaves the viewport on its own, and a
        # pointer event outside it is not ignored - the browser parks the cursor
        # at the origin, mid-movement. The destination is emitted unclamped: it
        # is where the movement has to end.
        w, h = self._viewport()
        evs = [_pacing.Ev(wp.t_ms, *_pacing.clamp_to_viewport(wp.x, wp.y, w, h))
               for wp in path[:-1]]
        evs.append(_pacing.Ev(path[-1].t_ms, to_point[0], to_point[1]))
        if self.motion_budget_s:
            evs = _pacing.fit_timeline(evs, self.motion_budget_s)
        if not evs:  # a generator that produced nothing must still arrive
            self._mouse_event("mousemove", to_point, buttons=buttons)
            return 1
        return _pacing.drive(
            evs, lambda x, y: self._mouse_event("mousemove", (x, y),
                                                buttons=buttons),
            origin=(x0, y0))

    def drag_and_drop(self, source: str, target: str, *,
                      timeout: float = 30.0,
                      frame_id: Optional[str] = None,
                      **opts):
        """`drag_and_drop`, in four beats.

        ⛔ THE FIRST `mousemove` AFTER THE `mousedown` IS NOT SKIPPED.
        Gecko gives birth to a drag from a movement with the button held
        down: press and release on the target, and you've made two
        clicks. And the two ends are resolved SEPARATELY, each with its
        own retry loop, because taking the second point before pressing
        the first would measure it on a page that is about to change.

        ⛔ AND THE TRAVEL IS A PATH, NOT A JUMP - which is a correctness fix as
        well as a stealth one. Measured 2026-09-16 on a page whose source was
        `draggable`: this used to emit five events in all, of which the two
        carrying the whole 480-pixel journey were IDENTICAL and 7 ms apart - a
        pair no device can produce, since a move event is born from moving. The
        second was there because one jump alone did not reliably start the drag.
        With a real path it is not needed: the drag is born at the first point
        that clears Gecko's threshold, the way it is for a hand.
        """
        if opts.get("trial"):
            # ⛔ BOTH ENDS, AND NOT ONE EVENT. A drag that only checked the
            # source would answer half the question, and the half it skipped is
            # the one that fails: a target covered by an overlay is the ordinary
            # reason a drag does not land. Resolved in order, because resolving
            # the target first would measure it on a page the press is about to
            # change - the same reason the real path resolves them separately.
            self._retry(source, lambda f, el, p: p, timeout=timeout,
                        frame_id=frame_id, **opts)
            self._retry(target, lambda f, el, p: p, timeout=timeout,
                        frame_id=frame_id, **opts)
            return None
        start = self._retry(source, lambda f, el, p: p, timeout=timeout,
                            frame_id=frame_id, **opts)
        # The approach is a path too: the cursor was somewhere before this call,
        # and arriving at the source in one event is the same tell as crossing
        # the page in one.
        self._glide(start)
        self._mouse_event("mousedown", start, buttons=BUTTON_MASK[0],
                          click_count=1)

        def run(f, element, point):
            self._glide(point, buttons=BUTTON_MASK[0])
            self._mouse_event("mouseup", point, buttons=0, click_count=1)
            return point
        try:
            return self._retry(target, run, timeout=timeout, frame_id=frame_id,
                               **opts)
        except BaseException:
            # ⛔ A button left down poisons EVERY subsequent action: the
            # `buttons` field of every event after would say "pressed".
            self._mouse_event("mouseup", start, buttons=0, click_count=1)
            raise

    # ── the pointer, by coordinates ─────────────────────────────────────────
    def move(self, x: float, y: float, *, steps: int = 1) -> None:
        """`mouse.move`. With `steps > 1` it interpolates, like Playwright
        does."""
        x0, y0 = self.position
        for i in range(1, max(1, steps) + 1):
            self._mouse_event("mousemove",
                              (x0 + (x - x0) * i / steps,
                               y0 + (y - y0) * i / steps))

    def mouse_down(self, *, button: int = 0, clicks: int = 1) -> None:
        self._mouse_event("mousedown", self.position, button=button,
                          buttons=BUTTON_MASK[button], click_count=clicks)

    def mouse_up(self, *, button: int = 0, clicks: int = 1) -> None:
        self._mouse_event("mouseup", self.position, button=button,
                          buttons=0, click_count=clicks)

    def click_at(self, x: float, y: float, *, button: int = 0,
                clicks: int = 1, delay_ms: Optional[float] = None):
        self.move(x, y)
        self._click_at_point((x, y), button=button, clicks=clicks,
                             delay_ms=delay_ms)

    def wheel(self, dx: float, dy: float) -> None:
        """`mouse.wheel`, from where the pointer IS - not from 0,0."""
        self.c.send("Page.dispatchWheelEvent",
                    {"x": self.position[0], "y": self.position[1],
                     "deltaX": dx, "deltaY": dy, "deltaZ": 0,
                     "modifiers": self.keyboard.modifier_mask()},
                    session=self.session, timeout=10)

    def _click_at_point(self, point, *, button: int = 0,
                        clicks: int = 1, modifiers: int = 0,
                        delay_ms: Optional[float] = None) -> None:
        """⛔ `clickCount` GROWS between hits: 1, then 2. It's that field
        that gives birth to `dblclick`, not the interval. And the
        release's `buttons` is zero, because it describes what stays
        pressed AFTERWARD.

        ⛔ `modifiers` is what the CALLER asked for, and it is ORed with what
        the keyboard is really holding rather than replacing it - a click made
        inside `keyboard.down("Shift")` and one made with
        `modifiers=["Shift"]` have to reach the page identically. It used to be
        dropped entirely: `event.shiftKey` came back false on a click the
        caller had explicitly modified, with no error anywhere."""
        plan = self._click_plan(clicks, delay_ms)
        for n in range(1, clicks + 1):
            dwell_ms, gap_ms = plan[n - 1]
            self._mouse_event("mousedown", point, button=button,
                              buttons=BUTTON_MASK[button], click_count=n,
                              modifiers=modifiers)
            if dwell_ms > 0.0:
                time.sleep(dwell_ms / 1000.0)
            self._mouse_event("mouseup", point, button=button, buttons=0,
                              click_count=n, modifiers=modifiers)
            if gap_ms > 0.0:
                time.sleep(gap_ms / 1000.0)

    def _click_plan(self, clicks: int, delay_ms: Optional[float] = None):
        """How long each press lasts and how long until the next one.

        ⛔ A PRESS WITH NO DURATION IS A PRESS NO HAND MADE. `mousedown` and
        `mouseup` used to leave together, so what a page measured as the hold
        was one protocol round trip, and the two presses of a double click had
        nothing between them either - delivered as a `dblclick` regardless,
        because that event is born from `clickCount` and not from the interval,
        so a page saw a double click no operating system would have accepted.

        ⛔ `delay_ms` IS THE CALLER'S OVERRIDE and Playwright documents it in
        milliseconds - the wait between `mousedown` and `mouseup`. It used to
        be dropped here, so the only rhythm available was none.
        """
        if delay_ms:
            return [(float(delay_ms), 0.0)] * clicks
        if self.pointer_persona is None:
            return [(0.0, 0.0)] * clicks
        from .._behaviour import plan_click
        self._click_nonce += 1
        return plan_click(self.pointer_persona, clicks, nonce=self._click_nonce)

    def fill(self, selector: str, text: str, *, timeout: float = 30.0,
             frame_id: Optional[str] = None,
             element_id: Optional[str] = None, **opts):
        """Writes into a field.

        ⛔ It doesn't just write `element.value = ...`: a site listening
        for `input`/`change` would see nothing. The injected script does
        the mutation and says what's needed next - `needsinput` if the
        text still needs typing, `done` if the value was set and only the
        events are missing. And those events are requested from
        `Page.dispatchTrustedInputEvents`, or they come out with
        `isTrusted: false`, which is the tell measured in [B175].
        """
        def run(f, element, point):
            self.inj.call(f, "(injected, el) => injected.focusNode(el, true)",
                          {"objectId": element})
            result = self.inj.call(
                f, "(injected, el, v) => injected.fill(el, v)",
                {"objectId": element}, text)
            if isinstance(result, str) and result.startswith("error:"):
                raise EvaluationError("fill: %s" % result)
            if result == "needsinput":
                if text:
                    self._type(text)
                else:
                    # Clearing a field doesn't generate keystrokes: the
                    # events still need to be requested, or the page
                    # doesn't know it changed.
                    self._trusted_events(f, element, ["input", "change"])
            else:
                self._trusted_events(f, element, ["input", "change"])
            return result
        return self._retry(selector, run, element_id=element_id,
                           states=["visible", "stable", "enabled",
                                   "editable"],
                           timeout=timeout, frame_id=frame_id, **opts)

    # ── the tools ───────────────────────────────────────────────────────────
    def _mouse_event(self, event_type: str, point, *, button: int = 0,
                     buttons: int = 0, click_count: Optional[int] = None,
                     modifiers: int = 0):
        p = {"type": event_type, "x": point[0], "y": point[1],
             "button": button, "buttons": buttons,
             # The modifiers come from the KEYBOARD unless the caller
             # imposes them: a click with Shift down must say so, or the
             # page sees a plain click while the user was making a
             # different one.
             "modifiers": modifiers or self.keyboard.modifier_mask()}
        if click_count is not None:
            p["clickCount"] = click_count
        answer = self.c.send("Page.dispatchMouseEvent", p,
                             session=self.session, timeout=10)
        # The id the renderer will ack once it has handled this event. Kept so
        # that the landing question after a commit waits for exactly the last
        # event the commit sent; 0 from an engine that does not return one, in
        # which case the question is asked without waiting. [B217]
        self._last_event_id = (answer or {}).get("eventId", 0)
        self.position = (point[0], point[1])

    def _type(self, text: str):
        """Character-by-character typing.

        ⛔ It's a handoff to the keyboard, and the reason it isn't written
        here anymore is a measured defect: this function used to send
        `code: ""` and `keyCode: 0` for EVERY character. The event still
        fires, the text shows up in the field, the action succeeds and
        the tests pass - while the page reads an empty `event.code` on a
        key that every real Firefox names. The real layout lives in
        `keylayout.py`, generated from the bundle.

        ⛔ And characters the US layout doesn't have (an ideogram, an
        emoji) are not TYPED: `keyboard.type` rejects them and they go to
        `Page.insertText`. Faking a keypress for a key that doesn't exist
        is exactly the defect above, in a form that's harder to see.
        """
        try:
            self.keyboard.type(text)
        except UnknownKey:
            # ⛔ ONLY this exception, and not `Exception`: a transport
            # error mid-typing would get swallowed and the text
            # reinserted from scratch, doubling what had already gone in.
            self.keyboard.insert_text(text)

    def _trusted_events(self, frame_id: str, element: str, types: list):
        """⛔ Goes through the command our OWN fork added to Juggler.

        Dispatching from the injected script would produce `isTrusted:
        false`, and mixing trusted and untrusted events on the same form
        is a cheaper tell than any single signal: no enumeration API, just
        one `addEventListener`. Measured in [B175].
        """
        self.c.send("Page.dispatchTrustedInputEvents",
                    {"frameId": frame_id, "objectId": element,
                     "types": types},
                    session=self.session, timeout=10)
