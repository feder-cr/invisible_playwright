"""An event of ANY family that the engine sends while a popup's page is handed from its buffer to its dispatcher reaches the client once, in order.

NO BROWSER HERE (the real dispatchers over a scripted Juggler connection; the parking points and the record of the previous test).

The page's own listener holds the events of the period between its construction and the end of `_install_events`, and the hand-over gave
back only the `Network.*` ones: a `console` message, a `pageError`, a `dialog`, a `crash` or any other event that came in that period was
dropped. A dialog is the worst of them: `alert()` blocks the page until the client accepts it, and a client that was never told of the dialog
cannot. The hand-over now gives back every held event, whatever its kind, in the order it arrived and once (the events the lifecycle owns - a
frame, a navigation, a load state - are also said again by the reconciliation, which is idempotent).

One event of each family (console, pageError, dialog, crash), in this order, at every placement among FOUR points of the passage (before the
snapshot of the initializer, as the main frame's dispatcher is made, as the events are installed, after): 35 cases, none sampled. Each must
be told once, in the order of arrival, a dialog must be known to the page (`_dialogs_open`) so that the client can accept it, and the
console message must be in the page's log.

Known-bad input: the hand-over as it was (only `Network.*` given back) -> every order with an event before the end of `_install_events`.
"""
from __future__ import annotations

import itertools
import threading

import pytest

from invisible_playwright._juggler import dispatcher as base
from invisible_playwright._juggler import remote
from invisible_playwright._juggler.server import LocalUtilsDispatcher

from test_a_commit_at_any_point_of_the_hand_over_reaches_the_client_once import POINTS, Parking, PopupEngine, _install
from test_every_order_of_the_events_of_a_popup_gives_one_page_event import FRAME, SESSION, TARGET, _thread, _wait

pytestmark = pytest.mark.unit

FAMILIES = ["console", "pageError", "dialog", "crash"]


def _send(conn, family):
    if family == "console":
        conn.dispatch_event("Runtime.console", {"type": "log", "args": [], "location": {"url": "", "lineNumber": 0, "columnNumber": 0}}, SESSION)
    elif family == "pageError":
        conn.dispatch_event("Page.uncaughtError", {"message": "boom", "stack": "", "location": {"url": "", "lineNumber": 0, "columnNumber": 0}}, SESSION)
    elif family == "dialog":
        conn.dispatch_event("Page.dialogOpened", {"dialogId": "d1", "type": "alert", "message": "m", "defaultValue": ""}, SESSION)
    elif family == "crash":
        conn.dispatch_event("Page.crashed", {}, SESSION)


def _run(placement, accept=False):
    parking, told, trace, dialog_guids, sent = Parking(), {}, [], [], []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        original_emit = base.Dispatcher.emit

        def spy(self, method, params=None):
            kind = getattr(self, "TYPE", "")
            if (kind == "BrowserContext" and method in ("console", "pageError", "dialog")) or (
                    kind == "Page" and method == "crash" and getattr(self, "target_id", None) == TARGET):
                trace.append(method)
                if method == "dialog":
                    dialog_guids.append(params["dialog"]["guid"])
            elif kind == "BrowserContext" and method == "page":
                trace.append("page")
            return original_emit(self, method, params)
        mp.setattr(base.Dispatcher, "emit", spy)
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        conn = PopupEngine()
        original_send = conn.send

        def recording_send(method, params=None, session=None, timeout=30):
            sent.append((method, dict(params or {})))
            return original_send(method, params, session, timeout)
        conn.send = recording_send
        browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
        try:
            context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
            server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
            opener_target = next(t for t in conn.sessions if t != TARGET)
            del trace[:]
            conn.attach(opener_target, context.context_id)
            conn.frame()
            conn.commit()
            todo = list(zip(FAMILIES, placement))
            for i in range(len(POINTS)):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                for family, where in list(todo):
                    if where == i:
                        _send(conn, family)
                        todo.remove((family, where))
                parking.release[i].set()
            conn.ready()
            assert _wait(lambda: "page" in trace), "the popup was never announced"
            assert _wait(lambda: _thread() is None), "the adoption thread never ended"
            page = browser._page_targets.get(TARGET)
            result = [t for t in trace if t != "page"], sorted(page._dialogs_open), len(page._console_log)
            if accept:
                # the client accepts the dialog, as a handler of `page.on("dialog")` does: the answer reaches the engine
                server.object(dialog_guids[0]).call("accept", {})
                return result + ([m for m in sent if m[0] == "Page.handleDialog"],)
            return result
        finally:
            parking.open_all()
            try:
                server.shutdown()
            except Exception:
                pass


def test_one_event_of_each_family_reaches_the_client_once_and_in_order_at_every_point_of_the_hand_over():
    failures, count = [], 0
    for combo in itertools.combinations_with_replacement(range(len(POINTS)), len(FAMILIES)):
        count += 1
        told, dialogs, console_entries = _run(combo)[:3]
        problems = []
        if told != FAMILIES:
            problems.append("the client was told %s" % told)
        if dialogs != ["d1"]:
            problems.append("the dialog is %s for the page: the client cannot accept it" % (dialogs,))
        if console_entries != 1:
            problems.append("%d console messages in the page's log" % console_entries)
        if problems:
            failures.append("%s: %s" % ([POINTS[i] for i in combo], "; ".join(problems)))
    assert count == 35
    assert not failures, "%d of %d orders lost or doubled an event, e.g.:\n  %s" % (len(failures), count, "\n  ".join(failures[:12]))


@pytest.mark.parametrize("point", [0, 1, 2])
def test_a_dialog_opened_during_the_hand_over_can_be_accepted_by_the_client(point):
    """The dialog that opened before the page was handed over is announced, and accepting it sends the engine the answer that unblocks the page."""
    told, dialogs, _, answers = _run((point, point, point, point), accept=True)
    assert "dialog" in told
    assert [a[1].get("dialogId") for a in answers] == ["d1"], "the engine was not answered: %s" % answers
    assert answers[0][1].get("accept") is True


def test_an_event_that_does_not_fit_in_the_hold_is_not_lost_in_silence(monkeypatch):
    """The hold has a cap (it bounds the memory of a page that never finishes building). The events beyond it are lost, and the loss is said once,
    with its count, when the hold ends."""
    import warnings

    from invisible_playwright._juggler import server as server_mod
    monkeypatch.setattr(server_mod.PageDispatcher, "HELD_CAP", 8)      # the birth of the page already holds a few events
    parking, told, trace = Parking(), {}, []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        original_emit = base.Dispatcher.emit

        def spy(self, method, params=None):
            if getattr(self, "TYPE", "") == "BrowserContext" and method in ("console", "page"):
                trace.append(method)
            return original_emit(self, method, params)
        mp.setattr(base.Dispatcher, "emit", spy)
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        conn = PopupEngine()
        browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
        try:
            context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
            server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
            opener_target = next(t for t in conn.sessions if t != TARGET)
            del trace[:]
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                conn.attach(opener_target, context.context_id)
                conn.frame()
                conn.commit()
                for i in range(len(POINTS)):
                    assert parking.reached[i].wait(5)
                    if i == 0:
                        for _ in range(20):
                            _send(conn, "console")      # the hold takes what is left of its 8, loses the rest
                    parking.release[i].set()
                conn.ready()
                assert _wait(lambda: "page" in trace)
                assert _wait(lambda: _thread() is None)
            lost = [str(w.message) for w in caught if "were lost while it was handed over" in str(w.message)]
            kept = trace.count("console")
            assert 0 < kept < 20, "the hold keeps %d of 20" % kept
            assert len(lost) == 1, "the loss was said %d times: %s" % (len(lost), lost)
            assert "%d event(s)" % (20 - kept) in lost[0] and "more than 8" in lost[0], lost[0]
        finally:
            parking.open_all()
            try:
                server.shutdown()
            except Exception:
                pass


class ChooserEngine(PopupEngine):
    """The engine as far as a file chooser needs it: the injected script is built, the node is adopted into the utility world, it is asked
    whether it takes several files."""

    def frame(self):
        self.dispatch_event("Page.frameAttached", {"frameId": FRAME, "parentFrameId": None}, SESSION)
        for world in ("", "__ctx_aux__"):          # the page's own world and the utility one, under the names the client looks for
            self.dispatch_event("Runtime.executionContextCreated", {
                "executionContextId": "e%s" % world, "auxData": {"frameId": FRAME, "name": world}}, SESSION)

    def send(self, method, params=None, session=None, timeout=30):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "injected-1"}}
        if method == "Page.adoptNode":
            return {"remoteObject": {"objectId": "adopted-1"}}
        if method == "Runtime.callFunction":
            return {"result": {"value": True}}
        return super().send(method, params, session, timeout)


@pytest.mark.parametrize("point", range(len(POINTS)), ids=POINTS)
def test_a_file_chooser_opened_during_the_hand_over_reaches_the_client_and_can_be_answered(point):
    """The chooser is told once, with the handle of its input element (the client answers a chooser through that handle) and its `isMultiple`."""
    parking, told, chooser = Parking(), {}, []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        original_emit = base.Dispatcher.emit

        def spy(self, method, params=None):
            if getattr(self, "TYPE", "") == "Page" and method == "fileChooser" and getattr(self, "target_id", None) == TARGET:
                chooser.append(params)
            return original_emit(self, method, params)
        mp.setattr(base.Dispatcher, "emit", spy)
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        conn = ChooserEngine()
        browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
        try:
            context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
            server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
            opener_target = next(t for t in conn.sessions if t != TARGET)
            conn.attach(opener_target, context.context_id)
            conn.frame()
            conn.commit()
            for i in range(len(POINTS)):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                if i == point:
                    conn.dispatch_event("Page.fileChooserOpened", {"element": {"objectId": "input-1"}, "executionContextId": "e"}, SESSION)
                parking.release[i].set()
            conn.ready()
            assert _wait(lambda: chooser), "the file chooser opened during the hand-over never reached the client (%s)" % (conn.handler_errors,)
            assert _wait(lambda: _thread() is None)
            assert len(chooser) == 1 and chooser[0]["isMultiple"] is True
            assert server.object(chooser[0]["element"]["guid"]) is not None, "the client has no handle to answer the chooser through"
        finally:
            parking.open_all()
            try:
                server.shutdown()
            except Exception:
                pass
