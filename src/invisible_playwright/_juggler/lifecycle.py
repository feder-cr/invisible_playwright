"""The lifecycle: frame tree, navigations, load state.

⛔ THIS IS THE PIECE THAT DECIDES WHETHER THE BREAK FROM PLAYWRIGHT HOLDS UP,
and the reason is how it fails: it does not crash, it "works nineteen times
out of twenty". This is why the comments here say WHY a line is the way it
is, not what it does.

THE CLASSIC DEFECT, and the reason for half of this file: **states must be
kept per NAVIGATION, not per frame.** If kept per frame, a `load` left over
from the PREVIOUS document satisfies the wait for the next one, and
`goto()` returns before the page exists. It is the error that almost never
shows, because it needs the two events to arrive in the wrong order.

THE CORRELATION, which the protocol does NOT hand you. `Page.eventFired`
carries `frameId` and `name` (`load` or `DOMContentLoaded`) and **does not
carry the navigationId**. So membership is established by ORDER: after the
`navigationCommitted` of navigation N, the first `load` of that frame
belongs to N. That is what makes it mandatory to clear the states on
`navigationStarted`.

THE FOUR WAITS, and which event closes each:

    commit             Page.navigationCommitted
    domcontentloaded   Page.eventFired name=DOMContentLoaded
    load               Page.eventFired name=load
    networkidle        `load`, then zero inflight requests for IDLE_QUIET
                       seconds (`Lifecycle._settle`, the only place)

⛔ networkidle IS A STATE LIKE THE OTHER THREE, kept in `Frame.states` and
cleared with them, and `announce` hands it to the page so the client hears it
too. It used to be a predicate evaluated inside `wait_for_state`, which only a
waiter in THIS process could see: the client's `wait_for_load_state(
"networkidle")` waits for a `loadstate` event that was never sent.

⛔ `sameDocumentNavigation` does NOT clear anything: it is the same
document, and a history push does not reload the page. Treating it as a
navigation means waiting for a `load` that will never arrive.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

#: How much silence the network needs before it counts as "still". It is
#: Playwright's value: shorter, and a pause between two requests would read
#: as quiet.
IDLE_QUIET = 0.5

STATES = ("commit", "domcontentloaded", "load", "networkidle")


class NavigationError(RuntimeError):
    """The navigation was aborted by the browser, with its text."""


class Frame:
    def __init__(self, frame_id: str, parent: Optional[str]):
        self.id = frame_id
        self.parent = parent
        self.url = ""
        #: the current navigation, and ONLY its states
        self.navigation: Optional[str] = None
        self.states: set = set()
        #: the aborted navigations, with their text, for whoever was
        #: waiting on them
        self.aborted: dict = {}
        #: every navigation this frame has shown, numbered in the order the
        #: browser reported it. See `follows`.
        self._order: dict = {}
        #: how many same-document navigations the browser has reported, so a
        #: `goto` that creates no document can wait for the one it caused.
        self.same_document = 0

    def saw(self, navigation: Optional[str]) -> None:
        """Number `navigation` the first time the browser mentions it."""
        if navigation is not None and navigation not in self._order:
            self._order[navigation] = len(self._order)

    def follows(self, navigation: str) -> bool:
        """Is the current navigation `navigation` itself, or one that came
        AFTER it?

        ⛔ TWO DEFECTS SIT ON EITHER SIDE OF THIS LINE, and each fix alone
        re-opens the other.

        Before ours: `Page.navigate` answers with the navigationId before
        `navigationStarted` arrives, and in that window the frame still
        carries the states of the PREVIOUS document. Accepting them made
        `goto(until="commit")` return in 0.01s on `about:blank` (2026-08-27).
        A navigation we have not seen yet is therefore never followed.

        After ours: a page that replaces itself from script while it loads -
        `location.replace`, a JavaScript challenge, YouTube's `?themeRefresh=1`
        - starts a NEW navigation after ours committed, and ours never
        reaches `load`: its document is gone. Requiring `f.navigation ==
        ours` made every such `goto` time out on a page that was ready, 45 s
        on YouTube and Reddit while stock Playwright answered in 2 to 4 s on
        the same Firefox (2026-09-25, [B228]). Stock Playwright waits for the
        frame's lifecycle after our navigation, whichever document carries
        it, and so does this.
        """
        if self.navigation == navigation:
            return True
        mine = self._order.get(navigation)
        current = self._order.get(self.navigation)
        return mine is not None and current is not None and current > mine


class Lifecycle:
    """Follows a single page. Hooks into a `Connection`'s events."""

    def __init__(self, connection, session: str):
        self.c = connection
        self.session = session
        self.frames: dict = {}
        self.main_frame: Optional[str] = None
        self.ready = False
        self._inflight = 0
        self._last_activity = time.monotonic()
        self._cv = threading.Condition()
        #: Who hears what only this object can decide: `announce(what,
        #: frame_id, url=None)`, called UNDER the lock and BEFORE any waiter
        #: is woken, so whatever it sends upward is on its way before the
        #: answer of a wait that the same event satisfies. Set by the page
        #: once its channel exists.
        self.announce = None
        #: The one pending silence check, or None. See `_settle`.
        self._settle_timer: Optional[threading.Timer] = None
        # ⛔ REGISTERED, NOT CHAINED, and the difference is not style: this
        # used to capture the previous `on_event` and install a closure over
        # it, so every page added a nested call to the delivery of every
        # event. The registry delivers to each subscriber in turn, which is
        # also why nothing here has to remember to pass the event on - the
        # observer that a chain could silence by forgetting one line cannot
        # be silenced by a list.
        connection.add_listener(self._route)

    def _route(self, method: str, params: dict,
               event_session: Optional[str]) -> None:
        if event_session == self.session:
            self._on_event(method, params)

    def detach(self) -> None:
        """Stop listening. Called when the page this follows goes away."""
        self.c.remove_listener(self._route)
        with self._cv:
            self.announce = None
            if self._settle_timer is not None:
                self._settle_timer.cancel()
                self._settle_timer = None

    def frame(self, frame_id: str):
        """The frame with this id, or None.

        ⛔ HERE AND NOT AT THE CALLER, because how frames are kept is this
        object's business. Callers used to reach in with
        `self.lifecycle.frames.get(id)` - two of them - and each one of those
        also asserted that `frames` is a dict keyed by id. `_frame()` below
        CREATES one when it is missing, which is right for an event arriving
        for a frame we never saw attach and wrong for a caller that only wants
        to look: the two readings need two names.
        """
        return self.frames.get(frame_id)

    # ── event intake ────────────────────────────────────────────────────────
    def _on_event(self, method: str, p: dict) -> None:
        with self._cv:
            f = self._apply(method, p)
            self._settle()
            if f is not None or method.startswith("Network."):
                self._cv.notify_all()

    def _settle(self) -> None:
        """Give networkidle to every frame whose document has loaded, once the
        network has been quiet for IDLE_QUIET. Called under the lock.

        ⛔ networkidle IMPLIES `load`, THE WAY `load` IMPLIES
        `domcontentloaded`. The request counter is page-wide and knows nothing
        about documents. With a `beforeunload` listener on the page being
        left, Firefox sends `navigationStarted` BEFORE the new document's
        request: the counter is zero and has been quiet for seconds - the OLD
        page's silence. Measured 2026-10-03 leaving a production single-page
        login app: `goto(..., "networkidle")` returned in 78 ms with no
        Response, still on the old page. `load` is cleared on
        `navigationStarted`, so requiring it measures the quiet on the frame's
        current document.

        ⛔ AND IT IS BORN ON A SILENCE DEADLINE, when NOTHING happens, so no
        event can deliver it: a timer does, one at a time. A check that finds
        activity since it was scheduled schedules the remainder.

        Once given it stays until the next document, as in Playwright: a
        request the page makes later does not take it back.
        """
        waiting = [f for f in self.frames.values()
                   if "load" in f.states and "networkidle" not in f.states]
        if not waiting or self._inflight:
            return
        quiet_for = time.monotonic() - self._last_activity
        if quiet_for < IDLE_QUIET:
            if self._settle_timer is None:
                self._settle_timer = threading.Timer(
                    IDLE_QUIET - quiet_for, self._settle_later)
                self._settle_timer.daemon = True
                self._settle_timer.start()
            return
        for f in waiting:
            f.states.add("networkidle")
            if self.announce is not None:
                self.announce("networkidle", f.id)
        self._cv.notify_all()

    def _settle_later(self) -> None:
        with self._cv:
            self._settle_timer = None
            self._settle()

    def _apply(self, method: str, p: dict):
        if method == "Page.ready":
            self.ready = True
            return None

        if method == "Page.frameAttached":
            fid = p["frameId"]
            self.frames[fid] = Frame(fid, p.get("parentFrameId"))
            if p.get("parentFrameId") is None:
                self.main_frame = fid
            return self.frames[fid]

        if method == "Page.frameDetached":
            fid = p["frameId"]
            # The whole subtree is removed, not just the one node: an
            # orphaned child would be left answering for a frame that no
            # longer exists.
            for x in [k for k, v in self.frames.items()
                      if k == fid or self._descendants(k, fid)]:
                self.frames.pop(x, None)
            return None

        if method == "Page.navigationStarted":
            f = self._frame(p["frameId"])
            f.navigation = p["navigationId"]
            f.saw(f.navigation)
            # ⛔ THIS is where the correctness of the whole file lives: the
            # states of the previous document do NOT count for this one.
            f.states = set()
            return f

        if method == "Page.navigationCommitted":
            f = self._frame(p["frameId"])
            nav = p.get("navigationId")
            if nav is not None:
                f.navigation = nav
                f.saw(nav)
            f.url = p.get("url", f.url)
            f.states.add("commit")
            return f

        if method == "Page.navigationAborted":
            f = self._frame(p["frameId"])
            f.aborted[p["navigationId"]] = p.get("errorText", "aborted")
            return f

        if method == "Page.sameDocumentNavigation":
            f = self._frame(p["frameId"])
            f.url = p.get("url", f.url)
            f.same_document += 1
            # ⛔ No clearing here: it is the same document.
            # Announced HERE, under the lock and before `_on_event` wakes a
            # waiter, so the page's `navigated` is on its way up before the
            # answer of a `goto` waiting for this very event.
            if self.announce is not None:
                self.announce("sameDocument", f.id, f.url)
            return f

        if method == "Page.eventFired":
            f = self._frame(p["frameId"])
            name = p.get("name")
            if name == "load":
                f.states.add("load")
                # `load` implies `domcontentloaded`: if an unexpected event
                # order meant the second one never arrived, whoever is
                # waiting for it would be stuck in front of a page that is
                # already loaded.
                f.states.add("domcontentloaded")
            elif name == "DOMContentLoaded":
                f.states.add("domcontentloaded")
            return f

        if method == "Network.requestWillBeSent":
            self._inflight += 1
            self._last_activity = time.monotonic()
        elif method in ("Network.requestFinished", "Network.requestFailed"):
            # It never drops below zero: a response without its request
            # does arrive for real, for instance for a load that started
            # before we hooked in, and a negative counter would make
            # `networkidle` unreachable forever.
            self._inflight = max(0, self._inflight - 1)
            self._last_activity = time.monotonic()
        return None

    def _descendants(self, fid: str, ancestor: str) -> bool:
        seen = set()
        cur = self.frames.get(fid)
        while cur and cur.parent and cur.parent not in seen:
            if cur.parent == ancestor:
                return True
            seen.add(cur.parent)
            cur = self.frames.get(cur.parent)
        return False

    def _frame(self, fid: str) -> Frame:
        # An event can name a frame we never saw attach (we hooked in on an
        # already-live page). It gets created instead of being lost.
        if fid not in self.frames:
            self.frames[fid] = Frame(fid, None)
            if self.main_frame is None:
                self.main_frame = fid
        return self.frames[fid]

    # ── waiting ─────────────────────────────────────────────────────────────
    def _reached(self, f: Frame, state: str) -> bool:
        # One rule for the four: networkidle is born in `_settle`, the
        # others at event intake, and all of them live in `f.states`.
        return state in f.states

    def wait_for_new_navigation(self, frame_id: str, previous: Optional[str],
                                state: str = "load",
                                timeout: float = 30.0) -> Optional[str]:
        """Wait for a navigation that is NOT the one already in progress, and
        answer with the id of the one it waited for.

        ⛔ THE ID IS RETURNED BECAUSE ONLY THIS FUNCTION KNOWS IT. The caller
        needs it to answer `reload` / `go_back` with a Response, and reading
        the frame's current navigation afterwards would be a different value
        the moment the page navigates on its own. It is computed here anyway.

        ⛔ THIS IS THE HISTORY CASE, AND WITHOUT IT `go_back` IS A NO-OP THAT
        REPORTS SUCCESS. `Page.goBack` answers `{success: true}` the moment the
        browser accepts the request, not when the new document is loaded. Wait
        for `load` at that instant and it is already set - by the document you
        are navigating AWAY from - so the wait returns immediately and the next
        read sees the old page.

        Measured on 2026-08-27: `go_back()` from the second page, then
        `title()`, answered "second". The states were right, the document was
        not, and nothing raised.

        `goto` does not need this because `Page.navigate` hands back the
        navigationId to wait on. History gives no id, so the only thing to
        anchor on is that the frame's CURRENT navigation has changed.
        """
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                f = self.frames.get(frame_id)
                if f is not None and f.navigation != previous:
                    break
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(
                        "no new navigation on frame %s in %.0fs: the previous "
                        "one (%s) is still current" % (frame_id, timeout,
                                                       previous))
                self._cv.wait(min(left, 0.05))
            navigation = self.frames[frame_id].navigation
        self.wait_for_state(frame_id, state, navigation=navigation,
                            timeout=max(0.05, deadline - time.monotonic()))
        return navigation

    def wait_for_main_frame(self, timeout: float = 20.0) -> str:
        """The main frame id, waiting for it to arrive.

        ⛔ IT ARRIVES AS AN EVENT, so reading `main_frame` right after attaching
        to a page is a race: it is set by `Page.frameAttached`, which the
        browser sends when it is ready and not when we ask. On a free machine
        the attribute is usually already there, which is exactly what makes
        this the kind of race that ships - it fails under load, on somebody
        else's machine, once in twenty runs.
        """
        deadline = time.monotonic() + timeout
        with self._cv:
            while self.main_frame is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(
                        "no main frame in %.0fs: the page never announced one. "
                        "Frames seen: %s" % (timeout, sorted(self.frames)))
                self._cv.wait(min(left, 0.05))
            return self.main_frame

    def wait_for_state(self, frame_id: str, state: str, *,
                        navigation: Optional[str] = None,
                        timeout: float = 30.0) -> None:
        if state not in STATES:
            raise ValueError("unknown state: %r (the four are %s)"
                              % (state, ", ".join(STATES)))
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                f = self.frames.get(frame_id)
                if f is not None:
                    if navigation and navigation in f.aborted:
                        raise NavigationError(f.aborted[navigation])
                    # ⛔ CLEARING THE STATES ON `navigationStarted` IS NOT
                    # ENOUGH, and this is the real defect measured on
                    # 2026-08-27.
                    #
                    # `Page.navigate` answers with the navigationId BEFORE
                    # `navigationStarted` has arrived. In that window the
                    # frame still carries the states of the previous
                    # document - `about:blank` already has commit,
                    # domcontentloaded and load - and whoever is waiting
                    # for `commit` finds it right away and returns.
                    # Measured: the first `goto(until="commit")` returned
                    # in 0.01s with `url=about:blank`, i.e. before it had
                    # even started.
                    #
                    # The fix is not to wait a moment: it is to require
                    # that the states belong to OUR navigation or to one
                    # that came after it (`Frame.follows` says why the
                    # second half is not optional). A navigation from
                    # BEFORE ours is not ours, no matter what it says.
                    if navigation is not None and not f.follows(navigation):
                        pass
                    elif self._reached(f, state):
                        return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if f is None:
                        reason = "the frame does not exist"
                    elif (navigation is not None
                          and not f.follows(navigation)):
                        # The message must say THIS, because it is the
                        # case where the states are there but are not
                        # ours, and without this line it would look like
                        # the browser is not responding.
                        reason = ("the current navigation is %s, not our "
                                  "%s" % (f.navigation, navigation))
                    else:
                        reason = ("states reached: %s"
                                  % (sorted(f.states) or "none"))
                    raise TimeoutError(
                        "%s not reached in %.0fs (%s, inflight requests: "
                        "%d)" % (state, timeout, reason, self._inflight))
                # networkidle comes true on a SILENCE DEADLINE, when nothing
                # happens; `_settle`'s timer notifies then, so every state
                # can sleep until the next notify.
                self._cv.wait(remaining)

    # ── navigation ──────────────────────────────────────────────────────────
    def goto(self, url: str, *, frame_id: Optional[str] = None,
              until: str = "load", timeout: float = 30.0,
              referer: Optional[str] = None) -> dict:
        fid = frame_id or self.main_frame
        if fid is None:
            raise RuntimeError("no frame: the page has not announced its "
                                "main frame yet")
        params = {"frameId": fid, "url": url}
        if referer:
            params["referer"] = referer
        deadline = time.monotonic() + timeout
        # Counted BEFORE the command: the engine may report the same-document
        # navigation before its reply arrives.
        with self._cv:
            before = self._frame(fid).same_document
        result = self.c.send("Page.navigate", params,
                              session=self.session, timeout=timeout) or {}
        nav = result.get("navigationId")

        # ⛔ A NULL `navigationId` is not an error: the protocol declares
        # it `Nullable`, and Juggler answers it for a url that differs from
        # the current one only by its fragment. There is no `load` to wait
        # for, and waiting for one would be a timeout on something that
        # succeeded.
        #
        # ⛔ BUT THE ANSWER WAITS FOR THE SAME-DOCUMENT NAVIGATION, as
        # upstream's `goto` does. Answering on the reply put it ahead of
        # `Page.sameDocumentNavigation`, and the client read the OLD url
        # after `goto` returned, until its next call delivered the event
        # (measured 2026-10-04, one step behind on every fragment goto).
        if nav is None:
            with self._cv:
                while self._frame(fid).same_document == before:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError(
                            "the same-document navigation to %s was not "
                            "reported in %.0fs" % (url, timeout))
                    self._cv.wait(left)
                return {"navigationId": None, "url": self.frames[fid].url}

        self.wait_for_state(fid, until, navigation=nav, timeout=timeout)
        return {"navigationId": nav, "url": self.frames[fid].url}

    # ── inspection ──────────────────────────────────────────────────────────
    def frame_tree(self) -> dict:
        return {fid: {"parent": f.parent, "url": f.url,
                       "states": sorted(f.states),
                       "navigation": f.navigation}
                for fid, f in self.frames.items()}

    @property
    def inflight(self) -> int:
        return self._inflight
