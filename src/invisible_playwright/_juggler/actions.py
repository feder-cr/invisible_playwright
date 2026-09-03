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

from .injected import EvaluationError
from .keyboard import BUTTON_MASK, Keyboard, UnknownKey

#: The states a pointer action requires, in the order Playwright asks for
#: them. `stable` is the most expensive (waits two frames) and comes first
#: because it is also the one that most often isn't true yet.
ACTION_STATES = ["visible", "stable", "enabled"]


class ElementNotActionable(TimeoutError):
    """The loop timed out. The message carries the reason for the LAST turn."""


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
    def __init__(self, connection, session: str, lifecycle, injected):
        self.c = connection
        self.session = session
        self.lifecycle = lifecycle
        self.inj = injected
        #: ⛔ A SINGLE keyboard per page, and that's the point: it holds
        #: the state of the modifiers. Building one per action would lose
        #: "Shift is down" between a `down` and the next key, and
        #: `Shift+a` would type `a`.
        self.keyboard = Keyboard(connection, session)
        #: The last pointer position. Used by the wheel and by drag and
        #: drop, which start from where the mouse IS - not from 0,0.
        self.position = (0.0, 0.0)

    # ── geometry ────────────────────────────────────────────────────────────
    def _in_viewport(self, point) -> bool:
        """Is this main-frame point somewhere an event can actually land?

        ⛔ Asked of the PAGE, not of a stored viewport size. The window can be
        resized, and a value cached at launch is a second source for a fact the
        page already knows.
        """
        try:
            size = self.inj.evaluate(
                self.lifecycle.main_frame,
                "({w: window.innerWidth, h: window.innerHeight})")
        except Exception:
            # ⛔ Unknown is not "outside": answering False here would scroll
            # on every action the moment this probe broke.
            return True
        if not isinstance(size, dict):
            return True
        return (0 <= point[0] <= size.get("w", 0)
                and 0 <= point[1] <= size.get("h", 0))

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
               position=None, element_id: Optional[str] = None):
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
                    element = self.inj.query_selector(f, selector)
                    ours = True
                if not element:
                    reason = "the selector finds nothing"
                elif states:
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
                # ⛔ This too is a condition of the WORLD, not a failure:
                # the page moved between the check and the event. We start
                # over, and the point gets recalculated on the new
                # geometry.
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
    def _with_hit_target(self, f, element, point, kind, act):
        """Acts ONLY if the event truly lands on the intended element.

        ⛔ This is NOT one more check before acting: it's an interceptor
        installed FOR THE WHOLE DURATION of the action. The difference
        matters, because a check beforehand leaves open exactly the window
        this closes - the page can move between the check and the event.
        Here the listener watches the event WHILE it arrives, and if the
        point doesn't belong to the intended element it BLOCKS it and says
        so.

        It's the driver's own mechanism (`setupHitTargetInterceptor`), so
        it adds no new surface: the listeners that serve it are already
        installed, **in the utility world** after the fix in
        `31-client-fork.md` §3.9. In the page world they would be
        countable.
        """
        # ⛔ THE PRELIMINARY POINT ONLY IN THE MAIN FRAME, and the reason is a
        # coordinate space, not a preference. `Page.getContentQuads` answers in
        # the MAIN frame's space - `getBoxQuads({relativeTo: mainFrame})`, read
        # in `PageAgent.js` - which is exactly what `dispatchMouseEvent` wants.
        # But the interceptor runs inside the TARGET frame's document, and its
        # preliminary `expectHitTarget(hitPoint, el)` calls `elementFromPoint`
        # THERE. Handing it a main-frame point inside a nested iframe asks the
        # child about a coordinate that means something else, and it answers
        # `<html>` - measured as `the event would have landed elsewhere` on an
        # element that was sitting right under the pointer.
        #
        # Passing null skips only the check BEFORE the event. The listener
        # still validates each event as it arrives, using the event's OWN
        # clientX/clientY, which are already in the right document. The
        # guarantee is unchanged; a covered element in a child frame is caught
        # at dispatch time instead of a moment earlier.
        in_main = f == self.lifecycle.main_frame
        h = self.inj.call(
            f,
            "(injected, el, a, p) => {"
            "  const r = injected.setupHitTargetInterceptor(el, a, p, false);"
            "  return typeof r === 'string' ? {error: r} : {stop: r.stop}; }",
            {"objectId": element}, kind,
            {"x": point[0], "y": point[1]} if in_main else None,
            by_value=False)
        try:
            failure = self.inj.call(f, "(injected, h) => h.error || ''",
                                    {"objectId": h})
            if failure:
                raise WrongHitTarget(failure)
            result = act()
            # ⛔ `stop()` returns `"done"` OR an object describing what was
            # actually hit. Reading it as a boolean would always say yes,
            # which is the same defect as `elementState`.
            done = self.inj.call(
                f, "(injected, h) => { const r = h.stop ? h.stop() : 'done';"
                   " return typeof r === 'string' ? r : JSON.stringify(r); }",
                {"objectId": h})
            if done != "done":
                raise WrongHitTarget(done)
            return result
        finally:
            self.inj.dispose(f, h)

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
              position=None, element_id: Optional[str] = None):
        def run(f, element, point):
            return self._with_hit_target(
                f, element, point, "hover",
                lambda: self._mouse_event("mousemove", point) or point)
        return self._retry(selector, run, timeout=timeout, element_id=element_id,
                           frame_id=frame_id, position=position)

    def click(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None, button: int = 0,
              clicks: int = 1, position=None, modifiers: int = 0, element_id: Optional[str] = None):
        def run(f, element, point):
            def act():
                # The order is that of a user: approach, press, release.
                # Skipping the mousemove leaves the page without the
                # hover, and there are sites that open the menu right
                # there.
                # ⛔ The move carries the modifiers too. A page that reads
                # `event.shiftKey` on `mouseover` - menus do - would otherwise
                # see an unmodified approach followed by a modified click,
                # which no real input device produces.
                self._mouse_event("mousemove", point, modifiers=modifiers)
                self._click_at_point(point, button=button, clicks=clicks,
                                     modifiers=modifiers)
                return point
            return self._with_hit_target(f, element, point, "mouse", act)
        return self._retry(selector, run, timeout=timeout, frame_id=frame_id,
                           position=position, element_id=element_id)

    def dblclick(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
                 button: int = 0, position=None, modifiers: int = 0):
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
                          modifiers=modifiers)

    def check(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
              position=None, element_id: Optional[str] = None):
        return self._set_checked(selector, True, timeout=timeout, element_id=element_id,
                                 frame_id=frame_id, position=position)

    def uncheck(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
                position=None, element_id: Optional[str] = None):
        return self._set_checked(selector, False, timeout=timeout, element_id=element_id,
                                 frame_id=frame_id, position=position)

    def _set_checked(self, selector: str, wanted: bool, *, timeout: float,
                     frame_id: Optional[str] = None, position=None, element_id: Optional[str] = None):
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

            def act():
                self._mouse_event("mousemove", point)
                self._click_at_point(point)
            # ⛔ GOES THROUGH THE INTERCEPTOR like `click`, and it isn't a
            # finishing touch: the first draft called `_click_at_point`
            # directly and failed on the very page that shifts its layout
            # at 1200 ms. One single place knows how to click; two know it
            # only until one of them learns something the other doesn't.
            self._with_hit_target(f, element, point, "mouse", act)
            if not self.inj.element_state(f, element, state):
                raise EvaluationError(
                    "clicked but the box stayed %s: someone intercepted "
                    "the click or put the value back"
                    % ("unchecked" if wanted else "checked"))
            return state
        return self._retry(selector, run, timeout=timeout, element_id=element_id,
                           frame_id=frame_id, position=position)

    def focus(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None, element_id: Optional[str] = None):
        """⛔ Does NOT require `visible`: `focus()` works on an off-screen
        element, and imposing the pointer states would time out an action
        that would have succeeded. Playwright does the same."""
        def run(f, element, point):
            return self.inj.call(
                f, "(injected, el) => injected.focusNode(el, true)",
                {"objectId": element})
        return self._retry(selector, run, states=[], timeout=timeout, frame_id=frame_id,
                           element_id=element_id)

    def blur(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None):
        def run(f, element, point):
            return self.inj.call(
                f,
                "(injected, el) => { if (!el.isConnected) return "
                "'error:notconnected'; el.blur(); return 'done'; }",
                {"objectId": element})
        return self._retry(selector, run, states=[], timeout=timeout, frame_id=frame_id,
)

    def select_text(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None):
        def run(f, element, point):
            r = self.inj.call(f, "(injected, el) => injected.selectText(el)",
                              {"objectId": element})
            if isinstance(r, str) and r.startswith("error:"):
                raise EvaluationError("selectText: %s" % r)
            return r
        return self._retry(selector, run, states=["visible"],
                           timeout=timeout, frame_id=frame_id)

    def select_option(self, selector: str, options, *, timeout: float = 30.0, frame_id: Optional[str] = None, element_id: Optional[str] = None):
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
                           element_id=element_id)

    def dispatch_event(self, selector: str, event_type: str, detail=None, *,
                       timeout: float = 30.0, frame_id: Optional[str] = None):
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
)

    def press(self, selector: str, key: str, *, timeout: float = 30.0, frame_id: Optional[str] = None, element_id: Optional[str] = None):
        """`press`: focuses and presses, with the modifiers from the name."""
        def run(f, element, point):
            self.inj.call(f, "(injected, el) => injected.focusNode(el, true)",
                          {"objectId": element})
            self.keyboard.press(key)
            return key
        return self._retry(selector, run,
                           states=["visible", "stable", "enabled"],
                           timeout=timeout, frame_id=frame_id,
                           element_id=element_id)

    def type_text(self, selector: str, text: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
                  delay: float = 0.0, element_id: Optional[str] = None):
        """`type`: one key per character, WITHOUT clearing first.

        ⛔ It isn't `fill`: that one replaces the content, this one
        appends to it. Swapping them is the easiest way to end up with
        `foobar` in a field that was meant to hold `bar`.
        """
        def run(f, element, point):
            self.inj.call(f, "(injected, el) => injected.focusNode(el, true)",
                          {"objectId": element})
            self.keyboard.type(text, delay=delay)
            return text
        return self._retry(selector, run,
                           states=["visible", "stable", "enabled"],
                           timeout=timeout, frame_id=frame_id,
                           element_id=element_id)

    def set_input_files(self, selector: str, files, *, timeout: float = 30.0, frame_id: Optional[str] = None):
        """`set_input_files`. The paths are ABSOLUTE and the browser
        resolves them.

        ⛔ Goes through `Page.setFileInputFiles` and not the injected
        script: a page can't construct a `FileList`, and trying would
        leave the input empty with no error.
        """
        def run(f, element, point):
            self.c.send("Page.setFileInputFiles",
                        {"frameId": f, "objectId": element,
                         "files": [str(p) for p in files]},
                        session=self.session, timeout=30)
            return list(files)
        return self._retry(selector, run, states=[], timeout=timeout, frame_id=frame_id,
)

    def tap(self, selector: str, *, timeout: float = 30.0, frame_id: Optional[str] = None,
            position=None):
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
                           frame_id=frame_id, position=position)

    def drag_and_drop(self, source: str, target: str, *,
                      timeout: float = 30.0,
                      frame_id: Optional[str] = None):
        """`drag_and_drop`, in four beats.

        ⛔ THE FIRST `mousemove` AFTER THE `mousedown` IS NOT SKIPPED.
        Gecko gives birth to a drag from a movement with the button held
        down: press and release on the target, and you've made two
        clicks. And the two ends are resolved SEPARATELY, each with its
        own retry loop, because taking the second point before pressing
        the first would measure it on a page that is about to change.
        """
        start = self._retry(source, lambda f, el, p: p, timeout=timeout, frame_id=frame_id)
        self._mouse_event("mousemove", start)
        self._mouse_event("mousedown", start, buttons=BUTTON_MASK[0],
                          click_count=1)

        def run(f, element, point):
            # Two movements: one gives birth to the drag, the second one
            # carries it onto the target. With only one, Gecko sometimes
            # doesn't start it.
            self._mouse_event("mousemove", point, buttons=BUTTON_MASK[0])
            self._mouse_event("mousemove", point, buttons=BUTTON_MASK[0])
            self._mouse_event("mouseup", point, buttons=0, click_count=1)
            return point
        try:
            return self._retry(target, run, timeout=timeout, frame_id=frame_id)
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
                clicks: int = 1):
        self.move(x, y)
        self._click_at_point((x, y), button=button, clicks=clicks)

    def wheel(self, dx: float, dy: float) -> None:
        """`mouse.wheel`, from where the pointer IS - not from 0,0."""
        self.c.send("Page.dispatchWheelEvent",
                    {"x": self.position[0], "y": self.position[1],
                     "deltaX": dx, "deltaY": dy, "deltaZ": 0,
                     "modifiers": self.keyboard.modifier_mask()},
                    session=self.session, timeout=10)

    def _click_at_point(self, point, *, button: int = 0,
                        clicks: int = 1, modifiers: int = 0) -> None:
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
        for n in range(1, clicks + 1):
            self._mouse_event("mousedown", point, button=button,
                              buttons=BUTTON_MASK[button], click_count=n,
                              modifiers=modifiers)
            self._mouse_event("mouseup", point, button=button, buttons=0,
                              click_count=n, modifiers=modifiers)

    def fill(self, selector: str, text: str, *, timeout: float = 30.0, frame_id: Optional[str] = None, element_id: Optional[str] = None):
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
                           timeout=timeout, frame_id=frame_id)

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
        self.c.send("Page.dispatchMouseEvent", p,
                    session=self.session, timeout=10)
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
