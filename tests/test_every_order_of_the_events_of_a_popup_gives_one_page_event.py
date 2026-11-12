"""Every order in which the engine's events about a popup can reach the adoption gives the client exactly one `page` event.

NO BROWSER HERE: the real dispatchers over a scripted Juggler connection, and the adoption thread stopped, one step at a time, at SIX
points of its own (before it records itself as building, before it sends the init scripts, before it waits for the main frame, before it
records itself as waiting, before it waits for the ready, before it announces) while the engine's events are delivered between the steps.

The events of a target that is not a page a caller asked for, as the engine sends them: ATTACH (always first), then FRAME (the main frame
and its contexts), COMMIT (the initial about:blank), READY, and DETACH (the tab is closed: a download's empty tab, a popup the site closed)
which can come after ANY prefix of the others and after which the engine answers "cannot find session" to every command about the
session. For each of the prefixes that can occur, EVERY placement of its events among the eight moments of the adoption (parked at
each of the six points, while it waits for the ready, after it) is run: 650 cases, none sampled.

The invariant (`_Adoption`): such a target gives the client exactly ONE `page` event, at its ready or at its disappearance, and when
it disappeared its close right after, never before; no warning of a failed adoption; nothing left in the registry; the adoption thread
ends. A target that gets neither a ready nor a detach gives no event and its wait ends with its context.

Why it exists: the same race was found four times by a run on a loaded machine, each time one step further along (a detach before the
ready, during the build, between the build and the wait, and a command answered "cannot find session"); a fifth was this test's first
find (a detach while the adoption waits for the main frame of a tab the engine never announced).
"""
from __future__ import annotations

import itertools
import threading
import time
import warnings

import pytest

from invisible_core.juggler.connection import ProtocolError
from invisible_playwright._juggler import dispatcher as base
from invisible_playwright._juggler import remote
from invisible_playwright._juggler import server as server_mod
from invisible_playwright._juggler.server import LocalUtilsDispatcher

from test_a_closed_context_leaves_nothing_in_the_registry import ScriptedJuggler

pytestmark = pytest.mark.unit

TARGET = "x1"
SESSION = "xs1"
FRAME = "fx1"
#: where the adoption thread can be stopped, in the order it reaches them
GATES = ["building", "init_scripts", "main_frame", "waiting", "ready_wait", "announce"]
#: the moments an event can be delivered: parked at each gate, while it waits for the ready, parked at `announce`, after it is over
MOMENTS = ["g0", "g1", "g2", "g3", "g4", "during_wait", "g5", "after"]


class Engine(ScriptedJuggler):
    """The scripted engine, which answers 'cannot find session' to a command about a session whose target it took away."""

    def __init__(self):
        super().__init__()
        self.gone_sessions = set()

    def send(self, method, params=None, session=None, timeout=30):
        if session in self.gone_sessions:
            raise ProtocolError('%s: ERROR: cannot find session with id "%s"' % (method, session))
        return super().send(method, params, session, timeout)

    def attach(self, opener_target, context_id):
        self.sessions[TARGET] = SESSION
        self.dispatch_event("Browser.attachedToTarget", {
            "sessionId": SESSION,
            "targetInfo": {"targetId": TARGET, "type": "page", "openerId": opener_target, "browserContextId": context_id}}, None)

    def frame(self):
        self.dispatch_event("Page.frameAttached", {"frameId": FRAME, "parentFrameId": None}, SESSION)
        for world in ("", "utility"):
            self.dispatch_event("Runtime.executionContextCreated", {
                "executionContextId": "e%s" % world, "auxData": {"frameId": FRAME, "name": world}}, SESSION)

    def commit(self):
        self.dispatch_event("Page.navigationStarted", {"frameId": FRAME, "navigationId": "n1", "url": "about:blank"}, SESSION)
        self.dispatch_event("Page.navigationCommitted", {"frameId": FRAME, "navigationId": "n1", "url": "about:blank", "name": ""}, SESSION)

    def ready(self):
        self.dispatch_event("Page.ready", {}, SESSION)

    def detach(self):
        self.gone_sessions.add(SESSION)
        self.dispatch_event("Browser.detachedFromTarget", {"targetId": TARGET, "sessionId": SESSION}, None)


class Gates:
    """Stops the adoption thread of the popup at each of the six points, until the test lets it pass."""

    def __init__(self):
        self.reached = [threading.Event() for _ in GATES]
        self.release = [threading.Event() for _ in GATES]
        self.passed = set()

    def pause(self, index):
        if threading.current_thread().name != "invpw-adopt-%s" % TARGET or index in self.passed:
            return
        self.passed.add(index)
        self.reached[index].set()
        self.release[index].wait(10)

    def open_all(self):
        for event in self.release:
            event.set()


def _thread():
    for t in threading.enumerate():
        if t.name == "invpw-adopt-%s" % TARGET:
            return t
    return None


def _wait(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.002)
    return condition()


def _install_gates(monkeypatch, gates):
    def gated(index, owner, name):
        original = getattr(owner, name)

        def wrapper(*args, **kwargs):
            gates.pause(index)
            return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, wrapper)

    gated(0, server_mod.BrowserDispatcher, "_enter_building")
    gated(1, server_mod.InjectedScript, "install")
    gated(2, server_mod.Lifecycle, "wait_for_main_frame")
    gated(3, server_mod.BrowserDispatcher, "_enter_waiting")
    gated(4, server_mod.PageDispatcher, "wait_until_ready")
    gated(5, server_mod.BrowserDispatcher, "_enter_announced")


def _run(events, placement):
    """One case: `events` (a prefix of frame, commit, ready, detach) delivered at the moments in `placement` (one per event)."""
    gates = Gates()
    trace = []
    original_emit = base.Dispatcher.emit

    def spy(self, method, params=None):
        if method == "page" and getattr(self, "TYPE", "") == "BrowserContext":
            trace.append("page")
        elif method == "close" and getattr(self, "TYPE", "") == "Page" and getattr(self, "target_id", None) == TARGET:
            trace.append("close")
        return original_emit(self, method, params)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(base.Dispatcher, "emit", spy)
        _install_gates(mp, gates)
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        conn = Engine()
        browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
        try:
            context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
            # the opener, built with no gate (the gates only stop the popup's own thread)
            server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
            opener_target = next(t for t in conn.sessions if t != TARGET)
            del trace[:]          # the opener's own page event is not the popup's

            todo = list(zip(events, placement))
            delivered = set()

            def deliver(moment):
                for event, where in list(todo):
                    if where == moment:
                        getattr(conn, event)()
                        delivered.add(event)
                        todo.remove((event, where))

            def deliver_next():
                """The adoption is blocked in a wait that only an event of the engine can end: the next one arrives now (that wait IS the
                moment it was placed at)."""
                if todo:
                    event, _where = todo.pop(0)
                    getattr(conn, event)()
                    delivered.add(event)
                    return True
                return False

            def advance_until(condition, what, seconds=3.0):
                """Wait for `condition`; while nothing moves for 0.1 s, deliver the next pending event."""
                end = time.monotonic() + seconds
                idle_since = time.monotonic()
                while time.monotonic() < end:
                    if condition():
                        return True
                    if time.monotonic() - idle_since > 0.1:
                        if deliver_next():
                            idle_since = time.monotonic()
                        else:
                            idle_since = time.monotonic() + 1e9          # nothing left to deliver: just wait
                    time.sleep(0.002)
                assert condition(), what
                return True

            def parked(i):
                return gates.reached[i].is_set() or _thread() is None

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                conn.attach(opener_target, context.context_id)
                assert gates.reached[0].wait(3), "the adoption thread never started"
                for i in range(5):                       # parked at gate i
                    if i > 0:
                        advance_until(lambda: parked(i) or any(gates.reached[j].is_set() for j in range(i + 1, 6)),
                                      "the adoption thread never reached %s" % GATES[i])
                    deliver("g%d" % i)
                    gates.release[i].set()
                # gate 4 is released: the thread is in (or about to be in) its wait for the ready
                deliver("during_wait")
                final_unblocked = "ready" in delivered or "detach" in delivered or any(e in ("ready", "detach") for e, _ in todo)
                if final_unblocked:
                    advance_until(lambda: gates.reached[5].is_set() or _thread() is None, "the wait for the ready never ended")
                    deliver("g5")                        # parked at the announcement (if it got there)
                    gates.release[5].set()
                    advance_until(lambda: _thread() is None, "the adoption thread never ended")
                    deliver("after")
                    while deliver_next():                # anything left arrives after it all
                        pass
                else:
                    while deliver_next():
                        pass
                    time.sleep(0.05)
                    assert _thread() is not None or not trace, "a target with neither ready nor detach was announced"
                time.sleep(0.01)
            bad = [str(w.message) for w in caught if "adoption" in str(w.message)]
            return trace, bad, dict(server_state=len(browser._adoptions)), (browser, context, gates, conn)
        finally:
            gates.open_all()


def _check(events, placement):
    trace, bad, state, (browser, context, gates, conn) = _run(events, placement)
    try:
        label = "events %s at %s" % (events, placement)
        problems = []
        disappears = "detach" in events
        if bad:
            problems.append("a warning of a failed adoption: %s" % bad)
        if "ready" in events or disappears:
            if trace.count("page") != 1:
                problems.append("%d page events" % trace.count("page"))
            if disappears:
                if trace.count("close") != 1:
                    problems.append("%d close events" % trace.count("close"))
                elif "page" in trace and trace.index("page") > trace.index("close"):
                    problems.append("the close came before the page")
            elif trace.count("close"):
                problems.append("a close for a page that did not disappear")
            if state["server_state"] != 0:
                problems.append("%d adoptions left in the registry" % state["server_state"])
        else:
            if trace:
                problems.append("events %r for a target with neither ready nor detach" % (trace,))
            # its context closes: the wait ends, still nothing announced
            browser.server.handle({"guid": context.guid, "method": "close", "params": {}})
            if not _wait(lambda: _thread() is None):
                problems.append("the wait did not end with its context")
            if trace.count("page"):
                problems.append("announced after its context closed")
        return label, problems
    finally:
        gates.open_all()
        try:
            browser.server.shutdown()
        except Exception:
            pass


def _placements(n, moments):
    """Every way of putting n events, in order, on the given moments (the same moment allowed, the order kept)."""
    return itertools.combinations_with_replacement(range(len(moments)), n)


SEQUENCES = {
    "frame_commit_ready": (["frame", "commit", "ready"], MOMENTS),
    "frame_commit_ready_detach": (["frame", "commit", "ready", "detach"], MOMENTS),
    "frame_commit_detach": (["frame", "commit", "detach"], MOMENTS),
    "frame_detach": (["frame", "detach"], MOMENTS),
    "detach": (["detach"], MOMENTS),
    # neither ready nor detach: nothing is announced; the events can only arrive while the adoption is alive
    "frame_commit": (["frame", "commit"], MOMENTS[:6]),
}


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_every_order_of_the_events_of_a_popup_gives_one_page_event(name):
    events, moments = SEQUENCES[name]
    failures = []
    count = 0
    for combo in _placements(len(events), moments):
        placement = [moments[i] for i in combo]
        count += 1
        try:
            label, problems = _check(events, placement)
        except AssertionError as e:
            label, problems = "events %s at %s" % (events, placement), [str(e)]
        if problems:
            failures.append("%s: %s" % (label, "; ".join(problems)))
    assert count > 0
    assert not failures, "%d of %d orders broke the invariant, e.g.:\n  %s" % (len(failures), count, "\n  ".join(failures[:12]))
