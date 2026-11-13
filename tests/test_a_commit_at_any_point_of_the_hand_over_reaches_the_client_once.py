"""The destination's commit of a popup, whenever it comes while the page is handed from the buffer to its dispatcher, reaches the client once.

NO BROWSER HERE (the real dispatchers over a scripted Juggler connection, as `test_every_order_of_the_events_of_a_popup_gives_one_page_event`).

A popup's page is built the moment the engine attaches its target, and what the client is told of its frame comes from two places: the
INITIALIZER (a snapshot of what the lifecycle knew when the frame dispatcher was made: url, name, load states) and the lifecycle's own
announcements (`navigated`, `loadstate`, a new frame), which go up through a hook (`Lifecycle.announce`) that the page installs at the
end of its construction (`_install_events` -> `_hear_lifecycle`). The page's own listener holds the events of the same period and
`_install_events` hands over only the `Network.*` ones. So an event that the lifecycle heard AFTER the snapshot and BEFORE the hook was
told to nobody: the commit of the destination left the client's frame at `about:blank` for good, and the `DOMContentLoaded`/`load` of
the destination was dropped with the held events (measured on Windows, `test_popups_reach_the_client_e2e.py` with no route on the context:
the popup "is at about:blank", or `wait_for_load_state("load")` times out).

The test parks the adoption thread at FOUR points of that passage - after the main frame is known (before the snapshot), as the main
frame's dispatcher is made (after the snapshot), as the events are installed (the hold is still on) and after they are installed (the hook
is in) - and delivers the destination's commit, `DOMContentLoaded` and `load` (in this order) at every placement of the three among the
four points, 20 cases, none sampled. (The hand-over itself, from the release of the hold to the hook, is one step with the lifecycle's lock
held: there is no point inside it to park at, which is the property that is wanted.) The page is then made ready and announced, and what the CLIENT was
told of the main frame (the initializer, then each `navigated` and `loadstate`, in order) must be what the lifecycle holds: the url of the
destination, the states commit/domcontentloaded/load, and the destination's url told exactly once.

Known-bad input: the code as it was -> the orders with an event between the snapshot and the hook are red.
"""
from __future__ import annotations

import itertools
import threading
import time

import pytest

from invisible_playwright._juggler import dispatcher as base
from invisible_playwright._juggler import remote
from invisible_playwright._juggler import server as server_mod
from invisible_playwright._juggler.server import LocalUtilsDispatcher

from test_every_order_of_the_events_of_a_popup_gives_one_page_event import Engine, FRAME, SESSION, TARGET, _thread, _wait

pytestmark = pytest.mark.unit

DEST = "http://dest.test/p"
POINTS = ["after_main_frame", "frame_dispatcher", "events_install", "events_installed"]
STATES = ("commit", "domcontentloaded", "load")


class PopupEngine(Engine):
    def start(self):
        self.dispatch_event("Page.navigationStarted", {"frameId": FRAME, "navigationId": "n2", "url": DEST}, SESSION)

    def commit_dest(self):
        self.dispatch_event("Page.navigationCommitted", {"frameId": FRAME, "navigationId": "n2", "url": DEST, "name": ""}, SESSION)

    def dcl(self):
        self.dispatch_event("Page.eventFired", {"frameId": FRAME, "name": "DOMContentLoaded"}, SESSION)

    def load(self):
        self.dispatch_event("Page.eventFired", {"frameId": FRAME, "name": "load"}, SESSION)


class Parking:
    """Stops the adoption thread of the popup at the four points of the passage, until the test lets it go on."""

    def __init__(self):
        self.reached = [threading.Event() for _ in POINTS]
        self.release = [threading.Event() for _ in POINTS]

    def pause(self, index):
        if threading.current_thread().name != "invpw-adopt-%s" % TARGET or self.reached[index].is_set():
            return
        self.reached[index].set()
        self.release[index].wait(10)

    def open_all(self):
        for event in self.release:
            event.set()


def _install(mp, parking, told):
    """The parking points, and a record of what the client is told of the main frame of the popup."""
    original_wait = server_mod.Lifecycle.wait_for_main_frame

    def after_main_frame(self, *args, **kwargs):
        result = original_wait(self, *args, **kwargs)
        parking.pause(0)
        return result
    mp.setattr(server_mod.Lifecycle, "wait_for_main_frame", after_main_frame)

    original_init = server_mod.FrameDispatcher.__init__

    def frame_init(self, server, page, frame_id, url="about:blank", name="", load_states=None, parent_frame=None, told_commit=None):
        if frame_id == FRAME and parent_frame is None:
            parking.pause(1)
            told["url"] = url
            told["states"] = set(load_states or ["commit"])
            told["navigated"] = []
            told["ops"] = []
        return original_init(self, server, page, frame_id, url=url, name=name, load_states=load_states, parent_frame=parent_frame, told_commit=told_commit)
    mp.setattr(server_mod.FrameDispatcher, "__init__", frame_init)

    original_install = server_mod.PageDispatcher._install_events

    def install(self):
        parking.pause(2)
        result = original_install(self)
        parking.pause(3)
        return result
    mp.setattr(server_mod.PageDispatcher, "_install_events", install)

    original_emit = base.Dispatcher.emit

    def spy(self, method, params=None):
        if getattr(self, "TYPE", "") == "Frame" and getattr(self, "frame_id", None) == FRAME and "ops" in told:
            if method == "navigated":
                told["navigated"].append(params["url"])
                told["ops"].append(("url", params["url"]))
            elif method == "loadstate":
                for key in ("add", "remove"):
                    if key in params:
                        told["ops"].append((key, params[key]))
        return original_emit(self, method, params)
    mp.setattr(base.Dispatcher, "emit", spy)


def _client_view(told):
    url, states = told["url"], set(told["states"])
    for op, value in told["ops"]:
        if op == "url":
            url = value
        elif op == "add":
            states.add(value)
        elif op == "remove":
            states.discard(value)
    return url, states


def _run(placement):
    """One case: commit_dest, dcl and load delivered at the points in `placement` (one per event, in order)."""
    parking, told, announced = Parking(), {}, []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        original_emit = base.Dispatcher.emit

        def page_spy(self, method, params=None):
            if method == "page" and getattr(self, "TYPE", "") == "BrowserContext":
                announced.append(time.monotonic())
            return original_emit(self, method, params)
        mp.setattr(base.Dispatcher, "emit", page_spy)
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        conn = PopupEngine()
        browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
        try:
            context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
            server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
            opener_target = next(t for t in conn.sessions if t != TARGET)
            del announced[:]
            conn.attach(opener_target, context.context_id)
            conn.frame()
            conn.commit()          # the initial about:blank
            conn.start()           # the navigation to the address starts
            todo = list(zip(["commit_dest", "dcl", "load"], placement))
            for i in range(len(POINTS)):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                for event, where in list(todo):
                    if where == i:
                        getattr(conn, event)()
                        todo.remove((event, where))
                parking.release[i].set()
            conn.ready()
            assert _wait(lambda: announced), "the popup was never announced"
            assert _wait(lambda: _thread() is None), "the adoption thread never ended"
            page = browser._page_targets.get(TARGET)
            frame = page.lifecycle.frames[FRAME]
            return _client_view(told), (frame.url, {s for s in frame.states if s in STATES}), told
        finally:
            parking.open_all()
            try:
                server.shutdown()
            except Exception:
                pass


def test_the_commit_and_the_load_of_the_destination_reach_the_client_whenever_they_come_while_the_page_is_handed_over():
    failures, count = [], 0
    for combo in itertools.combinations_with_replacement(range(len(POINTS)), 3):
        count += 1
        (client_url, client_states), (url, states), told = _run(combo)
        label = "commit_dest/dcl/load at %s" % [POINTS[i] for i in combo]
        problems = []
        if client_url != url:
            problems.append("the client's frame is at %r, the lifecycle's at %r" % (client_url, url))
        if {s for s in client_states if s in STATES} != states:
            problems.append("the client has the states %s, the lifecycle %s" % (sorted(client_states & set(STATES)), sorted(states)))
        told_dest = (1 if told["url"] == DEST else 0) + told["navigated"].count(DEST)
        if told_dest != 1:
            problems.append("the destination's address was told %d times" % told_dest)
        if problems:
            failures.append("%s: %s" % (label, "; ".join(problems)))
    assert count == 20
    assert not failures, "%d of %d orders lost or doubled an event, e.g.:\n  %s" % (len(failures), count, "\n  ".join(failures[:12]))


def _run_announcement(where):
    """The blank page loads, the page is made ready, and the destination's commit comes at `where`: before the ready, or WHILE the page is
    announced (the adoption thread is parked inside the announcement, after the `page` event and before the replay of the states said
    before it, and the commit is delivered by another thread, as the engine's reader would)."""
    told, announced, inside = {}, [], threading.Event()
    go = threading.Event()
    with pytest.MonkeyPatch.context() as mp:
        open_parking = Parking()
        open_parking.open_all()
        _install(mp, open_parking, told)        # the record of what the client is told; its parking points are open
        original_emit = base.Dispatcher.emit

        def page_spy(self, method, params=None):
            result = original_emit(self, method, params)
            if method == "page" and getattr(self, "TYPE", "") == "BrowserContext" and threading.current_thread().name == "invpw-adopt-%s" % TARGET:
                announced.append(True)
                inside.set()
                go.wait(10)
            return result
        mp.setattr(base.Dispatcher, "emit", page_spy)
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        conn = PopupEngine()
        browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
        try:
            context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
            server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
            opener_target = next(t for t in conn.sessions if t != TARGET)
            del announced[:]
            told.clear()
            conn.attach(opener_target, context.context_id)
            conn.frame()
            conn.commit()
            conn.start()
            assert _wait(lambda: (browser._adoptions.get(TARGET) is not None and browser._adoptions[TARGET].state == "waiting")), \
                "the adoption never waited for the ready"
            conn.dcl()                           # the blank page's load, said live and kept for the replay
            conn.load()
            deliverer = None
            if where == "before_ready":
                conn.commit_dest()
                conn.ready()
            else:
                conn.ready()
                assert inside.wait(5), "the page was never announced"
                deliverer = threading.Thread(target=conn.commit_dest, daemon=True)
                deliverer.start()
                time.sleep(0.2)                  # the commit is on its way while the announcement is not over
            go.set()
            if deliverer is not None:
                deliverer.join(5)
            assert _wait(lambda: _thread() is None), "the adoption thread never ended"
            page = browser._page_targets.get(TARGET)
            known = page.lifecycle.frames[FRAME]
            return _client_view(told), (known.url, {s for s in known.states if s in STATES}), told
        finally:
            go.set()
            try:
                server.shutdown()
            except Exception:
                pass


@pytest.mark.parametrize("where", ["before_ready", "during_announcement"])
def test_a_commit_before_or_during_the_announcement_leaves_the_client_with_the_states_of_the_destination(where):
    (client_url, client_states), (url, states), told = _run_announcement(where)
    assert client_url == url == DEST
    assert {s for s in client_states if s in STATES} == states == {"commit"}, (
        "the client has the states %s of a destination that has only committed: the load of the blank page, said again after the "
        "commit, is still on its frame" % sorted(client_states & set(STATES)))
    assert (1 if told["url"] == DEST else 0) + told["navigated"].count(DEST) == 1
