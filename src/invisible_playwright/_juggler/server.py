"""The Python side of the Playwright protocol, backed by Juggler.

⛔ EVERY SHAPE IN THIS FILE WAS MEASURED, NOT DEDUCED. `scripts/capture_protocol.py`
records a real session against the Node driver, and the initializers, the object
parentage, the event names and the ordering below come from that recording. The
difference matters: reading `_impl` tells you which initializer fields are
CONSUMED today, and the fields a future code path will want are exactly the ones
that reading cannot show you.

**WHAT THE RECORDING SAID, and some of it is not what you would guess:**

- `goto`, `click`, `fill`, `title`, `content` and `querySelector` are sent to
  the **Frame**, not the Page. The Page owns almost nothing.
- `mouseMove` IS sent to the Page - and in a humanised session it arrives
  nineteen times for one click, because the cursor travels.
- `BrowserContext` is created with three children already alive - `Debugger`,
  `Tracing`, `APIRequestContext` - and each is announced BEFORE the context that
  names it, then re-parented with `__adopt__`.
- A `Frame` is created before its `Page` and adopted afterwards, so the Page
  initializer can point at a `mainFrame` that already exists.

**THE ORDER IS THE PROTOCOL.** `Connection.dispatch` looks a guid up in a plain
dict and raises `Cannot find object` when it is missing, so a `__create__` that
arrives after the message naming it is not a race that usually works: it is a
hard failure. Everything here creates first and returns the channel second.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from . import connection as juggler
from .actions import Actions
from .dispatcher import Dispatcher, ProtocolException, Server
from .injected import InjectedScript
from .keyboard import MODIFIER_MASK
from .lifecycle import Lifecycle

# ⛔ RE-EXPORTED ON PURPOSE, not merely imported. These leaf helpers moved out
# of this file so that the classes below read as one story instead of being
# interleaved with functions - but `tests/gates/prefs_byte_parity.py` and the
# transport tests import `_write_user_js`, `_serialize`, `_host_of` and
# `_domain_matches` from HERE, and a move that breaks its callers to tidy a
# file is not a tidy-up. One definition, two names to reach it.
from ._marshal import (_as_callable, _button, _console_text, _deserialize,
                       _guid_of, _headers_array, _js_string, _location,
                       _resource_type, _serialize, _with_argument)
# ⛔ THE PARSER IS THE CORE'S, and briefly it was not: a copy of it lived
# here for the length of one fix. Two readings of what a proxy is would be
# the same duplication that produced the defect, one layer down.
from invisible_core import parse_proxy

from ._profile import (_domain_matches, _host_of, _only_set,
                       _read_version, _remove_profile, _write_user_js)












# ── the leaves we do not implement, and say so ──────────────────────────────
class DisposableDispatcher(Dispatcher):
    """The handle `add_init_script` gives back so a caller can remove it.

    ⛔ IT IS PART OF THE CONTRACT, NOT AN EXTRA. `_page.py` and
    `_browser_context.py` both wrap the reply in `from_channel(...)`, so a
    server that answers `None` does not merely lose the handle - it raises
    `AttributeError: 'NoneType' object has no attribute '_object'` inside the
    client, on a call that otherwise worked. The first implementation here
    returned None and that is exactly what happened.
    """

    TYPE = "Disposable"
    METHODS = {"dispose": "op_dispose"}

    def __init__(self, server, parent, undo) -> None:
        self._undo = undo
        super().__init__(server, parent, {})

    def op_dispose(self, params: Dict) -> Any:
        try:
            self._undo()
        except Exception:
            # ⛔ A caller tidying up must not be handed an error because the
            # page it belonged to is already gone.
            pass
        self.dispose()
        return None


class RefusingDispatcher(Dispatcher):
    """An object that exists so the tree is well formed, and refuses the rest.

    ⛔ IT EXISTS BECAUSE THE PROTOCOL REQUIRES IT, NOT BECAUSE WE SUPPORT IT.
    `BrowserContext`'s initializer names a `tracing`, a `debugger` and a
    `requestContext`, and `_browser_context.py` reads all three with
    `from_channel` at construction time. Leaving them out does not disable
    tracing: it raises a KeyError before any page exists.

    So they are created, and every method on them refuses with a reason. That is
    the whole of section 5.4: out of perimeter must FAIL LOUDLY, never no-op and
    never AttributeError.
    """

    REASON = "not implemented"

    def call(self, method: str, params: Dict) -> Any:
        if method in self.METHODS:
            return super().call(method, params)
        raise ProtocolException(
            "%s.%s is outside what invisible_playwright implements: %s. This "
            "is a deliberate refusal, not a gap - see "
            "32-stacco-da-playwright.md section 5.4."
            % (self.TYPE, method, self.REASON))


class TracingDispatcher(RefusingDispatcher):
    TYPE = "Tracing"
    REASON = ("tracing is outside the automation core; it records a session "
              "for the trace viewer and drives none of it")
    METHODS = {"tracingStop": "stop_noop", "tracingStopChunk": "stop_noop"}

    def stop_noop(self, params: Dict) -> Any:
        # ⛔ These two answer instead of refusing because `close()` calls them
        # on the way out: refusing here would turn every clean shutdown into an
        # error about a feature the caller never asked for.
        return {"artifact": None}


class DebuggerDispatcher(RefusingDispatcher):
    TYPE = "Debugger"
    REASON = "the inspector and its paused state are not part of this fork"


class APIRequestContextDispatcher(RefusingDispatcher):
    TYPE = "APIRequestContext"
    REASON = ("APIRequestContext performs HTTP outside the page, which is not "
              "browser automation and carries none of the browser fingerprint")
    METHODS = {"dispose": "dispose_self"}

    def dispose_self(self, params: Dict) -> Any:
        self.dispose()
        return None


# ── handles ─────────────────────────────────────────────────────────────────
class ElementHandleDispatcher(Dispatcher):
    TYPE = "ElementHandle"
    METHODS = {
        "dispose": "op_dispose",
        "boundingBox": "op_bounding_box",
        "evaluateExpression": "op_evaluate",
        "textContent": "op_text_content",
        "innerText": "op_inner_text",
        "innerHTML": "op_inner_html",
        "inputValue": "op_input_value",
        "getAttribute": "op_get_attribute",
        "scrollIntoViewIfNeeded": "op_scroll_into_view",
        "ownerFrame": "op_owner_frame",
        "contentFrame": "op_content_frame",
        "getProperty": "op_get_property",
        "getPropertyList": "op_get_property_list",
        "jsonValue": "op_json_value",
    }

    def __init__(self, server, frame: "FrameDispatcher", object_id: str,
                 preview: str = "", *, world: str = "utility") -> None:
        self.frame = frame
        self.object_id = object_id
        # ⛔ WHICH WORLD THE objectId BELONGS TO. Almost every handle here is
        # born in the utility world, where the rest of this server lives. One
        # is not: `waitForFunction` runs the CALLER'S expression in the page's
        # own world, so its result exists only there - and an objectId is not
        # portable between worlds. A handle that lies about its world does not
        # fail loudly: Juggler answers that the object does not exist, on a
        # call the user thinks succeeded.
        self.world = world
        # ⛔ THE PARENT IS THE FRAME, NOT THE PAGE, and the parent is not
        # bookkeeping: `_element_handle.py` does `self._frame = cast("Frame",
        # parent)` - the client learns which frame a handle belongs to from the
        # guid tree and from nothing else. With the Page as parent, every call
        # that reads a timeout off that frame died with
        # `'Page' object has no attribute '_timeout'`, an error that names
        # neither the handle nor the parentage.
        #
        # ⛔ Measured against the driver on the same session: it creates
        # ElementHandle as a child of Frame, and this server created it as a
        # child of Page. The protocol diff did not catch it because it compared
        # types, initializer fields and events - not PARENTAGE, which is now a
        # fourth dimension it checks.
        super().__init__(server, frame,
                         {"preview": preview or "JSHandle@node"})

    # ⛔ Same reason as `page` just above, one level further: an element handle
    # reaches the injected script through the frame and the page, and thirteen
    # call sites used to spell that path out.
    @property
    def injected(self) -> "InjectedScript":
        return self.page.injected


    @property
    def page(self) -> "PageDispatcher":
        return self.frame.page

    def op_dispose(self, params: Dict) -> Any:
        try:
            self.injected.dispose(self.frame.frame_id, self.object_id)
        except Exception:
            # ⛔ A handle whose context is already gone is not an error worth
            # raising: the caller is tidying up, and the node it pointed at
            # stopped existing on its own.
            pass
        self.dispose()
        return None

    def op_bounding_box(self, params: Dict) -> Any:
        return {"value": self.injected.bounding_box(
            self.frame.frame_id, self.object_id)}

    def op_evaluate(self, params: Dict) -> Any:
        """`handle.evaluate(fn, arg)` - and the SECOND argument is the point.

        ⛔ IT USED TO CALL `r(el)` AND DROP `arg` ON THE FLOOR, and that was
        not a corner: Playwright's contract is `fn(element, arg)`, so every
        caller passing data got `undefined` where their value should be. The
        expression then threw inside the page, the client turned it into an
        evaluation error, and callers that treat a failed probe as "no" - which
        is the sane way to write a probe - simply took the wrong branch in
        silence.

        ⛔ THE COST, MEASURED. The humanised cursor asks the element whether an
        off-centre point actually lands on it (`_cursor._hits`, which passes
        `{x, y}` as exactly this argument). With `arg` dropped the check threw
        every single time, was caught, and answered False - so the landing
        override was never applied and EVERY click fell on the exact geometric
        centre of its element. That is the tell `_landing_override`'s own
        docstring describes: one exact number, identical in every install,
        readable from a single event. The whole landing feature was inert on
        this transport and nothing failed.

        It was found by diffing the protocol against the Node driver: the
        driver's `click` carried a `position` and ours did not.
        """
        return {"value": _serialize(self.injected.call(
            self.frame.frame_id,
            "(injected, el) => { const r = (%s);"
            "  return typeof r === 'function' ? r(el, %s) : r; }"
            % (params["expression"],
               json.dumps(_deserialize(params.get("arg")), default=str)),
            {"objectId": self.object_id}))}

    def op_text_content(self, params: Dict) -> Any:
        return {"value": self.injected.text_content(
            self.frame.frame_id, self.object_id)}

    def op_inner_text(self, params: Dict) -> Any:
        return {"value": self.injected.inner_text(
            self.frame.frame_id, self.object_id)}

    def op_inner_html(self, params: Dict) -> Any:
        return {"value": self.injected.inner_html(
            self.frame.frame_id, self.object_id)}

    def op_input_value(self, params: Dict) -> Any:
        return {"value": self.injected.input_value(
            self.frame.frame_id, self.object_id)}

    def op_get_attribute(self, params: Dict) -> Any:
        return {"value": self.injected.get_attribute(
            self.frame.frame_id, self.object_id, params["name"])}

    def op_get_property(self, params: Dict) -> Any:
        object_id = self.injected.call(
            self.frame.frame_id,
            "(injected, o, n) => o[n]",
            {"objectId": self.object_id}, params["name"], by_value=False)
        handle = ElementHandleDispatcher(self.server, self.frame, object_id)
        return {"handle": handle.channel}

    def op_get_property_list(self, params: Dict) -> Any:
        """⛔ ONE HANDLE PER PROPERTY, and each one holds its value alive. On a
        large object this is the most expensive call in the file, which is why
        `json_value` exists and should be preferred when the values are
        serialisable."""
        names = self.injected.call(
            self.frame.frame_id,
            "(injected, o) => o === Object(o) ? Object.keys(o) : []",
            {"objectId": self.object_id}) or []
        out = []
        for name in names:
            object_id = self.injected.call(
                self.frame.frame_id, "(injected, o, n) => o[n]",
                {"objectId": self.object_id}, name, by_value=False)
            handle = ElementHandleDispatcher(self.server, self.frame, object_id)
            out.append({"name": name, "value": handle.channel})
        return {"properties": out}

    def op_json_value(self, params: Dict) -> Any:
        """The handle's value, read IN THE WORLD THE HANDLE LIVES IN.

        ⛔ Almost every handle here is a utility-world one and takes the first
        branch. The exception is what `waitForFunction` hands back: the
        caller's expression ran in the page's own world, so its result exists
        only there. Reading it through the utility world asks Juggler about an
        objectId that context has never seen, and the answer is not an error a
        caller can act on - it is an evaluation failure on a handle they were
        just given.
        """
        if getattr(self, "world", "utility") == "main":
            return {"value": _serialize(self.injected.json_value_in(
                self.frame.frame_id, "main", self.object_id))}
        return {"value": _serialize(self.injected.call(
            self.frame.frame_id, "(injected, o) => o",
            {"objectId": self.object_id}))}

    def op_scroll_into_view(self, params: Dict) -> Any:
        """⛔ [B184]: this does not work in the shipped engine, and it does not
        work through the Node driver either. It is wired correctly here so the
        day the engine is fixed nothing else has to change, and the failure
        arrives from the engine rather than from a missing method."""
        self.page.send("Page.scrollIntoViewIfNeeded",
                       _only_set({"frameId": self.frame.frame_id,
                                  "objectId": self.object_id,
                                  "rect": params.get("rect")}))
        return None

    def op_owner_frame(self, params: Dict) -> Any:
        return {"frame": self.frame.channel}

    def op_content_frame(self, params: Dict) -> Any:
        """The frame this element CONTAINS, for an iframe.

        ⛔ Answers null rather than raising when the element is not a frame
        owner: that is what the client expects, and raising would turn an
        ordinary "this div is not an iframe" into a failed script.
        """
        result = self.page.send("Page.describeNode",
                                {"frameId": self.frame.frame_id,
                                 "objectId": self.object_id}) or {}
        content_frame_id = result.get("contentFrameId")
        if not content_frame_id:
            return {"frame": None}
        frame = self.page.frame_for(content_frame_id)
        return {"frame": frame.channel}




# ── frame ───────────────────────────────────────────────────────────────────
class FrameDispatcher(Dispatcher):
    TYPE = "Frame"
    METHODS = {
        "goto": "op_goto",
        "querySelector": "op_query_selector",
        "click": "op_click",
        "fill": "op_fill",
        "title": "op_title",
        "content": "op_content",
        "textContent": "op_text_content",
        "innerText": "op_inner_text",
        "innerHTML": "op_inner_html",
        "inputValue": "op_input_value",
        "getAttribute": "op_get_attribute",
        "isVisible": "op_is_visible",
        "isHidden": "op_is_hidden",
        "isEnabled": "op_is_enabled",
        "isDisabled": "op_is_disabled",
        "isChecked": "op_is_checked",
        "isEditable": "op_is_editable",
        "hover": "op_hover",
        "dblclick": "op_dblclick",
        "check": "op_check",
        "uncheck": "op_uncheck",
        "focus": "op_focus",
        "blur": "op_blur",
        "selectText": "op_select_text",
        "press": "op_press",
        "type": "op_type",
        "evaluateExpression": "op_evaluate",
        "evaluateExpressionHandle": "op_evaluate_handle",
        "querySelectorAll": "op_query_selector_all",
        "queryCount": "op_query_count",
        "waitForSelector": "op_wait_for_selector",
        "waitForFunction": "op_wait_for_function",
        "waitForTimeout": "op_wait_for_timeout",
        "setContent": "op_set_content",
        "evalOnSelector": "op_eval_on_selector",
        "evalOnSelectorAll": "op_eval_on_selector_all",
        "selectOption": "op_select_option",
        "setInputFiles": "op_set_input_files",
        "tap": "op_tap",
        "dispatchEvent": "op_dispatch_event",
        "dragAndDrop": "op_drag_and_drop",
        "frameElement": "op_frame_element",
        "expect": "op_expect",
        "resolveSelector": "op_resolve_selector",
        "ariaSnapshot": "op_aria_snapshot",
        "drop": "op_drop",
        "registerSelectorEngine": "op_register_selector_engine",
        "waitForElementState": "op_wait_for_element_state",
        "setTestIdAttributeName": "op_set_test_id",
    }

    def __init__(self, server, page: "PageDispatcher", frame_id: str,
                 url: str = "about:blank", name: str = "",
                 load_states: Optional[List[str]] = None) -> None:
        self.page = page
        self.frame_id = frame_id
        #: ⛔ Kept here because the INITIALIZER is a snapshot: the client reads
        #: the url from it once, at creation, and afterwards only from
        #: `navigated` events. A frame created before it navigates keeps an
        #: empty url forever unless something updates this.
        self.url = url
        super().__init__(server, page.context,
                         {"url": url, "name": name,
                          "loadStates": load_states or ["commit"]})

    # ── the engines, which belong to the page ───────────────────────────────
    #
    # ⛔ PROPERTIES, AND THE POINT IS HOW MANY PLACES KNOW. A frame does its
    # work through three engines it does not own - the actionability loop, the
    # injected script and the lifecycle - and each one used to be reached by
    # climbing: `self.page.actions`, thirty-eight times across this class.
    # Thirty-eight places knew that the actions engine lives on the page. Now
    # one does, and thirty-eight read as what they mean.
    @property
    def actions(self) -> "Actions":
        return self.page.actions

    @property
    def injected(self) -> "InjectedScript":
        return self.page.injected

    @property
    def lifecycle(self) -> "Lifecycle":
        return self.page.lifecycle


    # ── navigation ──────────────────────────────────────────────────────────
    def op_goto(self, params: Dict) -> Any:
        result = self.lifecycle.goto(
            params["url"], frame_id=self.frame_id,
            until=params.get("waitUntil") or "load",
            timeout=(params.get("timeout") or 30000) / 1000.0)
        self.emit("navigated", {"url": result["url"], "name": "",
                                "newDocument": {"request": None}})
        # ⛔ `goto` answers with a Response CHANNEL or null, never with a URL.
        # `_frame.py` calls `from_nullable_channel` on it.
        return {"response": None}

    # ── reading ─────────────────────────────────────────────────────────────
    def op_title(self, params: Dict) -> Any:
        return {"value": self.injected.title(self.frame_id)}

    def op_content(self, params: Dict) -> Any:
        return {"value": self.injected.content(self.frame_id)}

    def op_query_selector(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        object_id = self.injected.query_selector(frame_id, selector)
        if not object_id:
            return {"element": None}
        handle = ElementHandleDispatcher(
            self.server, self.page.frame_for(frame_id), object_id)
        return {"element": handle.channel}

    def _with_element(self, params: Dict, read):
        frame_id, selector = self.enter_frames(params["selector"])
        object_id = self.injected.query_selector(frame_id, selector)
        if not object_id:
            raise ProtocolException("no element matches %r" % selector)
        try:
            return {"value": read(object_id)}
        finally:
            self.injected.dispose(frame_id, object_id)

    def op_text_content(self, params: Dict) -> Any:
        return self._with_element(params, lambda o: self.injected
                                  .text_content(self.frame_id, o))

    def op_inner_text(self, params: Dict) -> Any:
        return self._with_element(params, lambda o: self.injected
                                  .inner_text(self.frame_id, o))

    def op_inner_html(self, params: Dict) -> Any:
        return self._with_element(params, lambda o: self.injected
                                  .inner_html(self.frame_id, o))

    def op_input_value(self, params: Dict) -> Any:
        return self._with_element(params, lambda o: self.injected
                                  .input_value(self.frame_id, o))

    def op_get_attribute(self, params: Dict) -> Any:
        return self._with_element(params, lambda o: self.injected
                                  .get_attribute(self.frame_id, o,
                                                 params["name"]))

    def _state(self, params: Dict, state: str) -> Any:
        return self._with_element(params, lambda o: self.injected
                                  .element_state(self.frame_id, o, state))

    def op_is_visible(self, params: Dict) -> Any:
        return self._state(params, "visible")

    def op_is_hidden(self, params: Dict) -> Any:
        return self._state(params, "hidden")

    def op_is_enabled(self, params: Dict) -> Any:
        return self._state(params, "enabled")

    def op_is_disabled(self, params: Dict) -> Any:
        return self._state(params, "disabled")

    def op_is_checked(self, params: Dict) -> Any:
        return self._state(params, "checked")

    def op_is_editable(self, params: Dict) -> Any:
        return self._state(params, "editable")

    def op_evaluate(self, params: Dict) -> Any:
        """⛔ THE PAGE'S OWN WORLD, not the utility one, and the distinction
        is the semantics of the API rather than a preference.

        `page.evaluate()` is the user asking to run code AS THE PAGE: it must
        see the page's globals, the page's prototypes, and whatever the site
        has monkey-patched. The utility world sees the same DOM through an
        Xray, so those are exactly what it does NOT see.

        The first draft ran it in utility and it was caught by the opposite
        assertion to the one you would expect: a test checking that the PAGE
        cannot see a closed shadow root found that it could - because the code
        asking was not running as the page at all.

        Everything else in this file stays in utility on purpose. This one
        method leaves, because the caller asked for it.
        """
        return {"value": _serialize(self.injected.evaluate_in_main(
            self.frame_id, _with_argument(params)))}

    # ── acting ──────────────────────────────────────────────────────────────
    # ⛔ frame-crossing selectors
    ENTER_FRAME = " >> internal:control=enter-frame >> "

    def enter_frames(self, selector: str):
        """`(frame_id, tail)` for a selector that crosses into an iframe.

        ⛔ THE HOP IS SERVER-SIDE, and that is not an implementation detail:
        the injected script lives in ONE document and cannot reach inside an
        iframe's - that is the same-origin boundary, not a missing feature. So
        `#outer >> internal:control=enter-frame >> #inner` is resolved here by
        finding `#outer`, asking the engine which frame it CONTAINS, and
        starting again in that frame.

        ⛔ AND IT RECURSES, because frames nest. Handling one level looks
        right on every page that has a single iframe and is wrong on the ones
        that matter - the geometry test in this suite uses two.

        The judge found this as three failures out of seven: they were the only
        ones left after child frames started being announced, and they are all
        the same missing hop.
        """
        frame = self
        while True:
            head, _, tail = selector.partition(self.ENTER_FRAME)
            if not tail:
                return frame.frame_id, head
            owner = frame.page.injected.query_selector(frame.frame_id, head)
            if not owner:
                raise ProtocolException(
                    "the frame owner %r matched nothing, so there is nothing "
                    "to enter" % head)
            try:
                described = frame.page.send(
                    "Page.describeNode",
                    {"frameId": frame.frame_id, "objectId": owner}) or {}
            finally:
                frame.page.injected.dispose(frame.frame_id, owner)
            inner = described.get("contentFrameId")
            if not inner:
                raise ProtocolException(
                    "%r is not a frame owner: it has no content frame to "
                    "enter" % head)
            frame = frame.page.frame_for(inner)
            selector = tail

    def _timeout(self, params: Dict) -> float:
        return (params.get("timeout") or 30000) / 1000.0

    def _pointer(self, params: Dict) -> Dict:
        """`button`, `modifiers` and `clickCount`, read in ONE place.

        ⛔ THEY WERE NEVER READ AT ALL, and the operations looked complete
        because the click happened. `page.click(button="right")` produced a
        LEFT click - no `contextmenu` event, no error - and
        `modifiers=["Shift"]` produced a click whose `event.shiftKey` was
        false. Both are documented options of the API this package promises to
        answer, and both were dropped between the wire and the engine.

        ⛔ AND THE FIELD-COVERAGE GATE COULD NOT SEE IT, which is worth
        writing down rather than hiding: that gate compares what a CAPTURED
        session sent against what the code reads, and the captured session
        clicks once, with the left button and no modifier. A field nobody
        exercised is a field it cannot report. It was the e2e - the slowest and
        least clever instrument here - that caught this.

        ⛔ The masks are Firefox's, not Gecko's, and right and middle are
        swapped in the `buttons` encoding: both tables live in `keyboard.py`
        and are read from there rather than re-derived.
        """
        names = params.get("modifiers") or []
        mask = 0
        for name in names:
            mask |= MODIFIER_MASK.get(name, 0)
        return {
            "button": _button(params.get("button")),
            # ⛔ A modifier the CALLER named is added to whatever the keyboard
            # is actually holding, never substituted for it: a click made
            # inside `keyboard.down("Shift")` and one made with
            # `modifiers=["Shift"]` must reach the page identically.
            "modifiers": mask,
            "clicks": int(params.get("clickCount") or 1),
        }

    def _position(self, params: Dict):
        """The caller's offset inside the element, or None.

        ⛔ ONE PLACE READS IT. Six pointer operations take it, and six copies
        of `params.get("position")` is exactly the shape that lets the seventh
        be written without it - which is how this option came to be dropped by
        all of them at once.
        """
        position = params.get("position")
        if isinstance(position, dict) and "x" in position and "y" in position:
            return position
        return None

    def op_click(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.click(selector, timeout=self._timeout(params),
                                frame_id=frame_id,
                                position=self._position(params),
                                **self._pointer(params))
        return None

    def op_dblclick(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        pointer = self._pointer(params)
        # dblclick sets its own clickCount; the caller's is not a second one.
        pointer.pop("clicks", None)
        self.actions.dblclick(selector, timeout=self._timeout(params),
                                   frame_id=frame_id,
                                   position=self._position(params), **pointer)
        return None

    def op_hover(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.hover(selector, timeout=self._timeout(params),
                                frame_id=frame_id,
                                position=self._position(params))
        return None

    def op_fill(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.fill(selector, params["value"],
                               timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_check(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.check(selector, timeout=self._timeout(params),
                                frame_id=frame_id,
                                position=self._position(params))
        return None

    def op_uncheck(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.uncheck(selector, timeout=self._timeout(params),
                                  frame_id=frame_id,
                                  position=self._position(params))
        return None

    def op_focus(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.focus(selector,
                                timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_blur(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.blur(selector,
                               timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_select_text(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.select_text(selector,
                                      timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_press(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.press(selector, params["key"],
                                timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_type(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.type_text(selector, params["text"],
                                    timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_select_option(self, params: Dict) -> Any:
        # ⛔ A bare string never reaches the option filter: it starts from
        # `matches = true` and narrows only on valueOrLabel / value / label /
        # index, so a plain string matches everything and picks the FIRST
        # option. Measured: ["b"] answered ['a'].
        chosen = self.actions.select_option(
            params["selector"], params.get("options") or [],
            timeout=self._timeout(params))
        return {"values": chosen or []}

    def op_set_input_files(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        paths = [f.get("name") if isinstance(f, dict) else f
                 for f in (params.get("localPaths") or params.get("files") or [])]
        self.actions.set_input_files(selector, paths,
                                          timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_tap(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.tap(selector, timeout=self._timeout(params),
                              frame_id=frame_id,
                              position=self._position(params))
        return None

    def op_dispatch_event(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        # ⛔ THE ARGUMENT ARRIVES IN PLAYWRIGHT'S SERIALISED ENVELOPE, not as
        # a plain value: `{"value": {...}, "handles": []}`. Passing it through
        # is rejected by Juggler's closed-world schema with `Found property
        # "<root>.args[3].handles" - [] which is not described in this scheme`,
        # and the message names a field the caller never wrote. Nothing to do
        # with frames: it failed on the main frame too, and only an iframe test
        # happened to exercise it.
        self.actions.dispatch_event(
            selector, params["type"],
            _deserialize(params.get("eventInit")) or {},
            timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_drag_and_drop(self, params: Dict) -> Any:
        self.actions.drag_and_drop(params["source"], params["target"],
                                        timeout=self._timeout(params))
        return None

    def op_set_content(self, params: Dict) -> Any:
        """⛔ Goes through `document.open/write/close` in the MAIN world.

        The utility world has an EXTENDED principal, and Gecko requires
        `document.open()` to run under a principal EQUAL to the document's, so
        from there it answers `The operation is insecure` every single time -
        the same defect the fork already fixed inside the driver.
        """
        self.injected.evaluate_in_main(
            self.frame_id,
            "(() => { document.open(); document.write(%s); document.close(); })()"
            % _js_string(params["html"]), by_value=True)
        # ⛔ The load states of the OLD document do not count for the new one,
        # and `document.open()` starts a new one without a navigation event to
        # reset them. Waiting here would wait on states that are already set.
        self.lifecycle.wait_for_state(
            self.frame_id, params.get("waitUntil") or "load",
            timeout=self._timeout(params))
        return None

    def op_wait_for_timeout(self, params: Dict) -> Any:
        time.sleep((params.get("timeout") or 0) / 1000.0)
        return None

    def op_wait_for_load_state(self, params: Dict) -> Any:
        self.lifecycle.wait_for_state(
            self.frame_id, params.get("state") or "load",
            timeout=self._timeout(params))
        return None

    def op_wait_for_selector(self, params: Dict) -> Any:
        state = params.get("state") or "visible"
        object_id = self.actions.wait_for_selector(
            params["selector"], state=state, timeout=self._timeout(params))
        if object_id is None:
            return {"element": None}
        handle = ElementHandleDispatcher(self.server, self, object_id)
        return {"element": handle.channel}

    def op_wait_for_function(self, params: Dict) -> Any:
        """Poll the CALLER'S expression until it is truthy.

        ⛔ IN THE PAGE'S OWN WORLD, and it used to poll in the utility one.
        `wait_for_function` has exactly the semantics of `evaluate`: it is the
        caller asking to run THEIR code as the page. From behind the Xray a
        page global does not exist, so an expression like
        `() => !!window.Fingerprint` is false forever and the call dies on its
        own timeout naming an expression that was true in the page all along.

        ⛔ Measured: five e2e tests - every one that waits for a real detector
        library to finish - failed on this transport and passed on the driver.
        Nothing else in the suite waits on a page global, which is why 181 of
        186 were green and the gap looked like a detector problem.

        ⛔ AND IT IS THE SECOND METHOD THAT LEAVES THE XRAY, on purpose and
        for the same reason as `op_evaluate`: everything else in this server
        stays in utility so a site cannot count our reads, and these two leave
        because the caller asked for the page's own world by name. The cost is
        real - a polled expression is repeated page-visible work - so the
        interval stays coarse.
        """
        deadline = time.monotonic() + self._timeout(params)
        expression = _as_callable(params["expression"]).replace(
            "ARG", json.dumps(_deserialize(params.get("arg")), default=str))
        while True:
            value = self.injected.evaluate_in_main(self.frame_id,
                                                        expression)
            if value:
                # ⛔ A HANDLE, NEVER None. `_frame.py` wraps this reply in
                # `from_channel(...)`, so answering None is not "no handle" -
                # it is `AttributeError: 'NoneType' object has no attribute
                # '_object'` raised inside the client on a call that had just
                # succeeded. It stayed hidden while the poll timed out first.
                object_id = self.injected.evaluate_in_main(
                    self.frame_id, expression, by_value=False)
                handle = ElementHandleDispatcher(
                    self.server, self, object_id or "", world="main")
                return {"handle": handle.channel}
            if time.monotonic() > deadline:
                raise ProtocolException(
                    "the expression never became truthy in %.0fs: %s"
                    % (self._timeout(params), expression[:120]))
            time.sleep(0.05)

    def op_expect(self, params: Dict) -> Any:
        """⛔ The polling half of `expect()`. It answers ONE probe; the
        retrying is the client's, in `_assertions.py`, which is why this must
        not loop: looping here would multiply the caller's timeout by ours."""
        expression = params.get("expression") or ""
        selector = params.get("selector")
        try:
            if expression.startswith("to.have.count"):
                count = self.injected.count(self.frame_id, selector)
                expected = int((params.get("expectedNumber") or 0))
                return {"matches": count == expected,
                        "received": _serialize(count)}
            if expression.startswith("to.be."):
                state = expression.split("to.be.", 1)[1]
                return self._with_element(
                    {"selector": selector},
                    lambda o: None) and {"matches": True}
        except ProtocolException:
            return {"matches": False, "received": _serialize(None)}
        raise ProtocolException(
            "expect(%r) is not implemented yet" % expression)

    def op_aria_snapshot(self, params: Dict) -> Any:
        """The accessibility tree, as the injected script builds it.

        ⛔ It runs in the UTILITY world like every other read, which matters
        more here than elsewhere: an aria snapshot walks the WHOLE document and
        reads a role, a name and a description off every node. Done in the
        page's world that is thousands of accessor calls a site could count -
        the most expensive read in the API turned into the loudest one.
        """
        selector = params.get("selector")
        if selector:
            return self._with_element(
                params,
                lambda o: self.injected.call(
                    self.frame_id,
                    "(injected, el, o) => injected.ariaSnapshot(el, o)",
                    {"objectId": o}, {"mode": params.get("mode") or "raw"}))
        return {"snapshot": self.injected.call(
            self.frame_id,
            "(injected, o) => injected.ariaSnapshot(document.documentElement, o)",
            {"mode": params.get("mode") or "raw"})}

    def op_drop(self, params: Dict) -> Any:
        """⛔ REFUSED, and the reason is the protocol rather than the effort.

        `drop` carries an explicit DataTransfer payload - mime types and their
        values - and Juggler declares no command that can inject one:
        `dispatchDragEvent` exists INSIDE the content agent, reachable only
        from `Page.dispatchMouseEvent`, which synthesises the drag from real
        pointer movement and builds its own data.

        So `drag_and_drop()` works, because that is a pointer gesture; `drop()`
        with a payload cannot be expressed. Faking it by dispatching an
        untrusted `drop` event from the injected script would produce
        `isTrusted: false` on a form that saw trusted events for everything
        else - which is the mixture [B175] exists about.
        """
        raise ProtocolException(
            "drop() with an explicit data payload has no engine command: "
            "Juggler builds the DataTransfer from a real pointer drag, so "
            "there is nothing to inject one into. Use drag_and_drop(), which "
            "is a pointer gesture and works")

    def op_register_selector_engine(self, params: Dict) -> Any:
        """⛔ REFUSED for the same reason as `set_test_id_attribute`: the
        engines are handed to the injected script AT CONSTRUCTION, in
        `customEngines`, and the script is built once per frame. Registering
        one afterwards would need every live InjectedScript rebuilt, and a
        rebuild throws away every handle the caller is holding."""
        raise ProtocolException(
            "register_selector_engine() cannot be honoured after a page "
            "exists: custom engines are baked into the injected script when it "
            "is built, and rebuilding it would invalidate every handle the "
            "caller holds")

    def op_resolve_selector(self, params: Dict) -> Any:
        """The frame and selector a locator finally points at.

        ⛔ It answers THIS frame and the selector unchanged, which is correct
        only because frame-crossing locators are not supported here yet: an
        `iframe >> internal:control=enter-frame >> ...` would resolve into a
        child frame upstream. Answering this frame for one of those would be a
        wrong answer rather than a missing feature, so the compound form is
        refused.
        """
        selector = params.get("selector") or ""
        if "enter-frame" in selector:
            raise ProtocolException(
                "a frame-crossing locator (%s) cannot be resolved yet: "
                "answering this frame would be a wrong answer, not a missing "
                "one" % selector[:80])
        return {"frame": self.channel, "selector": selector}

    def op_wait_for_element_state(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        self.actions.wait_for_selector(
            selector, state=params["state"],
            timeout=self._timeout(params), frame_id=frame_id)
        return None

    def op_set_test_id(self, params: Dict) -> Any:
        raise ProtocolException(
            "set_test_id_attribute() cannot be honoured after the injected "
            "script is built: the attribute name is baked into it at "
            "construction. Pass it when the page is created instead of "
            "changing it mid-session")

    # ── frames as objects ───────────────────────────────────────────────────
    def op_frame_element(self, params: Dict) -> Any:
        raise ProtocolException(
            "frameElement needs the owner frame's handle, which this server "
            "does not track yet")

    # ── selectors that answer many ──────────────────────────────────────────
    def op_query_count(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        return {"value": self.injected.count(frame_id, selector)}

    def op_query_selector_all(self, params: Dict) -> Any:
        frame_id, selector = self.enter_frames(params["selector"])
        target = self.page.frame_for(frame_id)
        ids = self.injected.query_selector_all(frame_id, selector)
        handles = [ElementHandleDispatcher(self.server, target, oid)
                   for oid in ids]
        return {"elements": [h.channel for h in handles]}

    def op_eval_on_selector(self, params: Dict) -> Any:
        return self._with_element(
            params,
            lambda o: _serialize(self.injected.call(
                self.frame_id,
                "(injected, el) => { const r = (%s);"
                "  return typeof r === 'function' ? r(el) : r; }"
                % params["expression"],
                {"objectId": o})))

    def op_eval_on_selector_all(self, params: Dict) -> Any:
        value = self.injected.call(
            self.frame_id,
            "(injected, sel) => { const els = injected.querySelectorAll("
            "  injected.parseSelector(sel), document);"
            "  const r = (%s); return typeof r === 'function' ? r(els) : r; }"
            % params["expression"],
            params["selector"])
        return {"value": _serialize(value)}

    def op_evaluate_handle(self, params: Dict) -> Any:
        object_id = self.injected.evaluate_in_main(
            self.frame_id, _with_argument(params), by_value=False)
        handle = ElementHandleDispatcher(self.server, self, object_id)
        return {"handle": handle.channel}


# ⛔ `DialogDispatcher` LIVED HERE AND IS GONE, fused into `_pw._impl._dialog`
# on 2026-08-29 - the first type fused into its impl class rather than merely
# simplified in place. There is no `__create__` for a dialog any more: see
# `PageDispatcher._on_juggler_event`'s `Page.dialogOpened` branch below, which
# constructs the fused object directly and hands it across the thread boundary
# with `call_soon_threadsafe`, and `_pw/_impl/_dialog.py` for the object
# itself, including the auto-answer safety net this docstring used to
# describe.




class RequestDispatcher(Dispatcher):
    TYPE = "Request"
    METHODS = {
        "response": "op_response",
        "rawRequestHeaders": "op_raw_request_headers",
        "failure": "op_failure",
    }

    def __init__(self, server, page: "PageDispatcher", params: Dict) -> None:
        self.page = page
        self.request_id = params.get("requestId")
        self.raw_headers = _headers_array(params.get("headers"))
        self.response: Optional["ResponseDispatcher"] = None
        #: Filled by `Network.requestFailed`. ⛔ None until it fails: an
        #: empty string would say "failed for no reason", which is a different
        #: answer from "did not fail".
        self.failure: Optional[str] = None
        frame_id = params.get("frameId") or page.frame.frame_id
        super().__init__(server, page, {
            "url": params.get("url") or "",
            "method": params.get("method") or "GET",
            "headers": self.raw_headers,
            # ⛔ OMITTED WHEN ABSENT, never sent as null. The client reads
            # `initializer.get("postData")` and base64-decodes anything that is
            # not None, so a null is harmless today and a raw string would be
            # corruption tomorrow; the driver omits the key entirely and this
            # matches it.
            #
            # ⛔ AND ON THIS BUILD IT IS ALWAYS ABSENT, which is OUR doing and
            # not the transport's: `NetworkObserver.js` sets `postData:
            # undefined` in the request event under a stealth patch dated
            # 2026-08-24, whose comment says nobody asks for it without
            # `page.route()` or `request.postData()`. The consequence is that
            # `request.post_data()` answers None for every POST on BOTH
            # transports - a suppressed value rather than a missing feature,
            # which is the shape rule 12 is about. It is recorded here because
            # this is where somebody will come looking.
            **({"postData": params["postData"]}
               if params.get("postData") is not None else {}),
            "isNavigationRequest": bool(params.get("navigationId")),
            "resourceType": _resource_type(params),
            "frame": page.frame_for(frame_id).channel,
        })

    def op_response(self, params: Dict) -> Any:
        return {"response": self.response.channel if self.response else None}

    def op_raw_request_headers(self, params: Dict) -> Any:
        return {"headers": self.raw_headers}

    def op_failure(self, params: Dict) -> Any:
        """⛔ NULL means "it did not fail", and that is a different answer from
        an empty string. `_network.py` reads `errorText` and hands back None
        when there is none, so an empty string here would make every successful
        request look like it failed with a blank reason."""
        return {"error": {"errorText": self.failure} if self.failure else None}


class ResponseDispatcher(Dispatcher):
    TYPE = "Response"
    METHODS = {
        "body": "op_body",
        "rawResponseHeaders": "op_raw_response_headers",
        "securityDetails": "op_security_details",
        "serverAddr": "op_server_addr",
        "sizes": "op_sizes",
        "httpVersion": "op_http_version",
    }

    def __init__(self, server, request: RequestDispatcher,
                 params: Dict) -> None:
        self.request = request
        self.raw_headers = _headers_array(params.get("headers"))
        self.protocol_version = params.get("protocolVersion") or ""
        self.remote = {"ipAddress": params.get("remoteIPAddress") or "",
                       "port": params.get("remotePort") or 0}
        # ⛔ The parent is the REQUEST, which is where the driver puts it.
        # Unlike the ElementHandle case this one is not load-bearing today -
        # `_network.py` reads its request from the INITIALIZER, not from the
        # tree - but the parentage also decides what a disposal takes with it,
        # and a response outliving its request is not a shape anybody has
        # thought about. Matching the driver costs one word.
        super().__init__(server, request, {
            "url": request.initializer["url"],
            "status": params.get("status") or 0,
            "statusText": params.get("statusText") or "",
            "headers": self.raw_headers,
            "request": request.channel,
            "fromServiceWorker": bool(params.get("fromServiceWorker")),
            # ⛔ EVERY timing field is mandatory and -1 means "did not happen".
            # Leaving one out is a KeyError in `_network.py`; leaving it at 0
            # claims the phase took no time, which is a different lie.
            "timing": {"startTime": 0, "domainLookupStart": -1,
                       "domainLookupEnd": -1, "connectStart": -1,
                       "secureConnectionStart": -1, "connectEnd": -1,
                       "requestStart": -1, "responseStart": -1},
        })

    def op_body(self, params: Dict) -> Any:
        """The response body, base64, straight from the engine.

        This raised unconditionally between 2026-08-24 and 2026-08-30, because
        `Network.getResponseBody` had been removed from Juggler while trimming
        it. The command is back, and so is this. Reported from outside by
        someone identifying protection vendors from three signals - URL,
        headers, and the body of the scripts - who was left with two.

        ⛔ ONE KEY, and it has to be one. `_connection.py` asserts
        `len(result) == 1` and returns that value unwrapped, so the engine's
        second field cannot be forwarded even though it is the more
        interesting of the two.
        """
        page = self.request.page

        # ⛔ THE ENGINE ONLY HAS THE BODY ONCE THE REQUEST HAS FINISHED, and
        # `response.body()` upstream does not wait, so calling it from a
        # `page.on("response")` handler is a race that the caller cannot see
        # and cannot fix. It bites the MAIN DOCUMENT almost every time and
        # subresources almost never, because the document is still streaming
        # when its response event fires: measured on a three-request page,
        # the JSON and the script came back and the document answered
        # `Request "17" is not found`.
        #
        # `_requests` holds exactly the requests still in flight - the page's
        # own handler pops them on `requestFinished` and on `requestFailed` -
        # so waiting on it asks the question this method actually means. The
        # deadline is short and its expiry is not fatal: the send below runs
        # anyway, and a genuine absence still surfaces as the engine's error
        # rather than as a hang.
        deadline = time.monotonic() + 10.0
        rid = self.request.request_id
        while rid in getattr(page, "_requests", {}) and time.monotonic() < deadline:
            time.sleep(0.02)

        # ⛔ `page.send`, NOT `page.conn.send`: the command lives on
        # PageHandler, so it is scoped to a SESSION. Sent without one the
        # engine answers `Handler for does not implement method
        # "Network.getResponseBody"` - with a blank where the session should
        # be, which reads like a missing handler and is really a missing
        # address. The route commands next door get away with `conn.send`
        # because interception is answered on the browser handler.
        answer = page.send(
            "Network.getResponseBody", {"requestId": rid}) or {}
        if answer.get("evicted"):
            # ⛔ The reason the old refusal existed, kept for the one case
            # where it is still true: this body really is gone. The engine
            # caps storage at 100 MB per tab and a tenth of that per response,
            # and evicts oldest-first past the cap. Handing back an empty
            # string would read as an empty page rather than a dropped body,
            # which is the same lie the removal note warned about.
            raise ProtocolException(
                "response.body() is not available: the engine evicted this "
                "body to stay under its response-storage cap (100 MB per tab, "
                "10 MB per response). Read the body sooner, or fetch the URL "
                "again if it is still there")
        return {"binary": answer.get("base64body") or ""}

    def op_raw_response_headers(self, params: Dict) -> Any:
        return {"headers": self.raw_headers}

    def op_http_version(self, params: Dict) -> Any:
        """⛔ Juggler sends it on `requestFinished`, not on `responseReceived`,
        so a response asked before the request finished does not know yet. It
        answers what it has - the alternative is blocking a property read on a
        network event that may never come."""
        return {"value": self.protocol_version or "unknown"}

    def op_security_details(self, params: Dict) -> Any:
        return {"value": None}

    def op_server_addr(self, params: Dict) -> Any:
        return {"value": self.remote if self.remote["ipAddress"] else None}

    def op_sizes(self, params: Dict) -> Any:
        return {"sizes": {"requestBodySize": 0, "requestHeadersSize": 0,
                          "responseBodySize": 0, "responseHeadersSize": 0}}


class RouteDispatcher(Dispatcher):
    """One intercepted request, waiting for the caller to decide.

    ⛔ A ROUTE THAT IS NEVER ANSWERED HANGS THE PAGE, and it hangs it silently.
    The request sits held in the network layer; nothing errors, the page simply
    never finishes loading, and the failure surfaces as a timeout on whatever
    the caller does next. Playwright's client answers automatically when no
    handler matches - that safety net only works if this object reaches it,
    which is why the `route` event is emitted the instant the request is
    intercepted and never lazily.
    """

    TYPE = "Route"
    METHODS = {
        "abort": "op_abort",
        "continue": "op_continue",
        "fulfill": "op_fulfill",
        "redirectNavigationRequest": "op_redirect",
    }

    def __init__(self, server, request: "RequestDispatcher") -> None:
        self.request = request
        self.answered = False
        super().__init__(server, request.page, {"request": request.channel})

    # ⛔ FIVE LEVELS ON ONE LINE was what this replaced:
    # `self.request.page.context.browser.conn.send(...)`. A route answering a
    # request needs the connection; it does not need to know that a request has
    # a page, a page a context, a context a browser and a browser a `conn`.
    @property
    def conn(self) -> "juggler.Connection":
        return self.request.page.conn


    def _send(self, command: str, params: Dict) -> Any:
        if self.answered:
            raise ProtocolException(
                "this route was already answered: a request can be aborted, "
                "continued or fulfilled exactly once")
        self.answered = True
        params = dict(params)
        params["requestId"] = self.request.request_id
        result = self.conn.send(
            command, params, timeout=30)
        self.dispose()
        return result

    def op_abort(self, params: Dict) -> Any:
        """⛔ The error code travels: `NS_ERROR_ABORT` and `NS_ERROR_FAILURE`
        are not the same thing to a page that inspects the failure, and
        collapsing every reason into one is the sort of flattening a detector
        can read."""
        return self._send("Network.abortInterceptedRequest",
                          {"errorCode": params.get("errorCode") or "aborted"})

    def op_continue(self, params: Dict) -> Any:
        payload = _only_set({
            "url": params.get("url"),
            "method": params.get("method"),
            "headers": _headers_array(params.get("headers"))
                       if params.get("headers") is not None else None,
            "postData": params.get("postData"),
        })
        return self._send("Network.resumeInterceptedRequest", payload)

    def op_fulfill(self, params: Dict) -> Any:
        """⛔ THE BODY IS BASE64 ON THE WIRE, always. The client already
        encodes it and names the field `body` with `isBase64` beside it;
        Juggler wants `base64body`. Handing over the raw string produces a page
        whose bytes are the base64 TEXT, which renders as gibberish rather than
        failing."""
        body = params.get("body")
        if body is not None and not params.get("isBase64"):
            import base64 as _b64
            body = _b64.b64encode(str(body).encode("utf-8")).decode("ascii")
        return self._send("Network.fulfillInterceptedRequest", _only_set({
            "status": params.get("status") or 200,
            "statusText": params.get("statusText") or "",
            "headers": _headers_array(params.get("headers")),
            "base64body": body,
        }))

    def op_redirect(self, params: Dict) -> Any:
        return self._send("Network.resumeInterceptedRequest",
                          {"url": params["url"]})




# ── page ────────────────────────────────────────────────────────────────────
class PageDispatcher(Dispatcher):
    TYPE = "Page"
    METHODS = {
        "mouseMove": "op_mouse_move",
        "mouseDown": "op_mouse_down",
        "mouseUp": "op_mouse_up",
        "mouseClick": "op_mouse_click",
        "mouseWheel": "op_mouse_wheel",
        "keyboardDown": "op_key_down",
        "keyboardUp": "op_key_up",
        "keyboardPress": "op_key_press",
        "keyboardType": "op_key_type",
        "keyboardInsertText": "op_key_insert",
        "close": "op_close",
        "goBack": "op_go_back",
        "goForward": "op_go_forward",
        "reload": "op_reload",
        "updateSubscription": "op_update_subscription",
        "setViewportSize": "op_set_viewport_size",
        "emulateMedia": "op_emulate_media",
        "screenshot": "op_screenshot",
        "bringToFront": "op_bring_to_front",
        "exposeBinding": "op_expose_binding",
        "touchscreenTap": "op_touchscreen_tap",
        "requests": "op_requests",
        "__waitInfo__": "op_noop",
        "reject": "op_binding_reply",
        "resolve": "op_binding_reply",
        "requestGC": "op_request_gc",
        "addScriptTag": "op_add_script_tag",
        "addStyleTag": "op_add_style_tag",
        "consoleMessages": "op_console_messages",
        "pageErrors": "op_page_errors",
        "clearConsoleMessages": "op_clear_console_messages",
        "clearPageErrors": "op_clear_page_errors",
        "webStorageItems": "op_storage_items",
        "webStorageGetItem": "op_storage_get",
        "webStorageSetItem": "op_storage_set",
        "webStorageRemoveItem": "op_storage_remove",
        "webStorageClear": "op_storage_clear",
        "runBeforeUnload": "op_run_before_unload",
        "addInitScript": "op_add_init_script",
    }

    def __init__(self, server, context: "BrowserContextDispatcher",
                 session: str, target_id: str) -> None:
        self.context = context
        self.session = session
        self.target_id = target_id
        conn = context.browser.conn
        self.lifecycle = Lifecycle(conn, session)
        self.injected = InjectedScript(conn, session)
        self.injected.install()
        self.actions = Actions(conn, session, self.lifecycle, self.injected)
        # ⛔ THE EVENTS THIS PAGE ALREADY MISSED, handed over now that the two
        # things that need them exist. `Page.frameAttached` and the
        # `Runtime.executionContextCreated` pair are sent by the browser BEFORE
        # the `Browser.newPage` reply that told us which session to build this
        # object for, so without the replay the lifecycle waits twenty seconds
        # for a main frame that was announced before it was born.
        #
        # ⛔ AND IT DELIBERATELY DOES NOT REACH `_on_juggler_event`, which is
        # not wired yet and cannot be: that handler emits on `self.channel`,
        # and the channel does not exist until `super().__init__` has run,
        # which needs the main frame this replay is what delivers. The events
        # that precede a page describe its birth, and the initializer built
        # below carries that - the main frame is in it by name.
        replayed = context.browser.replay(session, conn.dispatch_event)
        self.replayed_events = replayed
        self._frames: Dict[str, Any] = {}
        self._requests: Dict[str, Any] = {}
        # ⛔ CAPPED, and that is not a detail: a page printing in a loop
        # would exhaust the memory of the process DRIVING it. Playwright keeps
        # the same logs and caps them for the same reason.
        self._console_log: List[Dict] = []
        self._error_log: List[Dict] = []
        #: ⛔ Capped like the others: a page that fetches in a loop
        #: must not exhaust the process driving it.
        self._request_log: List[Any] = []
        self.main_frame_id = self.lifecycle.wait_for_main_frame(timeout=20.0)
        # ⛔ The Frame is created BEFORE the Page and adopted after: the Page
        # initializer has to name a mainFrame the client can already resolve.
        self.frame = FrameDispatcher(server, self, self.main_frame_id)
        viewport = context.options.get("viewport") or {"width": 1280,
                                                       "height": 720}
        super().__init__(server, context,
                         {"mainFrame": self.frame.channel,
                          "viewportSize": viewport, "isClosed": False})
        self.emit("__adopt__", {"guid": self.frame.guid})
        self.frame.parent = self
        # ⛔ AFTER the Page exists: an event that fires during construction
        # would name a guid the client has not been told about yet.
        self._install_events()

    @property
    def browser(self) -> "BrowserDispatcher":
        return self.context.browser

    # ⛔ Six call sites used to reach `self.actions.keyboard`. The actions
    # engine owns the keyboard; a page pressing a key does not need to know
    # that, and now does not say it.
    @property
    def keyboard(self):
        return self.actions.keyboard


    # ⛔ THREE LEVELS, WRITTEN OUT EIGHT TIMES: `self.context.browser.conn`.
    # A page talks to the browser connection constantly, and every one of those
    # eight lines also asserted that a context has a browser and a browser has
    # a `conn`. One property, and the shape of the tree stops being everybody's
    # business.
    @property
    def conn(self) -> "juggler.Connection":
        return self.context.browser.conn



    def _install_events(self) -> None:
        """Turn Juggler events into protocol events.

        ⛔ THE SUBSCRIPTION IS NOT OPTIONAL AND IT IS NOT FREE. Playwright asks
        the server to enable categories with `updateSubscription`, and this
        server ignores that and always sends: the events are cheap here because
        they are already crossing the pipe for the lifecycle, and a category
        that is off is a `page.on("console")` that silently never fires.

        ⛔ AND `console` AND `pageError` GO TO THE CONTEXT, not to the Page.
        `_browser_context.py` listens for them and re-emits on the page it
        finds in `params["page"]`. Emitting them on the Page instead produces
        no error at all: the handler simply never runs, and the user concludes
        their page prints nothing.
        """
        # ⛔ Registered on the connection's list, never chained. The isolation
        # that used to live in this closure is now `dispatch_event`'s job and
        # covers every subscriber instead of just this one.
        self.conn.add_listener(self._route_juggler_event)

    def _route_juggler_event(self, method: str, params: Dict,
                             session) -> None:
        if session == self.session:
            self._on_juggler_event(method, params)

    def _detach_listeners(self) -> None:
        """Unsubscribe this page and everything it owns.

        ⛔ ALL THREE, AND THIS IS THE HALF OF THE FIX THAT IS EASY TO FORGET.
        A page registers three subscribers - its Lifecycle, its InjectedScript
        and itself - and before the registry none of them was ever removed:
        that is how a browser reached 979 subscribers at page 325 and then
        stopped delivering events altogether. Adding a listener without a
        matching removal is the same defect written in a new shape.
        """
        conn = self.conn
        conn.remove_listener(self._route_juggler_event)
        self.lifecycle.detach()
        self.injected.detach()

    def _on_juggler_event(self, method: str, params: Dict) -> None:
        if method == "Runtime.console":
            # ⛔ THE LOG FILLS HERE, not inside `console_messages()`. A log
            # filled on request can only ever hold what arrived AFTER the
            # request, which is nothing: `page.console_messages()` would always
            # answer an empty list and look like a page that prints nothing.
            # ⛔ ONE DICT, TWO CONSUMERS, and it used to be two identical
            # literals. The log and the live listener have to agree by
            # CONSTRUCTION: with two copies, populating `args` for real in one
            # of them - the obvious next change to this line - silently makes
            # `page.console_messages()` disagree with `page.on("console")`,
            # and nothing would fail. Sharing the object is safe because
            # `_remember` only appends and the client-side transform
            # (`_replace_guids_with_channels`) builds a new dict rather than
            # mutating this one.
            entry = {
                "page": self.channel,
                "type": params.get("type") or "log",
                "text": _console_text(params.get("args") or []),
                "args": [],
                "location": _location(params.get("location")),
            }
            self._remember(self._console_log, entry)
            self.context.emit("console", entry)
        elif method == "Page.uncaughtError":
            # Same single-dict rule as the console branch above.
            failure = {
                "page": self.channel,
                "error": {"error": {
                    "name": "Error",
                    "message": params.get("message") or "",
                    "stack": params.get("stack") or "",
                }},
                "location": _location(params.get("location")),
            }
            self._remember(self._error_log, failure)
            self.context.emit("pageError", failure)
        elif method == "Page.dialogOpened":
            self._emit_fused_dialog(params["dialogId"],
                                    params.get("type") or "alert",
                                    params.get("message") or "",
                                    params.get("defaultValue") or "")
        elif method == "Page.fileChooserOpened":
            # ⛔ OFF THIS THREAD, and it is a deadlock rather than a slowdown.
            # `_on_juggler_event` runs INSIDE the connection's read loop, and
            # everything this event needs - adopting the node into the utility
            # world, then asking the element whether it takes several files -
            # is a command whose answer only that same read loop can deliver.
            # Doing it here would block the reader waiting for a message the
            # reader is the one who has to receive.
            threading.Thread(
                target=self._announce_file_chooser, args=(params,),
                daemon=True).start()
        elif method == "Page.crashed":
            self.emit("crash")
        elif method == "Page.frameAttached":
            # ⛔ A CHILD FRAME HAS TO BE ANNOUNCED, or `page.frames` holds only
            # the main one and every iframe locator finds nothing. The judge
            # caught this: seven tests passed through the Node driver and failed
            # through us, all of them about frames, and the message was
            # `page.frames urls = ['http://.../']` - one entry where there
            # should have been two.
            child = self.frame_for(params["frameId"])
            self.emit("frameAttached", {"frame": child.channel})
        elif method == "Page.frameDetached":
            child = self._frames.pop(params.get("frameId"), None)
            if child is not None:
                self.emit("frameDetached", {"frame": child.channel})
                child.dispose()
        elif method == "Page.eventFired":
            state = {"load": "load",
                     "DOMContentLoaded": "domcontentloaded"}.get(
                         params.get("name") or "")
            if state:
                # ⛔ EVERY frame, not only the main one: a child that never
                # reaches `load` leaves a wait on it hanging forever.
                self.frame_for(params["frameId"]).emit(
                    "loadstate", {"add": state})
        elif method == "Network.requestWillBeSent":
            request = RequestDispatcher(self.server, self, params)
            self._requests[params.get("requestId")] = request
            self._remember(self._request_log, request)
            self.context.emit("request", {"request": request.channel,
                                          "page": self.channel})
            if self.context.intercepting and params.get("isIntercepted"):
                route = RouteDispatcher(self.server, request)
                self.context.emit("route", {"route": route.channel,
                                            "page": self.channel})
        elif method == "Network.responseReceived":
            request = self._requests.get(params.get("requestId"))
            if request is not None:
                response = ResponseDispatcher(self.server, request, params)
                request.response = response
                self.context.emit("response", {"response": response.channel,
                                               "page": self.channel})
        elif method == "Network.requestFinished":
            request = self._requests.pop(params.get("requestId"), None)
            if request is not None:
                if request.response is not None:
                    request.response.protocol_version = (
                        params.get("protocolVersion") or "")
                self.context.emit("requestFinished", {
                    "request": request.channel,
                    "response": request.response.channel
                               if request.response else None,
                    "responseEndTiming": params.get("responseEndTime") or 0,
                    "page": self.channel,
                })
        elif method == "Network.requestFailed":
            request = self._requests.pop(params.get("requestId"), None)
            if request is not None:
                request.failure = params.get("errorCode") or "failed"
                self.context.emit("requestFailed", {
                    "request": request.channel,
                    # ⛔ NOT OPTIONAL. `_browser_context.py` reads it with
                    # `params["responseEndTiming"]`, square brackets, so its
                    # absence is a `KeyError` INSIDE the client's event
                    # handler - and that handler runs on the read loop, so the
                    # error surfaces attached to whatever command happened to
                    # be in flight. It was measured as
                    # `Browser.close: 'responseEndTiming'`, which names a
                    # command that has nothing to do with it. The sibling
                    # event `requestFinished` already carried it; this one was
                    # missed, and only a flow with a FAILING request reaches
                    # it, which is why five files of transport judging never
                    # did.
                    "responseEndTiming": params.get("responseEndTime") or 0,
                    "failureText": params.get("errorCode") or "failed",
                    "page": self.channel,
                })
        elif method == "Page.navigationCommitted":
            child = self.frame_for(params["frameId"])
            child.url = params.get("url") or ""
            child.emit("navigated", {
                "url": child.url,
                "name": params.get("name") or "",
                "newDocument": {"request": None},
            })

    #: How many entries are kept. ⛔ A CAP, not a history: a page printing in
    #: a loop would exhaust the memory of the process DRIVING it, and a driver
    #: that dies because the page printed too much is a failure nobody will
    #: look for in the page.
    LOG_LIMIT = 500

    @staticmethod
    def _remember(log: list, entry: Dict) -> None:
        log.append(entry)
        if len(log) > PageDispatcher.LOG_LIMIT:
            del log[0]

    def _announce_file_chooser(self, params: Dict) -> None:
        """Build the handle the client expects and emit `fileChooser`.

        ⛔ `isMultiple` IS NOT IN THE ENGINE EVENT. Juggler sends the element
        and the context it lives in, nothing else, while the client's
        `FileChooser` is constructed with it - so it has to be ASKED of the
        element. Defaulting it to false would be a value invented here to fill
        a field, which is the shape of thing that is right until the first
        `<input multiple>`.
        """
        try:
            element = params.get("element") or {}
            object_id = element.get("objectId")
            context_id = params.get("executionContextId")
            if not object_id or not context_id:
                return
            frame_id = self.injected.frame_of_context(context_id)
            if not frame_id:
                # ⛔ Not a guess at the main frame: an element adopted into
                # the wrong frame's world answers about a different document.
                # Saying nothing loses the event; saying the wrong thing loses
                # the trust in every event.
                return
            adopted = self.injected.adopt(frame_id, context_id, object_id)
            if not adopted:
                return
            frame = self.frame_for(frame_id)
            handle = ElementHandleDispatcher(self.server, frame, adopted,
                                             "JSHandle@node")
            multiple = bool(self.injected.call(
                frame_id, "(injected, el) => !!el.multiple",
                {"objectId": adopted}))
            self.emit("fileChooser", {"element": handle.channel,
                                      "isMultiple": multiple})
        except Exception as failure:
            conn = self.conn
            if len(conn.handler_errors) < 32:
                conn.handler_errors.append("fileChooser: %s" % failure)

    def frame_for(self, frame_id: str) -> "FrameDispatcher":
        """The dispatcher for a frame of this page, made on first sight.

        ⛔ ONE DISPATCHER PER FRAME ID, AND THE REGISTRY IS WHY. Building a
        new one each time would announce a second `__create__` for the same
        frame, and the client would hold two ChannelOwners that disagree about
        the load states - the second one starting empty while the first is the
        one events are delivered to.
        """
        if frame_id == self.frame.frame_id:
            return self.frame
        existing = self._frames.get(frame_id)
        if existing is not None:
            return existing
        frame = self.lifecycle.frame(frame_id)
        made = FrameDispatcher(
            self.server, self, frame_id,
            url=getattr(frame, "url", "") or "",
            load_states=sorted(getattr(frame, "states", []) or []) or ["commit"])
        self._frames[frame_id] = made
        return made

    def send(self, command: str, params: Dict) -> Any:
        """A Juggler command on THIS page's session.

        ⛔ The session is not optional and it is not a detail: without it the
        command lands on whichever page the browser feels like, and with two
        pages open the events of one are indistinguishable from the other.
        """
        return self.conn.send(command, params,
                                              session=self.session, timeout=30)

    async def send_async(self, command: str, params: Dict) -> Any:
        """`send`, off the loop and awaitable.

        ⛔ THE PRIMITIVE A FUSED TYPE NEEDS. A fused object's own methods are
        `async def` - the public API promises that - but the Juggler call
        underneath still BLOCKS. This hands it to the transport's worker pool
        via `Server.run_blocking` and awaits the result, instead of a fused
        type reaching for the pool on its own and reinventing the translation
        `run_blocking` already does. Dialog is the first caller, 2026-08-29.
        """
        return await self.server.run_blocking(self.send, command, params)

    def _emit_fused_dialog(self, dialog_id: str, kind: str, message: str,
                           default_value: str) -> None:
        """A dialog just opened: build the fused object and hand it to the
        impl-side `BrowserContext` directly, no `__create__`, no guid, no
        channel.

        ⛔ THIS RUNS ON THE CONNECTION'S READ LOOP, not the asyncio loop -
        `_on_juggler_event` always does, see the comment on `_history` /
        `Page.fileChooserOpened` above for the same fact stated once already.
        `_on_dialog` and the auto-answer path it may take
        (`asyncio.create_task(...)`) both require the ASYNCIO loop, so the
        hand-off is `Server.call_soon`, the exact mechanism every other event
        already crosses this same boundary with - just carrying a real object
        instead of a wire message this time.

        ⛔ AND CREATING THE OBJECT IS NOT NOTIFYING ANYONE, same as when this
        was two objects: forgetting the hand-off is a HANG, not a leak - the
        content process sits inside `window.alert` and the next command times
        out naming something unrelated. Measured on 2026-08-28, before the
        fusion, as `Runtime.callFunction: no response in 30s`.
        """
        from invisible_playwright._pw._impl._dialog import Dialog as ImplDialog

        impl_page = self.server.twin(self.guid)
        if impl_page is None:
            # The client never learned about this page - can happen only if
            # a dialog fires in the gap between the engine creating the page
            # and `__create__` reaching the client, which today's ordering
            # guarantee is not supposed to allow. Refusing to guess who to
            # notify is safer than guessing wrong.
            return
        dialog = ImplDialog(self, impl_page, dialog_id, kind, message,
                           default_value)
        browser_context = impl_page._browser_context
        # ⛔ TWO HOPS, NOT ONE: `call_soon` gets to the loop thread from here
        # (the connection's read loop); `server.deliver` then runs the target
        # under the client's own event-delivery rules once there (the
        # `EventGreenlet` wrapping `dispatch` gives every wire event, see
        # `Connection.deliver_event`). Calling `_on_dialog` bare through
        # `call_soon` skips that wrapping entirely, and a sync-mode
        # `dialog.accept()` called from the "dialog" handler hangs with no
        # exception - found live, 2026-08-29, not by the unit suite.
        self.server.call_soon(
            lambda: self.server.deliver(browser_context._on_dialog, dialog))

    # ── history ─────────────────────────────────────────────────────────────
    def _history(self, command: str, params: Dict) -> Any:
        """⛔ goBack / goForward / reload are sent to the PAGE, not the Frame.

        The first draft put them on the Frame because everything else that
        navigates lives there, and it was wrong: `_page.py` sends them on the
        page channel. Guessing which object owns an operation is exactly what
        the recorded trace exists to stop.
        """
        frame_id = self.frame.frame_id
        # ⛔ READ THE CURRENT NAVIGATION FIRST. History gives back no
        # navigationId, so the only thing to anchor the wait on is that this
        # one has changed - and reading it after the command would sometimes
        # read the new one.
        frame = self.lifecycle.frame(frame_id)
        previous = frame.navigation if frame is not None else None
        result = self.send(command, {"frameId": frame_id}
                           if command != "Page.reload" else {}) or {}
        if command != "Page.reload" and not result.get("success"):
            # ⛔ NULL, not an error: `go_back` at the start of history is a
            # normal answer in Playwright, and raising would turn an ordinary
            # "there is nothing behind" into a failed script.
            return {"response": None}
        self.lifecycle.wait_for_new_navigation(
            frame_id, previous, params.get("waitUntil") or "load",
            timeout=(params.get("timeout") or 30000) / 1000.0)
        return {"response": None}

    def op_go_back(self, params: Dict) -> Any:
        return self._history("Page.goBack", params)

    def op_go_forward(self, params: Dict) -> Any:
        return self._history("Page.goForward", params)

    def op_reload(self, params: Dict) -> Any:
        return self._history("Page.reload", params)

    # ── pointer and keyboard ──────────────────────────────────────────
    def op_mouse_move(self, params: Dict) -> Any:
        self.actions.move(params["x"], params["y"],
                          steps=params.get("steps") or 1)
        return None

    def op_mouse_down(self, params: Dict) -> Any:
        self.actions.mouse_down(button=_button(params.get("button")),
                                clicks=params.get("clickCount") or 1)
        return None

    def op_mouse_up(self, params: Dict) -> Any:
        self.actions.mouse_up(button=_button(params.get("button")),
                              clicks=params.get("clickCount") or 1)
        return None

    def op_mouse_click(self, params: Dict) -> Any:
        self.actions.click_at(params["x"], params["y"],
                              button=_button(params.get("button")),
                              clicks=params.get("clickCount") or 1)
        return None

    def op_mouse_wheel(self, params: Dict) -> Any:
        self.actions.wheel(params["deltaX"], params["deltaY"])
        return None

    def op_key_down(self, params: Dict) -> Any:
        self.keyboard.down(params["key"])
        return None

    def op_key_up(self, params: Dict) -> Any:
        self.keyboard.up(params["key"])
        return None

    def op_key_press(self, params: Dict) -> Any:
        self.keyboard.press(params["key"])
        return None

    def op_key_type(self, params: Dict) -> Any:
        self.keyboard.type(params["text"])
        return None

    def op_key_insert(self, params: Dict) -> Any:
        self.keyboard.insert_text(params["text"])
        return None

    # ── viewport, media, capture ────────────────────────────────────────────
    def op_set_viewport_size(self, params: Dict) -> Any:
        self.send("Page.setViewportSize", _only_set(
            {"viewportSize": params.get("viewportSize")}))
        return None

    def op_emulate_media(self, params: Dict) -> Any:
        """⛔ ONLY WHAT WAS ASKED FOR TRAVELS. Our fork turned four hardwired
        values into "no-override" precisely because a BrowsingContext override
        SHORT-CIRCUITS the pref: Gecko reads the override first and only
        consults LookAndFeel when it is None, so an override nobody requested
        turns every declaration invisible_core makes into dead code without
        raising anything.
        """
        out: Dict[str, Any] = {}
        for ours, theirs in (("colorScheme", "colorScheme"),
                             ("reducedMotion", "reducedMotion"),
                             ("forcedColors", "forcedColors"),
                             ("contrast", "contrast"),
                             ("media", "type")):
            value = params.get(ours)
            if value is not None:
                out[theirs] = value
        if not out:
            return None
        self.send("Page.setEmulatedMedia", out)
        return None

    def op_screenshot(self, params: Dict) -> Any:
        """⛔ The engine answers base64 and the client wants base64: it does
        NOT want bytes. Decoding here would send a str() of a bytes object."""
        # ⛔ `clip` IS MANDATORY, whatever it looks like. The declaration
        # does not wrap it in `Optional`, so leaving it out is rejected exactly
        # like sending it as null: `Object "<root>.clip" is undefined, but has
        # some scheme`. Reading the type declaration is what settled this -
        # both of the obvious readings of the error are wrong.
        clip = params.get("clip")
        if not clip:
            size = self.injected.evaluate(
                self.frame.frame_id,
                "({x: 0, y: 0, width: window.innerWidth,"
                " height: window.innerHeight})")
            clip = size or {"x": 0, "y": 0, "width": 1280, "height": 720}
        result = self.send("Page.screenshot", _only_set({
            "mimeType": "image/jpeg" if params.get("type") == "jpeg"
                        else "image/png",
            "clip": clip,
            "quality": params.get("quality"),
            "omitDeviceScaleFactor": False,
        })) or {}
        # ⛔ BASE64 IN, BASE64 OUT. The client decodes it; decoding here and
        # handing back bytes puts a str() of a bytes object in the answer.
        return {"binary": result.get("data")}

    # ── page-level odds and ends ────────────────────────────────────────────
    def op_request_gc(self, params: Dict) -> Any:
        """⛔ `Heap.collectGarbage` really does collect, and it is SLOW - it was
        once used as a "bare command" to measure transport latency and reported
        26,8 ms of browser work as if it were ours. It is the right command
        here, and the wrong one to benchmark with."""
        self.conn.send("Heap.collectGarbage", {}, timeout=30)
        return None

    def _add_tag(self, params: Dict, tag: str) -> Any:
        """`add_script_tag` / `add_style_tag`.

        ⛔ THE MAIN WORLD, and that is the whole correctness of this method. A
        tag appended from the utility world would execute BEHIND THE XRAY: it
        would define nothing the page can see, which is the exact opposite of
        what these two functions promise. The utility world is right for
        everything we read and wrong for the one thing the caller wants the
        page itself to run.

        ⛔ AND A `url` HAS TO BE AWAITED. Appending a `<script src=...>` and
        returning immediately hands back a handle to a tag whose code has not
        run yet, so the very next `evaluate` does not see what it defines. The
        load is awaited here, and a failure to load RAISES rather than
        answering an element that does nothing.
        """
        url = params.get("url")
        content = params.get("content")
        path = params.get("path")
        if path:
            content = pathlib.Path(path).read_text(encoding="utf-8")
        if url is None and content is None:
            raise ProtocolException(
                "add_%s_tag needs one of url, path or content" % tag)

        if tag == "script":
            build = ("const el = document.createElement('script');"
                          " el.type = 'text/javascript';")
            attribute = "src"
        else:
            build = ("const el = document.createElement('style');"
                          " el.type = 'text/css';")
            attribute = "href"
            if url is not None:
                build = ("const el = document.createElement('link');"
                              " el.rel = 'stylesheet';")

        if url is not None:
            body = (
                "(async () => { %s"
                "  el.%s = %s;"
                "  const done = new Promise((ok, no) => {"
                "    el.onload = ok;"
                "    el.onerror = () => no(new Error('failed to load ' + %s));"
                "  });"
                "  (document.head || document.documentElement).appendChild(el);"
                "  await done; return el; })()"
                % (build, attribute, _js_string(url), _js_string(url)))
        else:
            body = (
                "(() => { %s el.textContent = %s;"
                "  (document.head || document.documentElement).appendChild(el);"
                "  return el; })()" % (build, _js_string(content)))

        object_id = self.injected.evaluate_in_main(
            self.frame.frame_id, body, by_value=False)
        handle = ElementHandleDispatcher(self.server, self.frame, object_id)
        return {"element": handle.channel}

    def op_add_script_tag(self, params: Dict) -> Any:
        return self._add_tag(params, "script")

    def op_add_style_tag(self, params: Dict) -> Any:
        return self._add_tag(params, "style")

    def op_console_messages(self, params: Dict) -> Any:
        return {"messages": list(self._console_log)}

    def op_page_errors(self, params: Dict) -> Any:
        return {"errors": list(self._error_log)}

    def op_clear_console_messages(self, params: Dict) -> Any:
        self._console_log.clear()
        return None

    def op_clear_page_errors(self, params: Dict) -> Any:
        self._error_log.clear()
        return None

    # ── web storage ───────────────────────────────────────────────
    def _storage(self, params: Dict, code: str, *args) -> Any:
        """localStorage or sessionStorage, in the PAGE's own world.

        ⛔ THE MAIN WORLD, and this one is not a style choice: web storage is
        keyed by ORIGIN and the utility world sandbox has an ExpandedPrincipal.
        Reading it from there does not raise - it answers a DIFFERENT store,
        empty, and the caller concludes the site saved nothing.

        ⛔ AND THE KIND IS VALIDATED. `kind` arrives as a string from the
        client; interpolating it into the expression would let anything through
        and produce a JavaScript error the caller cannot read.
        """
        kind = params.get("kind")
        if kind not in ("localStorage", "sessionStorage"):
            raise ProtocolException(
                "unknown storage kind %r: the two are localStorage and "
                "sessionStorage" % kind)
        return self.injected.evaluate_in_main(
            self.frame.frame_id, code % ((kind,) + args))

    def op_storage_items(self, params: Dict) -> Any:
        items = self._storage(params,
                              "(() => { const s = window.%s; const o = [];"
                              " for (let i = 0; i < s.length; i++) {"
                              "   const k = s.key(i);"
                              "   o.push({name: k, value: s.getItem(k)}); }"
                              " return o; })()")
        return {"items": items or []}

    def op_storage_get(self, params: Dict) -> Any:
        value = self._storage(params, "window.%s.getItem(%s)",
                              _js_string(params["name"]))
        return {"value": value}

    def op_storage_set(self, params: Dict) -> Any:
        self._storage(params, "window.%s.setItem(%s, %s)",
                      _js_string(params["name"]), _js_string(params["value"]))
        return None

    def op_storage_remove(self, params: Dict) -> Any:
        self._storage(params, "window.%s.removeItem(%s)",
                      _js_string(params["name"]))
        return None

    def op_storage_clear(self, params: Dict) -> Any:
        self._storage(params, "window.%s.clear()")
        return None

    def op_add_init_script(self, params: Dict) -> Any:
        """A script the caller wants run before anything on every document.

        ⛔ IT WAS MISSING, AND THE REFUSAL LAYER SAID SO CORRECTLY: the name
        is INSIDE the perimeter, so its absence was a gap and not a decision.
        Nothing in the transport judgement reached it - none of those five
        files calls it - and it turned up the first time a REALNESS gate ran on
        this path, which is the argument for running those on both transports
        rather than on the one they were written against.

        The list itself is owned by `InjectedScript`, because the engine's
        command REPLACES the whole set and the utility world lives in it.
        """
        source = params.get("source") or ""
        self.injected.add_init_script(source)
        handle = DisposableDispatcher(
            self.server, self,
            lambda: self.injected.remove_init_script(source))
        return {"disposable": handle.channel}

    def op_update_subscription(self, params: Dict) -> Any:
        """Turn a client-side event subscription into an engine-side one.

        ⛔ IT WAS A NO-OP, AND FOR MOST EVENTS THAT IS CORRECT: console,
        requests, dialogs and the rest are emitted unconditionally, so the
        client asking to hear them changes nothing here. `fileChooser` is the
        exception, and it is the exception that makes the no-op WRONG rather
        than merely incomplete: Juggler does not report a file picker unless it
        has been told to intercept it, so the subscription is the only thing
        that arms the engine. Without this, `page.expect_file_chooser()` waits
        for an event nobody will ever send and dies on its own timeout, with
        nothing in the log saying why.
        """
        if params.get("event") == "fileChooser":
            self.conn.send(
                "Page.setInterceptFileChooserDialog",
                {"enabled": bool(params.get("enabled"))},
                session=self.session, timeout=10)
        return None

    def op_run_before_unload(self, params: Dict) -> Any:
        """⛔ `close(run_before_unload=True)` means: let the page show its
        `beforeunload` dialog. Answering it is the CALLER's job through the
        dialog event, so this must not close the page itself - doing that
        would dismiss the very dialog the option exists to raise."""
        self.injected.evaluate_in_main(self.frame.frame_id, "window.close()")
        return None

    def op_touchscreen_tap(self, params: Dict) -> Any:
        """⛔ It fires whether or not touch is enabled on the context, and that
        is worth knowing: with touch off the event goes out and the page has no
        `ontouchstart` to listen with, so the call SUCCEEDS and does nothing.
        Touch is turned on at context creation with `hasTouch`."""
        self.send("Page.dispatchTapEvent",
                  {"x": params["x"], "y": params["y"],
                   "modifiers": self.keyboard.modifier_mask()})
        return None

    def op_requests(self, params: Dict) -> Any:
        return {"requests": [r.channel for r in self._request_log]}

    def op_binding_reply(self, params: Dict) -> Any:
        """⛔ `resolve` and `reject` are the REPLY half of `expose_binding`,
        and they are unreachable while that is refused: they arrive on a
        BindingCall object, and no BindingCall is ever created. This exists so
        the refusal names the feature instead of answering "Page has no method
        resolve", which would send the reader looking in the wrong place."""
        raise ProtocolException(
            "this is the reply path of expose_binding(), which is not "
            "implemented: no binding is ever installed, so nothing can call "
            "back into one")

    def op_expose_binding(self, params: Dict) -> Any:
        """`expose_binding` / `expose_function`.

        ⛔ THE PAGE-FACING HALF IS NOT THE HARD PART. Installing a function on
        the page is one init script; what makes this a real feature is the
        REPLY path - the page calls it, the server raises `bindingCalled`, the
        client runs Python, and the answer has to travel back into the promise
        the page is holding. That is what `reject` and `resolve` are for, and
        they are only reachable through the BindingCall object this creates.

        ⛔ AND THE FUNCTION MUST LIVE IN THE PAGE'S WORLD. Installed behind the
        Xray it would be invisible to the site, which is the whole point of the
        call. Juggler's `Page.addBinding` does that, so the world is not ours
        to choose here - which is also why it cannot be hidden from the page:
        a binding is a name the site can enumerate, and a caller asking for one
        is asking for that trade.
        """
        raise ProtocolException(
            "expose_binding() is not implemented yet: the page-facing half is "
            "one init script, but the reply path - bindingCalled, then resolve "
            "or reject into the promise the page holds - is not wired, and a "
            "binding that never answers hangs the page that called it")

    def op_bring_to_front(self, params: Dict) -> Any:
        self.send("Page.bringToFront", {})
        return None

    def op_noop(self, params: Dict) -> Any:
        return None

    def op_close(self, params: Dict) -> Any:
        """Close THIS page, and only this page.

        ⛔ `Page.close`, NOT `Browser.removeBrowserContext`. Removing the
        context is right for a page created by `browser.new_page()`, where the
        client made an implicit context that exists only for it - and wrong for
        every other page: a caller who opens three pages in one context and
        closes one would lose all three, plus its cookies and its storage. The
        earlier version did the second thing always, and it looked correct
        because the tests open one page per context.
        """
        try:
            self.conn.send(
                "Page.close", {"runBeforeUnload": False},
                session=self.session, timeout=10)
        except Exception:
            pass
        # ⛔ The session leaves the browser's registry here, or a long-lived
        # browser accumulates one entry per page it ever opened. Small, but it
        # is the kind of small that a scraper turns into a day-long leak.
        try:
            self.browser.forget(self.session)
        except Exception:
            pass
        self.announce_closed()
        self.dispose()
        return None

    def announce_closed(self) -> None:
        """Tell the client this page is closed, ONCE.

        ⛔ Once, because there are two ways in: the caller closes the page,
        and the caller closes the CONTEXT, which now tells its pages. Both are
        legitimate and both can happen in the same session - `page.close()`
        followed by `context.close()` is the ordinary shape. A second `close`
        is not obviously harmful on today's client, which resolves a future
        that is already resolved; it is a protocol event that never happens
        against the driver, and the whole point of this exercise is that the
        two answer the same.
        """
        if getattr(self, "_announced_closed", False):
            return
        self._announced_closed = True
        # ⛔ THE UNSUBSCRIBE HANGS OFF THE SAME ONCE-GUARD AS THE EVENT, on
        # purpose. Both ways a page can end - `page.close()` and
        # `context.close()` - already funnel through here, so this is the one
        # place that knows a page is over; putting the removal anywhere else
        # would mean two places deciding it, and the one that got missed
        # would leak a subscriber per page exactly as before.
        self._detach_listeners()
        self.emit("close")
        # ⛔ `close` FIRST, THEN `__dispose__`, which is the order the driver
        # uses and not a preference: the client resolves the close on the
        # event, and an object disposed before it is announced is one the
        # client has already dropped from its registry when the event arrives.
        #
        # ⛔ AND IT IS A REAL MESSAGE, NOT BOOKKEEPING. Without it the server's
        # guid registry grew by one object per page for the life of the browser
        # - measured 9 at page 0 and 508 at page 499 - and the client's did
        # too, because `_connection.py` only drops an object when it is told
        # to. The driver disposes Page, BrowserContext, Browser, ElementHandle
        # and APIRequestContext; the only one we were missing was Page, which
        # is why `diff_protocol.py` grew a fifth dimension to see it: the other
        # four compare `__create__`, initializers, events and parentage, and
        # the event comparison skips every method starting with `__`.
        self.dispose()




# ── context, browser, browser type ──────────────────────────────────────────
class BrowserContextDispatcher(Dispatcher):
    TYPE = "BrowserContext"
    METHODS = {
        "newPage": "op_new_page",
        "close": "op_close",
        "updateSubscription": "op_noop",
        "addInitScript": "op_context_init_script",
        "addCookies": "op_add_cookies",
        "cookies": "op_cookies",
        "clearCookies": "op_clear_cookies",
        "grantPermissions": "op_grant_permissions",
        "clearPermissions": "op_clear_permissions",
        "setGeolocation": "op_set_geolocation",
        "setOffline": "op_set_offline",
        "setExtraHTTPHeaders": "op_set_extra_headers",
        "storageState": "op_storage_state",
        "setStorageState": "op_set_storage_state",
        "setNetworkInterceptionPatterns": "op_set_interception",
        "setWebSocketInterceptionPatterns": "op_set_ws_interception",
    }

    def __init__(self, server, browser: "BrowserDispatcher", options: Dict,
                 context_id: str) -> None:
        self.browser = browser
        self.options = options
        self.context_id = context_id
        # ⛔ THE THREE CHILDREN COME FIRST. `_browser_context.py` resolves all
        # of them with `from_channel` inside its constructor, so a context whose
        # initializer names one that does not exist yet raises before any page.
        debugger = DebuggerDispatcher(server, browser)
        debugger.emit("pausedStateChanged", {})
        tracing = TracingDispatcher(server, browser)
        request_context = APIRequestContextDispatcher(
            server, browser, {"tracing": tracing.channel})
        request_context.emit("__adopt__", {"guid": tracing.guid})
        super().__init__(server, browser, {
            "debugger": debugger.channel,
            "requestContext": request_context.channel,
            "tracing": tracing.channel,
            "options": options,
            # ⛔ `isChromium` was here and is gone: the vendored client never
            # reads it, and the Node driver does not send it either. An
            # initializer field nobody consumes is not free - it is a claim
            # about the protocol that the next reader has to check before
            # touching, and the protocol diff reports it forever.
        })
        for child in (debugger, request_context, tracing):
            self.emit("__adopt__", {"guid": child.guid})
        self.pages: List[PageDispatcher] = []
        self.intercepting = False
        #: The caller's context-level init scripts, in order. The engine's
        #: command replaces the list, so the accumulation lives here.
        self._init_scripts: List[str] = []

    @property
    def conn(self) -> "juggler.Connection":
        return self.browser.conn


    def op_context_init_script(self, params: Dict) -> Any:
        """The same, for every page of this context - present and future.

        ⛔ IT WAS `op_noop`, which is the worst of the three possible
        answers. Refusing would have told the caller; doing it would have been
        right; accepting and discarding meant `context.add_init_script(...)`
        returned successfully and the page never saw the script - and a caller
        who adds a stub and then tests for it concludes the SITE removed it.

        ⛔ BOTH SIDES, and that is not belt and braces. `Browser.setInitScripts`
        covers pages this context opens LATER; the pages already open have
        their own list in the engine and do not re-read the context's, so they
        are told directly. A version that did only the first works in every
        test that adds the script before opening a page, which is most of them.
        """
        source = params.get("source") or ""
        self._init_scripts.append(source)
        self._browser_send("Browser.setInitScripts",
                           {"scripts": [{"script": s}
                                        for s in self._init_scripts]})
        for page in list(self.pages):
            try:
                page.injected.add_init_script(source)
            except Exception:
                pass
        handle = DisposableDispatcher(self.server, self,
                                      lambda: self._remove_init_script(source))
        return {"disposable": handle.channel}

    def _remove_init_script(self, source: str) -> None:
        """Undo one context-level init script, on both sides it was added to."""
        if source in self._init_scripts:
            self._init_scripts.remove(source)
        self._browser_send("Browser.setInitScripts",
                           {"scripts": [{"script": s}
                                        for s in self._init_scripts]})
        for page in list(self.pages):
            try:
                page.injected.remove_init_script(source)
            except Exception:
                pass

    def op_set_storage_state(self, params: Dict) -> Any:
        """⛔ COOKIES ONLY, and it refuses the rest rather than dropping it.
        A storage state carries cookies AND per-origin localStorage; writing
        the cookies and silently ignoring the origins would restore half a
        session and look like it worked, which is worse than saying no - the
        caller would debug the site instead of the tool.
        """
        state = params.get("storageState") or {}
        if state.get("origins"):
            raise ProtocolException(
                "set_storage_state() with per-origin localStorage is not "
                "implemented: restoring only the cookies would look like it "
                "worked and leave half the session missing. Use "
                "page.evaluate on each origin, or open an issue")
        cookies = state.get("cookies") or []
        if cookies:
            self._browser_send("Browser.setCookies", {"cookies": cookies})
        return None

    def op_set_interception(self, params: Dict) -> Any:
        """Turn request interception on or off for this context.

        ⛔ THE PATTERNS ARE NOT SENT, and that is a real narrowing that has to
        be said out loud rather than discovered. Juggler's
        `Browser.setRequestInterception` is a BOOLEAN: it intercepts
        everything or nothing. Playwright's client filters by url on its side
        and calls `continue` on what it does not want, so behaviour is correct
        - but every request now makes a round trip through this process, which
        a narrow pattern would have avoided. It is a cost, not a defect, and it
        is the reason `route()` on a busy page is slower here than upstream.
        """
        wanted = bool(params.get("patterns"))
        self._browser_send("Browser.setRequestInterception",
                           {"enabled": wanted})
        self.intercepting = wanted
        return None

    def op_set_ws_interception(self, params: Dict) -> Any:
        raise ProtocolException(
            "WebSocket routing is not implemented: Juggler reports websocket "
            "frames but has no command to hold or rewrite one, so a route that "
            "appeared to work would silently pass everything through")

    def op_noop(self, params: Dict) -> Any:
        return None

    def op_new_page(self, params: Dict) -> Any:
        conn = self.conn
        result = conn.send("Browser.newPage",
                           {"browserContextId": self.context_id}, timeout=30)
        target_id = result["targetId"]
        session = self.browser.session_for(target_id, timeout=20.0)
        page = PageDispatcher(self.server, self, session, target_id)
        self.pages.append(page)
        self.emit("page", {"page": page.channel})
        return {"page": page.channel}

    # ── cookies, permissions, geolocation ───────────────────────────────────
    def _browser_send(self, command: str, params: Dict) -> Any:
        params = dict(params)
        params["browserContextId"] = self.context_id
        return self.conn.send(command, params, timeout=30)

    def op_add_cookies(self, params: Dict) -> Any:
        """⛔ `expires` IS SECONDS AND -1 MEANS SESSION, not zero and not
        milliseconds. Playwright's client already speaks that convention, so
        the cookies pass through unchanged - but a translation added here
        "helpfully" would silently expire every session cookie in 1970."""
        self._browser_send("Browser.setCookies",
                           {"cookies": params.get("cookies") or []})
        return None

    def op_cookies(self, params: Dict) -> Any:
        result = self._browser_send("Browser.getCookies", {}) or {}
        cookies = result.get("cookies") or []
        urls = params.get("urls") or []
        if urls:
            wanted = [_host_of(u) for u in urls]
            cookies = [c for c in cookies
                       if any(_domain_matches(c.get("domain") or "", h)
                              for h in wanted)]
        return {"cookies": cookies}

    def op_clear_cookies(self, params: Dict) -> Any:
        # ⛔ Juggler clears the WHOLE context: it takes no filter. Playwright's
        # client can ask for a subset, and pretending to honour that by
        # clearing everything would be worse than refusing - so the filtered
        # form is refused and the unfiltered one works.
        if any(params.get(k) for k in ("name", "domain", "path")):
            raise ProtocolException(
                "clear_cookies() with a filter is not supported: the engine "
                "command clears the whole context, and quietly clearing more "
                "than asked is worse than refusing")
        self._browser_send("Browser.clearCookies", {})
        return None

    def op_grant_permissions(self, params: Dict) -> Any:
        self._browser_send("Browser.grantPermissions",
                           {"origin": params.get("origin") or "",
                            "permissions": params.get("permissions") or []})
        return None

    def op_clear_permissions(self, params: Dict) -> Any:
        self._browser_send("Browser.resetPermissions", {})
        return None

    def op_set_geolocation(self, params: Dict) -> Any:
        """⛔ NULL clears the override, and that is not the same as sending
        zeroes: latitude 0 longitude 0 is a real place in the Atlantic, and a
        page that reads it gets a fix instead of a refusal."""
        self._browser_send("Browser.setGeolocationOverride",
                           {"geolocation": params.get("geolocation")})
        return None

    def op_set_offline(self, params: Dict) -> Any:
        raise ProtocolException(
            "set_offline() has no engine command in this Juggler: the "
            "protocol declares no offline override, so honouring it would "
            "mean lying about the network state")

    def op_set_extra_headers(self, params: Dict) -> Any:
        raise ProtocolException(
            "set_extra_http_headers() is not wired yet: it needs request "
            "interception, which is the network group")

    def op_storage_state(self, params: Dict) -> Any:
        """⛔ COOKIES ONLY, and it says so. Upstream also collects localStorage
        per origin by evaluating in every page; returning just the cookies with
        an empty origins list would look complete and silently lose half the
        state a caller is trying to save."""
        result = self._browser_send("Browser.getCookies", {}) or {}
        return {"cookies": result.get("cookies") or [], "origins": []}

    def op_close(self, params: Dict) -> Any:
        try:
            self.conn.send("Browser.removeBrowserContext",
                                   {"browserContextId": self.context_id},
                                   timeout=10)
        except Exception:
            pass
        # ⛔ THE PAGES ARE TOLD FIRST, and this was missing entirely.
        # Measured against the Node driver on the same session: the driver
        # emits `close` on the page, the context AND the browser; this server
        # emitted the last two. The consequence is not cosmetic - the client
        # sets `is_closed()` and fires `page.on("close")` from that event, so
        # a page belonging to a closed context stayed "open" forever, and any
        # code waiting for it to close waited for good.
        for page in list(self.pages):
            try:
                page.announce_closed()
            except Exception:
                pass
        self.emit("close")
        self.dispose()
        return None


class BrowserDispatcher(Dispatcher):
    TYPE = "Browser"
    METHODS = {"newContext": "op_new_context", "close": "op_close",
               "newPage": "op_new_page"}

    def __init__(self, server, browser_type: "BrowserTypeDispatcher",
                 conn: Any, version: str) -> None:
        self.conn = conn
        self.browser_type = browser_type
        self._sessions: Dict[str, str] = {}
        self._sessions_ready = threading.Condition()
        # ⛔ THE EVENTS OF A SESSION START BEFORE ANYBODY IS LISTENING, and
        # that is not a corner case: measured on 2026-08-28 at the raw
        # protocol level, `Page.frameAttached` and the two
        # `Runtime.executionContextCreated` arrive at 0.65 s while the
        # `Browser.newPage` REPLY comes at 0.70 s. The reply is what tells this
        # server which session to build a `PageDispatcher` for, so every
        # consumer that dispatcher wires - the lifecycle, which learns the main
        # frame only from `frameAttached`, and the injected script, which
        # learns the worlds only from `executionContextCreated` - is registered
        # after its own events have already gone past.
        #
        # ⛔ IT USUALLY WORKED, WHICH IS WHY IT SHIPPED. On a quiet machine
        # the reply and the events interleave the other way often enough that
        # nothing is lost, and `wait_for_main_frame`'s own docstring predicted
        # the failure exactly: "it fails under load, on somebody else's
        # machine, once in twenty runs". What made it deterministic was an
        # unrelated command - `Browser.setTimezoneOverride` on the context -
        # which shifts the timing just enough to lose the race EVERY time. The
        # engine was innocent: the same sequence by hand answers in 0.7 s with
        # the frame announced.
        #
        # So the fix is not to stop sending that command. It is to stop
        # dropping events that arrived before their reader existed.
        self._buffered: Dict[str, List] = {}
        self._buffer_lock = threading.Lock()
        #: Sessions that already have a reader. Buffering one of these would be
        #: a leak with no purpose, and replaying into it would deliver every
        #: event twice.
        self._live: set = set()
        #: A page that never gets a `PageDispatcher` would otherwise buffer for
        #: the life of the browser. Enough to hold the burst that precedes a
        #: `newPage` reply, far too little to be a leak.
        self.BUFFER_CAP = 256
        # ⛔ THIS ONE RUNS FIRST NOW, WHERE THE CHAIN RAN IT LAST, and the
        # change is deliberate rather than incidental. A chain calls the most
        # recently installed link first, so the browser's recorder - installed
        # before any page existed - was the last thing every event reached; a
        # list registered in order calls it first. Nothing depends on a page's
        # subscriber seeing an event before this one records the session, and
        # recording earlier can only shorten the window in which `session_for`
        # is waiting for something that has already arrived.
        conn.add_listener(self._route_browser_event)
        conn.send("Browser.enable", {"attachToDefaultContext": True},
                  timeout=30)
        super().__init__(server, browser_type,
                         {"version": version, "name": "firefox",
                          "browserName": "firefox"})
        self.contexts: List[BrowserContextDispatcher] = []

    def _route_browser_event(self, method: str, params: Dict, session) -> None:
        if method == "Browser.attachedToTarget":
            info = params.get("targetInfo") or {}
            with self._sessions_ready:
                self._sessions[info.get("targetId")] = params.get("sessionId")
                self._sessions_ready.notify_all()
        if session:
            with self._buffer_lock:
                if session not in self._live:
                    held = self._buffered.setdefault(session, [])
                    if len(held) < self.BUFFER_CAP:
                        held.append((method, params))

    def replay(self, session: str, deliver) -> int:
        """Hand a new consumer the events of its session that it missed.

        ⛔ DELIVERED IN ORDER AND EXACTLY ONCE. The buffer is dropped as it is
        replayed, under the same lock that fills it, so an event arriving
        during the replay is either in the list or goes to the live path -
        never both, which would announce a frame twice, and never neither,
        which is the bug this exists for.
        """
        with self._buffer_lock:
            self._live.add(session)
            held = self._buffered.pop(session, None) or []
        for method, params in held:
            deliver(method, params, session)
        return len(held)

    def forget(self, session: str) -> None:
        """Stop holding events for a session nobody is going to read."""
        with self._buffer_lock:
            self._buffered.pop(session, None)
            self._live.discard(session)

    def session_for(self, target_id: str, timeout: float) -> str:
        """⛔ The session arrives as an EVENT, not in the reply to `newPage`.
        Polling the dict without waiting on the condition is a race that passes
        on a fast machine and fails on a loaded one."""
        import time
        deadline = time.monotonic() + timeout
        with self._sessions_ready:
            while target_id not in self._sessions:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ProtocolException(
                        "no session was attached for target %s in %.0fs"
                        % (target_id, timeout))
                self._sessions_ready.wait(left)
            return self._sessions[target_id]

    #: Context options that are ENGINE state, with the Juggler command and the
    #: field name each one travels in.
    #:
    #: ⛔ RECEIVING AN OPTION IS NOT APPLYING IT. These arrived in `params`,
    #: were stored on the dispatcher, and were handed back to the client in the
    #: initializer - so everything looked wired, and the client believed the
    #: context had them. Nothing ever reached the browser. The measurable
    #: consequence is a session that declares `timezone_id="America/New_York"`
    #: and whose pages report the host's zone, which is the
    #: `timezone_mismatch` signal this project exists to avoid, produced by the
    #: automation rather than by the proxy.
    #:
    #: ⛔ AND THE TIMEZONE IS NOT A DUPLICATE OF THE PREF. It looks like one -
    #: the profile already carries a zone - and the wrapper's own comment says
    #: why it is not: `juggler.timezone.override` goes through
    #: `JS::SetTimeZoneOverride`, which on Windows ICU silently falls back to
    #: the host zone for no-DST IANA names (America/Phoenix, Pacific/Honolulu).
    #: The per-realm path here works for every zone, which is why the wrapper
    #: passes it, and why dropping it would be a silent regression on exactly
    #: the zones nobody tests.
    ENGINE_OPTIONS = (
        ("locale", "Browser.setLocaleOverride", "locale"),
        # ⛔ KEPT, after being measured twice and nearly dropped once. Sending
        # it made every page creation time out, and the first reading was that
        # the command was the problem: the profile already carries the zone -
        # `build_launch_plan` writes `juggler.timezone.override` and calls it
        # the sole source - and a page with NO override reported
        # America/Phoenix and Pacific/Honolulu correctly, the two no-DST zones
        # whose breakage is the recorded reason the wrapper passes this option
        # at all. So the case for deleting it looked complete.
        #
        # ⛔ IT WAS THE WRONG CULPRIT. Driven by hand at the protocol level the
        # same command answers and the page announces its frame in 0.7 s. What
        # it actually did was shift the timing enough to lose a race this
        # server already had - see the event buffer in `BrowserDispatcher`.
        # Deleting it would have "fixed" the symptom, left the race for the
        # next unlucky machine, and quietly dropped a documented Playwright
        # option: a caller asking ONE context for a different zone would have
        # been ignored. That is not a smaller promise than the engine's, it is
        # a broken one.
        ("timezoneId", "Browser.setTimezoneOverride", "timezoneId"),
        ("colorScheme", "Browser.setColorScheme", "colorScheme"),
        ("reducedMotion", "Browser.setReducedMotion", "reducedMotion"),
        ("forcedColors", "Browser.setForcedColors", "forcedColors"),
        ("userAgent", "Browser.setUserAgentOverride", "userAgent"),
    )

    def op_new_context(self, params: Dict) -> Any:
        result = self.conn.send("Browser.createBrowserContext",
                                {"removeOnDetach": True}, timeout=30)
        context_id = result["browserContextId"]
        self._apply_context_options(context_id, params)
        context = BrowserContextDispatcher(self.server, self, params,
                                           context_id)
        self.contexts.append(context)
        self.emit("context", {"context": context.channel})
        return {"context": context.channel}

    def _apply_context_options(self, context_id: str, params: Dict) -> None:
        """Push the options the caller asked for into the engine.

        ⛔ BEFORE THE FIRST PAGE EXISTS, and that ordering is the whole point:
        these are defaults a new page INHERITS, so applying them after
        `newPage` would leave the first page - the only one most sessions ever
        open - without them.

        ⛔ AND `no-preference` IS SENT, NOT SKIPPED. It looks like an "unset"
        value and it is not: the client omits the field entirely when the
        caller said nothing, so the string only ever arrives because somebody
        asked for it by name. Treating it as absent would mean a context that
        explicitly asks to express no preference silently keeps whatever the
        profile declared - the one case where the caller was most explicit.
        """
        for name, command, field in self.ENGINE_OPTIONS:
            value = params.get(name)
            if value in (None, ""):
                continue
            self.conn.send(command,
                           {"browserContextId": context_id, field: value},
                           timeout=10)
        # ⛔ The rest of the option set, each one a lever that was arriving
        # and going nowhere. They are grouped here rather than spread through
        # the dispatcher because they share one property: the client sends them
        # ONCE, as context options, and never again - so a place that forgets
        # one is a feature that silently does not exist rather than one that
        # fails.
        # A context may carry its OWN proxy, and Playwright documents it as
        # overriding the browser-level one. Same refusal as the launch path:
        # a context whose proxy cannot be expressed must not come back usable.
        if params.get("proxy"):
            try:
                proxy = parse_proxy(params["proxy"]).as_engine_command()
            except ValueError as exc:
                raise ProtocolException(
                    "new_context(proxy=...) cannot be applied: %s" % exc)
            self.conn.send("Browser.setContextProxy",
                           dict(proxy, browserContextId=context_id),
                           timeout=10)
        headers = params.get("extraHTTPHeaders")
        if headers:
            self.conn.send("Browser.setExtraHTTPHeaders",
                           {"browserContextId": context_id,
                            "headers": headers}, timeout=10)
        if params.get("offline"):
            self.conn.send("Browser.setOnlineOverride",
                           {"browserContextId": context_id,
                            "override": "offline"}, timeout=10)
        geolocation = params.get("geolocation")
        if geolocation:
            self.conn.send("Browser.setGeolocationOverride",
                           {"browserContextId": context_id,
                            "geolocation": geolocation}, timeout=10)
        credentials = params.get("httpCredentials")
        if credentials:
            self.conn.send("Browser.setHTTPCredentials",
                           {"browserContextId": context_id,
                            "credentials": credentials}, timeout=10)
        if params.get("ignoreHTTPSErrors"):
            self.conn.send("Browser.setIgnoreHTTPSErrors",
                           {"browserContextId": context_id,
                            "ignoreHTTPSErrors": True}, timeout=10)
        if params.get("bypassCSP"):
            self.conn.send("Browser.setBypassCSP",
                           {"browserContextId": context_id,
                            "bypassCSP": True}, timeout=10)
        # ⛔ Inverted on purpose: Playwright says `javaScriptEnabled=False`,
        # Juggler says `javaScriptDisabled=True`. Passing one straight into the
        # other would turn scripting OFF for every default context, which is
        # the kind of inversion that looks like the site being broken.
        if params.get("javaScriptEnabled") is False:
            self.conn.send("Browser.setJavaScriptDisabled",
                           {"browserContextId": context_id,
                            "javaScriptDisabled": True}, timeout=10)
        if params.get("hasTouch"):
            self.conn.send("Browser.setTouchOverride",
                           {"browserContextId": context_id,
                            "hasTouch": True}, timeout=10)
        permissions = params.get("permissions")
        if permissions:
            # ⛔ `"*"` is the wildcard Juggler tests for by name
            # (`origin === '*' || page._url.startsWith(origin)` in
            # `TargetRegistry.js`). An empty string would ALSO match every url
            # through the `startsWith` half, which is exactly the kind of
            # accident that works until somebody tightens that condition.
            self.conn.send("Browser.grantPermissions",
                           {"browserContextId": context_id, "origin": "*",
                            "permissions": permissions}, timeout=10)
        viewport = params.get("viewport")
        if viewport:
            # ⛔ `screen` is a SEPARATE option from `viewport` and rides in
            # the same command. Without it the page reports a screen the size
            # of its own window, which no real desktop has ever done and which
            # this project spends a pref on getting right.
            wanted: Dict[str, Any] = {"viewportSize": {
                "width": viewport["width"], "height": viewport["height"]}}
            screen = params.get("screen")
            if screen:
                wanted["screenSize"] = {"width": screen["width"],
                                        "height": screen["height"]}
            if params.get("deviceScaleFactor"):
                wanted["deviceScaleFactor"] = params["deviceScaleFactor"]
            if params.get("isMobile"):
                wanted["isMobile"] = True
            self.conn.send("Browser.setDefaultViewport",
                           {"browserContextId": context_id,
                            "viewport": wanted}, timeout=10)

    def op_new_page(self, params: Dict) -> Any:
        context = self.op_new_context(params)
        guid = context["context"]["guid"]
        return self.server.object(guid).op_new_page({})

    def op_close(self, params: Dict) -> Any:
        try:
            self.conn.close()
        except Exception:
            pass
        self.emit("close")
        self.dispose()
        return None


class BrowserTypeDispatcher(Dispatcher):
    TYPE = "BrowserType"
    METHODS = {"launch": "op_launch",
        "launchPersistentContext": "op_launch_persistent",
               }

    def __init__(self, server, executable_path: str = "") -> None:
        super().__init__(server, None,
                         {"executablePath": executable_path,
                          "name": "firefox"})

    def op_launch_persistent(self, params: Dict) -> Any:
        """A browser whose profile SURVIVES the session, plus its context.

        ⛔ THE PROFILE IS THE CALLER'S AND IS NOT WIPED. `launch()` makes a
        throwaway directory; here the caller names one they intend to reuse, so
        writing `user.js` into it on every launch is correct - the prefs must be
        re-applied - but deleting anything in it is not.

        ⛔ BOTH COME BACK, AND THE LINE THAT USED TO BE HERE SAID OTHERWISE.
        It read "the CONTEXT comes back, NOT the browser ... `_browser_type.py`
        reads `context` from the answer". The client reads BOTH: `result
        ["browser"]` first, to attach it to the browser type, and then
        `result["context"]`. Returning only the context raised `KeyError:
        'browser'` on every `profile_dir=` session - measured 2026-08-30
        against the published 0.8.0, so it shipped.

        It is this project's most repeated defect, in its purest form: the
        assertion was written against the COMMENT rather than against the code
        it describes, and no test opened a persistent context to find out.
        """
        directory = params.get("userDataDir")
        if not directory:
            raise ProtocolException(
                "launch_persistent_context() needs a userDataDir: without one "
                "it is just launch(), and the profile it is supposed to keep "
                "would be a temporary directory")
        browser_channel = self.op_launch(dict(params, userDataDir=directory))
        browser = self.server.object(browser_channel["browser"]["guid"])
        return {"browser": browser_channel["browser"],
                "context": browser.op_new_context(params)["context"]}

    def op_launch(self, params: Dict) -> Any:
        executable = params.get("executablePath")
        if not executable:
            raise ProtocolException(
                "launch needs an executablePath: invisible_playwright pins its "
                "own engine and never downloads one at launch time")
        # ⛔ MERGED ONTO THIS PROCESS'S ENVIRONMENT, NEVER REPLACING IT, and
        # the difference is a browser that cannot reach the network.
        #
        # A bare `pw.firefox.launch()` sends an EMPTY `env` list - the caller
        # named no variables, so there are none to name. Taking that literally
        # meant launching Firefox with an environment of exactly nothing: no
        # `SYSTEMROOT`, no `PATH`, no `TEMP`. The browser starts, the protocol
        # works, `about:blank` and `data:` URLs load - and every HTTP
        # navigation comes back `Page.navigationAborted` with
        # `NS_ERROR_OUT_OF_MEMORY`, an error that names neither the cause nor
        # the environment.
        #
        # ⛔ AND IT WAS INVISIBLE TO EVERYTHING. The product path passes a
        # FULL environment (`_session.build_env`), so every test and every gate
        # that goes through `InvisiblePlaywright` was unaffected; only a caller
        # using the vendored client directly hit it - which is exactly who
        # `get_default_stealth_prefs` exists for. It surfaced the first time a
        # gate ran on this transport with a bare launch.
        #
        # The driver has the same semantics: given no `env` it uses its own
        # process environment, and given some it adds them.
        env = dict(os.environ)
        env.update({e["name"]: e["value"] for e in (params.get("env") or [])})
        # ⛔ WHO MAKES THE PROFILE TAKES IT AWAY - AND ONLY THAT ONE. The
        # caller's `userDataDir` is theirs and survives the session by
        # definition; a directory we invented is ours and must not.
        #
        # Measured on 2026-08-28, after one day of development: 136 leftover
        # `invisible_profile_*` directories, **5,0 GB**. Nothing failed, nothing
        # warned - a Firefox profile is a few dozen megabytes and the disk just
        # goes. The project already has the same defect recorded for
        # Playwright's own throwaway profiles, 7.308 directories accumulated
        # over seven months, and this reproduced it in hours.
        ours = params.get("userDataDir") is None
        profile = params.get("userDataDir") or tempfile.mkdtemp(
            prefix="invisible_profile_")
        _write_user_js(profile, params.get("firefoxUserPrefs") or {})
        # ⛔ THE CALLER'S TIMEOUT, not ours. `launch(timeout=)` is a
        # documented option and this server ignored it, so a caller who
        # shortened it waited the full built-in 60 s anyway - and one who
        # LENGTHENED it, on a slow machine or a cold disk, was cut off at 60
        # regardless. Milliseconds on the wire, seconds here; `0` means "no
        # limit" in Playwright, which is the one value that must not become a
        # zero-second deadline.
        wanted = params.get("timeout")
        ready = 60.0
        if isinstance(wanted, (int, float)) and wanted > 0:
            ready = float(wanted) / 1000.0
        # ⛔ PARSED BEFORE THE BROWSER STARTS, so a proxy we cannot express
        # refuses the launch instead of leaving a process running without one.
        proxy = None
        if params.get("proxy"):
            try:
                proxy = parse_proxy(params["proxy"]).as_engine_command()
            except ValueError as exc:
                raise ProtocolException("launch(proxy=...) cannot be applied: "
                                        "%s" % exc)
        conn = juggler.launch(executable, profile,
                              headless=bool(params.get("headless", True)),
                              env=env, argv_extra=params.get("args") or [],
                              ready_timeout=ready)
        self.server.on_shutdown(conn.close)
        # ⛔ AND SENT BEFORE ANY PAGE EXISTS. The driver used to do this and
        # nothing here replaced it, so `proxy=` was accepted and dropped for
        # every scheme the engine prefs do not carry - measured 2026-08-30, a
        # page resolved its own DNS and went out on the host address while the
        # session's timezone, locale and WebRTC candidate had all been resolved
        # THROUGH the proxy. Announcing one country and connecting from another
        # is worse than having no proxy at all.
        if proxy is not None:
            try:
                conn.send("Browser.setBrowserProxy", proxy, timeout=10)
            except BaseException as exc:
                conn.close()
                raise ProtocolException(
                    "the engine refused the proxy, so the browser was closed "
                    "rather than left running without one: %s" % exc)
        if ours:
            # ⛔ AFTER `conn.close`, and the order is the point: the hooks run
            # in reverse, so this one runs LAST - the browser is already gone
            # and no longer holds a lock on the profile. Removing it first
            # fails on Windows and fails SILENTLY, because the hook runner
            # swallows one hook's failure so it cannot stop the others.
            self.server.on_shutdown(lambda: _remove_profile(profile))
        version = _read_version(executable)
        browser = BrowserDispatcher(self.server, self, conn, version)
        return {"browser": browser.channel}








# ── the root ────────────────────────────────────────────────────────────────
class PlaywrightDispatcher(Dispatcher):
    TYPE = "Playwright"
    METHODS = {}


class LocalUtilsDispatcher(Dispatcher):
    TYPE = "LocalUtils"
    METHODS = {"addStackToTracingNoReply": "op_noop",
               "traceDiscarded": "op_noop"}

    def __init__(self, server) -> None:
        super().__init__(server, None, {"deviceDescriptors": []},
                         guid="localUtils")

    def op_noop(self, params: Dict) -> Any:
        return None


class JugglerServer(Server):
    """The whole Playwright protocol, answered in this process."""

    def object(self, guid: str) -> Dispatcher:
        obj = self._objects.get(guid)
        if obj is None:
            raise ProtocolException("no object %r" % guid)
        return obj

    #: ⛔ DECLARED like every other table, so the inventory that reads this
    #: file can SEE it. `initialize` is the only method the root answers and it
    #: was implemented from the first day, but it lived in an `if` inside
    #: `handle_root` - so the tool that derives coverage from the METHODS
    #: tables reported it as missing, and the count was wrong by one in the
    #: direction that makes you write code twice.
    METHODS = {"initialize": "op_initialize"}

    def handle_root(self, method: str, params: Dict) -> Any:
        name = self.METHODS.get(method)
        if name is None:
            raise ProtocolException(
                "the root only answers %s, not %r"
                % (", ".join(sorted(self.METHODS)), method))
        return getattr(self, name)(params)

    def op_initialize(self, params: Dict) -> Any:
        # ⛔ The order below is the recorded one: BrowserType, then LocalUtils,
        # then Playwright naming both. Announcing Playwright first would name
        # two guids the client has never seen.
        browser_type = BrowserTypeDispatcher(self)
        utils = LocalUtilsDispatcher(self)
        playwright = PlaywrightDispatcher(
            self, None, {"firefox": browser_type.channel,
                         "utils": utils.channel},
            guid="Playwright")
        return {"playwright": playwright.channel}










