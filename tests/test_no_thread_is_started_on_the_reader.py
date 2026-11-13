"""No thread is started on the engine connection's reader thread.

`Thread.start` does not return until the new thread is running. On the reader that wait is the reader's own delay: measured on Windows with
four browsers at once, it was 75% of the reader's lag, and it made `networkidle` miss 7 times in 400 (none in 400 once the start no longer
waited). Two listeners of this package started a thread from the reader: `Browser.attachedToTarget` (one thread per adoption) and
`Page.fileChooserOpened` (one thread to announce the chooser). Both now hand the thread to the browser's ONE long-lived starter
(`_ThreadStarter`, made with the browser), which starts it.

NO BROWSER HERE: the real dispatchers over a scripted Juggler connection, and every event of the engine delivered FROM A THREAD OF ITS OWN
(the reader's stand-in), the way the connection delivers them. `Thread.start` is replaced by a recorder of the thread that called it.

`Page.eventFired(load)` goes through the core's lifecycle, which started a `threading.Timer` for the silence of `networkidle` until the core
made one thread of deadlines for the connection (`DeadlineClock`): that case is skipped with a core that does not have it.

Known-bad input: the thread started in `_spawn_adoption` / `_route_juggler_event` -> two starts from the reader (the adoption and the
chooser), named in the failure.
"""
from __future__ import annotations

import queue
import threading

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_playwright._juggler import remote
from invisible_playwright._juggler.server import LocalUtilsDispatcher

from test_every_order_of_the_events_of_a_popup_gives_one_page_event import FRAME, SESSION, TARGET, _wait
from test_no_event_of_any_family_is_lost_by_the_hand_over import ChooserEngine

pytestmark = pytest.mark.unit

READER = "the-engine-reader"


class _Reader:
    """A thread that delivers the events it is handed, one at a time and in order, as the connection's reader does."""

    def __init__(self):
        self._work: "queue.Queue" = queue.Queue()
        self.thread = threading.Thread(target=self._run, name=READER, daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            job = self._work.get()
            if job is None:
                return
            fn, done = job
            try:
                fn()
            finally:
                done.set()

    def deliver(self, fn):
        done = threading.Event()
        self._work.put((fn, done))
        assert done.wait(10), "the reader is stuck inside a listener"

    def stop(self):
        self._work.put(None)


@pytest.fixture
def starts(monkeypatch):
    """Every `Thread.start` from now on, as (the name of the thread that called it, the name of the thread started)."""
    calls = []
    original = threading.Thread.start

    def recording(self):
        calls.append((threading.current_thread().name, self.name))
        return original(self)
    monkeypatch.setattr(threading.Thread, "start", recording)
    return calls


def _from_reader(starts):
    return [c for c in starts if c[0] == READER]


def _a_popup_is_announced(conn, reader, browser, context_id, opener_target):
    reader.deliver(lambda: conn.attach(opener_target, context_id))
    reader.deliver(conn.frame)
    reader.deliver(conn.commit)
    reader.deliver(conn.ready)
    assert _wait(lambda: browser._page_targets.get(TARGET) is not None, 5), "the popup was never announced (%s)" % (conn.handler_errors,)


def _world(starts):
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    conn = ChooserEngine()
    browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
    context = server.object(server.handle({"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(t for t in conn.sessions if t != TARGET)
    return server, conn, browser, context, opener_target


def test_an_attached_target_and_a_file_chooser_start_no_thread_on_the_reader(starts):
    server, conn, browser, context, opener_target = _world(starts)
    reader = _Reader()
    chooser = []
    try:
        del starts[:]
        _a_popup_is_announced(conn, reader, browser, context.context_id, opener_target)
        assert [c[1] for c in starts if c[1].startswith("invpw-adopt-")], "the adoption never started (%s)" % (starts,)
        page = browser._page_targets[TARGET]
        original_emit = type(page).emit

        def spy(self, method, params=None):
            if method == "fileChooser":
                chooser.append(params)
            return original_emit(self, method, params)
        type(page).emit = spy
        try:
            reader.deliver(lambda: conn.dispatch_event(
                "Page.fileChooserOpened", {"element": {"objectId": "input-1"}, "executionContextId": "e"}, SESSION))
            assert _wait(lambda: chooser, 5), "the chooser never reached the client (%s)" % (conn.handler_errors,)
        finally:
            type(page).emit = original_emit
        assert _from_reader(starts) == [], "the reader started %s" % (_from_reader(starts),)
        assert conn.handler_errors == []
    finally:
        reader.stop()
        server.shutdown()


@pytest.mark.skipif(not hasattr(EventListeners, "deadlines"), reason="the installed core still starts a Timer for the silence of networkidle")
def test_a_load_delivered_on_the_reader_starts_no_thread(starts):
    server, conn, browser, context, opener_target = _world(starts)
    reader = _Reader()
    try:
        _a_popup_is_announced(conn, reader, browser, context.context_id, opener_target)
        del starts[:]
        reader.deliver(lambda: conn.dispatch_event("Page.eventFired", {"frameId": FRAME, "name": "load"}, SESSION))
        assert _from_reader(starts) == [], "the reader started %s" % (_from_reader(starts),)
        assert conn.handler_errors == []
    finally:
        reader.stop()
        server.shutdown()


def test_the_starter_of_a_browser_ends_with_its_connection(starts):
    server, conn, browser, context, opener_target = _world(starts)
    try:
        starter = browser.thread_starter
        starter.start(target=lambda: None, name="invpw-test-started")
        assert _wait(lambda: ("invpw-thread-starter", "invpw-test-started") in starts, 3)
        conn.announce_closed()
        starter._thread.join(5)
        assert not starter._thread.is_alive(), "the starter outlived the connection"
        starter.start(target=lambda: None, name="invpw-test-after-the-end")      # after the end: nothing is started, nothing raises
        assert all(c[1] != "invpw-test-after-the-end" for c in starts)
    finally:
        server.shutdown()
