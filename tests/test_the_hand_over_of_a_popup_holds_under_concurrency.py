"""The hand-over of a popup's page, from the buffer to its dispatcher, holds when the engine's reader, the adoption thread and a close run AT THE SAME TIME.

NO BROWSER HERE (the real dispatchers over a scripted Juggler connection; the parking points and the record of
`test_a_commit_at_any_point_of_the_hand_over_reaches_the_client_once`). The earlier tests deliver the events while the adoption thread is
parked, one thread at a time. These put the TWO threads in the window that the parking cannot reach: the engine's reader between the lifecycle's
listener and the page's own (the lifecycle hears an event first, the page second, and the hand-over can fall between them), the reader
against the loop that gives the held events back, a close against the hook. Each is one sequence of events, written out, no sampling.

  A  the reader is stopped between the lifecycle's listener and the page's for a `load`; the hand-over runs; the reader goes on.
     The client must be told the `load` of the frame ONCE after the page is announced (twice, `page.on("load")` fires twice).
  B  the adoption thread is delivering the held events (a, b); the engine sends c. The client must be told a, b, c.
  C  the page is closed (`close`, `__dispose__`) after the held events are given back and before the lifecycle's hook goes in, while the
     lifecycle knows a frame the client was not told of. Nothing may be sent for the page's frames after its `__dispose__`.
  D  the reader and the hand-over both need the dispatcher of a frame that does not have one yet. It is made once.
  E  a document is committed at the SAME address as the one in the initializer, after the snapshot and before the hook. The client must be told it.

Known-bad input: 7d934a6 -> all five are red, each for the reason named in its assertion.
"""
from __future__ import annotations

import threading

import pytest

from invisible_playwright._juggler import dispatcher as base
from invisible_playwright._juggler import remote
from invisible_playwright._juggler import server as server_mod
from invisible_playwright._juggler.server import LocalUtilsDispatcher

from test_a_commit_at_any_point_of_the_hand_over_reaches_the_client_once import POINTS, Parking, PopupEngine, _install
from test_every_order_of_the_events_of_a_popup_gives_one_page_event import FRAME, SESSION, TARGET, _thread, _wait

pytestmark = pytest.mark.unit

CHILD = "fx2"
BLANK = "about:blank"
STATES = ("commit", "domcontentloaded", "load", "networkidle")


def _adopter():
    return threading.current_thread().name == "invpw-adopt-%s" % TARGET


class World:
    """A served browser over the scripted engine, the opener built, the context known."""

    def __init__(self):
        self.server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(self.server)
        LocalUtilsDispatcher(self.server)
        self.conn = PopupEngine()
        self.browser = remote.SharedBrowserDispatcher(self.server, browser_type, self.conn, "firefox/1")
        self.context = self.server.object(
            self.server.handle({"guid": self.browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
        self.server.handle({"guid": self.context.guid, "method": "newPage", "params": {"pageNumber": 1}})
        self.opener_target = next(t for t in self.conn.sessions if t != TARGET)

    def born(self):
        self.conn.attach(self.opener_target, self.context.context_id)
        self.conn.frame()
        self.conn.commit()

    def close(self):
        try:
            self.server.shutdown()
        except Exception:
            pass


def _mark_announcement(mp, told, announced):
    """`told['announced_at']`: how many things the client had been told of the main frame when the context said `page`."""
    original = base.Dispatcher.emit

    def spy(self, method, params=None):
        if method == "page" and getattr(self, "TYPE", "") == "BrowserContext" and _adopter():
            told["announced_at"] = len(told.get("ops", []))
            announced.append(True)
        return original(self, method, params)
    mp.setattr(base.Dispatcher, "emit", spy)


def _gate_page_listener(mp, gate, entered, match):
    """The page's own listener of the popup stops at `gate` for the events `match` picks: the lifecycle's listener, registered first, has
    heard them already - which is the window between the two."""
    original = server_mod.PageDispatcher._route_juggler_event

    def wrapper(self, method, params, session):
        if getattr(self, "target_id", None) == TARGET and match(method, params):
            entered.set()
            gate.wait(10)
        return original(self, method, params, session)
    mp.setattr(server_mod.PageDispatcher, "_route_juggler_event", wrapper)


def _finish(world, announced):
    world.conn.ready()
    assert _wait(lambda: announced), "the popup was never announced"
    assert _wait(lambda: _thread() is None), "the adoption thread never ended"


# --- A ----------------------------------------------------------------------------------------------------------------------------------------

def test_a_load_heard_by_the_lifecycle_between_its_listener_and_the_pages_is_told_once():
    """Sequence: [reader] lifecycle hears Page.eventFired(load) -> [reader] page listener stops -> [adoption] _install_events: the held events are
    given back, the hook goes in, the frames are reconciled: `load` is in the lifecycle's states and not in the client's, so it is told ->
    [reader] page listener goes on: the same event, now delivered live, tells it again.  The replay after the announcement says what was said
    before it, so the client hears the load twice and `page.on("load")` runs twice."""
    parking, told, announced, gate, entered = Parking(), {}, [], threading.Event(), threading.Event()
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        _mark_announcement(mp, told, announced)
        _gate_page_listener(mp, gate, entered, lambda m, p: m == "Page.eventFired" and p.get("name") == "load")
        world = World()
        reader = None
        try:
            world.born()
            for i in range(len(POINTS)):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                if i == 2:
                    reader = threading.Thread(target=world.conn.load, daemon=True)
                    reader.start()
                    assert entered.wait(5), "the reader never reached the page's listener"
                if i == 3:
                    gate.set()                  # the hand-over is over: the reader goes on with the same event
                    reader.join(5)
                parking.release[i].set()
            _finish(world, announced)
            after = told["ops"][told["announced_at"]:]
            loads = [op for op in after if op == ("add", "load")]
            assert len(loads) == 1, "after the page was announced the client was told `load` %d times: %s" % (len(loads), after)
        finally:
            gate.set()
            parking.open_all()
            world.close()


# --- B ----------------------------------------------------------------------------------------------------------------------------------------

def _console(conn, number):
    conn.dispatch_event("Runtime.console", {"type": "log", "args": [], "location": {"url": "", "lineNumber": number, "columnNumber": 0}}, SESSION)


def test_an_event_sent_while_the_held_ones_are_given_back_does_not_overtake_them():
    """Sequence: held = [console 1, console 2] -> [adoption] gives back console 1, stops inside the loop -> [reader] the engine sends console 3 ->
    [adoption] goes on, gives back console 2.  The hold is released as the FIRST thing the hand-over does, and the reader's check of it takes
    no lock, so 3 goes straight to the handler: the client hears 1, 3, 2.  (A response before its request is dropped as unknown.)"""
    parking, told, announced = Parking(), {}, []
    heard, inside, go = [], threading.Event(), threading.Event()
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        _mark_announcement(mp, told, announced)
        original = server_mod.PageDispatcher._deliver

        def deliver(self, method, params):
            result = original(self, method, params)         # the close's lock is let go when this returns: the pause is outside it
            if getattr(self, "target_id", None) == TARGET and method == "Runtime.console":
                heard.append(params["location"]["lineNumber"])
                if _adopter() and len(heard) == 1:
                    inside.set()
                    go.wait(10)
            return result
        mp.setattr(server_mod.PageDispatcher, "_deliver", deliver)
        world = World()
        try:
            world.born()
            for i in range(len(POINTS)):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                if i == 2:
                    _console(world.conn, 1)
                    _console(world.conn, 2)
                    parking.release[i].set()
                    assert inside.wait(5), "the adoption thread never gave back the first held event"
                    _console(world.conn, 3)     # the engine's reader, while the adoption thread is inside the loop
                    go.set()
                    continue
                parking.release[i].set()
            _finish(world, announced)
            assert heard == [1, 2, 3], "the page heard the messages in the order %s: a message the engine sent later overtook one it held" % heard
        finally:
            go.set()
            parking.open_all()
            world.close()


# --- C ----------------------------------------------------------------------------------------------------------------------------------------

def test_a_page_closed_before_the_hook_goes_in_is_not_given_a_frame_after_its_dispose():
    """Sequence: [adoption] the held events are given back, stops before the hook -> the engine attaches a child frame (the lifecycle knows it,
    the client does not) -> [caller] the page is closed: `close`, `__dispose__` -> [adoption] goes on: the hook is installed on a closed page and
    the frames reconciled, the child's dispatcher is made under the disposed page and its `__create__` is sent."""
    parking, told, announced = Parking(), {}, []
    sent, caught, at_hook, go = [], [], threading.Event(), threading.Event()
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        _mark_announcement(mp, told, announced)
        original = server_mod.PageDispatcher._hear_lifecycle

        def hear(self):
            if _adopter() and not caught:
                caught.append(self)
                at_hook.set()
                go.wait(10)
            return original(self)
        mp.setattr(server_mod.PageDispatcher, "_hear_lifecycle", hear)
        world = World()
        mp.setattr(world.server, "send_up", lambda message: sent.append(message))
        try:
            world.born()
            for i in range(3):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                parking.release[i].set()
            assert at_hook.wait(5), "the adoption thread never reached the hook"
            world.conn.dispatch_event("Page.frameAttached", {"frameId": CHILD, "parentFrameId": FRAME}, SESSION)
            page = caught[0]
            page.announce_closed()
            disposed_at = next(i for i, m in enumerate(sent) if m.get("method") == "__dispose__" and m.get("guid") == page.guid)
            go.set()
            parking.open_all()
            assert _wait(lambda: _thread() is None), "the adoption thread never ended"
            after = [m for m in sent[disposed_at + 1:] if m.get("method") == "__create__"]
            assert not after, "created after the page's __dispose__: %s" % [(m["params"]["type"], m["params"]["guid"], "under", m["guid"]) for m in after]
        finally:
            go.set()
            parking.open_all()
            world.close()


# --- D ----------------------------------------------------------------------------------------------------------------------------------------

def test_a_frame_the_reader_and_the_hand_over_both_need_is_made_once():
    """Sequence: the lifecycle knows a child frame the client was not told of; its `load` is heard by the lifecycle and the reader stops before the
    page's listener -> [adoption] stops before the hook -> both go on at once: the reader's handler (`Page.eventFired`) and the reconciliation each
    ask for the dispatcher of the child, each finds none, each makes one.  The client gets two `__create__` of a Frame for one frame id."""
    parking, told, announced = Parking(), {}, []
    gate, entered, at_hook, go = threading.Event(), threading.Event(), threading.Event(), threading.Event()
    made = []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        _mark_announcement(mp, told, announced)
        _gate_page_listener(mp, gate, entered, lambda m, p: m == "Page.eventFired" and p.get("frameId") == CHILD)
        meeting = threading.Barrier(2, timeout=0.6)
        before = server_mod.FrameDispatcher.__init__

        def frame_init(self, server, page, frame_id, *args, **kwargs):
            if frame_id == CHILD:
                made.append(threading.current_thread().name)
                try:
                    meeting.wait()              # a second maker is the case: where there is a lock, the second one waits outside and this times out
                except threading.BrokenBarrierError:
                    pass
            return before(self, server, page, frame_id, *args, **kwargs)
        mp.setattr(server_mod.FrameDispatcher, "__init__", frame_init)
        original = server_mod.PageDispatcher._hear_lifecycle

        def hear(self):
            if _adopter() and not at_hook.is_set():
                at_hook.set()
                go.wait(10)
            return original(self)
        mp.setattr(server_mod.PageDispatcher, "_hear_lifecycle", hear)
        world = World()
        reader = None
        try:
            world.born()
            for i in range(3):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                parking.release[i].set()
            world.conn.dispatch_event("Page.frameAttached", {"frameId": CHILD, "parentFrameId": FRAME}, SESSION)
            reader = threading.Thread(target=world.conn.dispatch_event, args=("Page.eventFired", {"frameId": CHILD, "name": "load"}, SESSION), daemon=True)
            reader.start()
            assert entered.wait(5), "the reader never reached the page's listener"
            assert at_hook.wait(5), "the adoption thread never reached the hook"
            gate.set()
            go.set()
            reader.join(10)
            parking.open_all()
            _finish(world, announced)
            assert len(made) == 1, "the dispatcher of one frame was made %d times (by %s): the client was told of it %d times" % (len(made), made, len(made))
        finally:
            gate.set()
            go.set()
            parking.open_all()
            world.close()


# --- E ----------------------------------------------------------------------------------------------------------------------------------------

def test_a_document_committed_at_the_same_address_between_the_snapshot_and_the_hook_is_told_to_the_client():
    """Sequence: the blank page has loaded (the initializer says about:blank, domcontentloaded, load) -> [reader] the page commits ANOTHER document at
    about:blank, after the snapshot -> the hook goes in.  The reconciliation compares the address and the name: they are the same, so no
    `navigated` is sent, and the client's frame keeps the old document's identity (a reload that waits for its navigation is never told it)."""
    parking, told, announced = Parking(), {}, []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        _mark_announcement(mp, told, announced)
        world = World()
        try:
            world.born()
            world.conn.dcl()
            world.conn.load()
            for i in range(len(POINTS)):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                if i == 1:      # parked as the frame's dispatcher is made: the initializer is the snapshot already taken
                    world.conn.dispatch_event("Page.navigationStarted", {"frameId": FRAME, "navigationId": "n2", "url": BLANK}, SESSION)
                    world.conn.dispatch_event("Page.navigationCommitted", {"frameId": FRAME, "navigationId": "n2", "url": BLANK, "name": ""}, SESSION)
                parking.release[i].set()
            _finish(world, announced)
            assert {"domcontentloaded", "load"} <= set(told["states"]), "the snapshot should carry the blank page's states: %s" % told["states"]
            page = world.browser._page_targets.get(TARGET)
            known = page.lifecycle.frames[FRAME]
            navigated = [value for op, value in told["ops"] if op == "url"]
            assert navigated == [BLANK], (
                "the client was told of %d document(s) committed after the snapshot, the lifecycle heard one (states now %s)"
                % (len(navigated), sorted(s for s in known.states if s in STATES)))
        finally:
            parking.open_all()
            world.close()
