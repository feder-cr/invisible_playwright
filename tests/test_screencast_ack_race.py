"""The screencast's frame / acknowledgement path, with no browser: a fake engine
that keeps the engine's own flow control, a reader thread shaped like
`invisible_core.juggler.connection.Connection`, and the shipped `PageDispatcher`
between them.

⛔ WHY THIS FILE EXISTS. The engine lets ONE frame be in flight until the
wrapper acknowledges it (`kMaxFramesInFlight = 1` in nsScreencastService.cpp,
and the heartbeat only repeats a frame when nothing is in flight). So a frame
the wrapper receives and does not acknowledge ends the stream for good: no
more frames, no error, `page.screencast.start(on_frame=...)` returned and
nothing ever arrives. That is an intermittency by construction, and it has
exactly one trigger on this side: a frame handled while `_screencast_id` is
still None.

`op_screencast_start` set the id AFTER `send("Page.startScreencast")` returned.
The reader thread wakes the caller on the reply and carries on reading, so a
frame sitting behind the reply in the pipe is handled by the reader while the
caller has not yet run its next line: the handler saw no id, dropped the frame
without an ack, and the engine waited for that ack forever.

The tests below drive that ordering on purpose (a frame right behind the
reply is the engine's WORST timing, and the real engine produces it whenever
the capture is already done by the time the reply is read) and then run many
start / stop cycles in several threads with random delays between the reply
and the frame, and random stops in the middle of a frame.

Known-bad, run before this file was trusted: take the ack and the id out of
the lock in `PageDispatcher._screencast_frame`, or set `_screencast_id` after
`send` returns as it was -> every test that says "stall" goes red.
"""
from __future__ import annotations

import queue
import random
import threading
import time
from types import SimpleNamespace

import pytest

from invisible_core.juggler.connection import EventListeners


class FakeEngine(EventListeners):
    """The engine's screencast and the Connection around it, in two threads.

    The engine side keeps the real rules:
      * one session per page, its id fresh on every start;
      * a frame is produced only while NOTHING is in flight, and counts as in
        flight until an ack that names the CURRENT session arrives;
      * after a stop no frame is produced for the old session;
      * the reply to a command is written before any frame that follows it.
    The connection side keeps the real shape: `send` waits on an Event that the
    READER thread sets, and the reader goes straight on to the next message.
    """

    def __init__(self, frame_delay=0.0):
        EventListeners.__init__(self)
        self.page = None
        self.frame_delay = frame_delay
        self.commands = queue.Queue()
        self.output = queue.Queue()
        self._pending = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self.session_number = 0
        self.active = None
        self.in_flight = 0
        self.not_before = 0.0
        self.frames_sent = 0
        self.acks_accepted = 0
        self.acks_ignored = 0
        self.errors = []
        self._closing = False
        self._engine = threading.Thread(target=self._engine_loop, daemon=True)
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._engine.start()
        self._reader.start()

    # -- Connection side ------------------------------------------------------
    def send(self, method, params=None, session=None, timeout=30):
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            ready = threading.Event()
            box = []
            self._pending[msg_id] = (ready, box)
        self.commands.put((msg_id, method, params or {}))
        if not ready.wait(10):
            raise TimeoutError("%s: no reply" % method)
        return box[0]

    def post(self, method, params=None, session=None):
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
        self.commands.put((msg_id, method, params or {}))

    def _reader_loop(self):
        while True:
            item = self.output.get()
            if item is None:
                return
            kind, a, b = item
            try:
                if kind == "reply":
                    with self._lock:
                        entry = self._pending.pop(a, None)
                    if entry is not None:
                        entry[1].append(b)
                        entry[0].set()
                else:
                    self.page._on_juggler_event(a, b)
            except Exception as failure:  # the real reader records and carries on
                self.errors.append(repr(failure))

    # -- engine side ----------------------------------------------------------
    def _engine_loop(self):
        while not self._closing:
            try:
                msg_id, method, params = self.commands.get(timeout=0.0003)
            except queue.Empty:
                self._maybe_frame()
                continue
            if method == "Page.startScreencast":
                if self.active is not None:
                    self.output.put(("reply", msg_id, {"error": "already running"}))
                    continue
                self.session_number += 1
                self.active = "cast-%d" % self.session_number
                self.in_flight = 0
                self.not_before = time.monotonic() + self.frame_delay
                self.output.put(("reply", msg_id, {"screencastId": self.active}))
            elif method == "Page.stopScreencast":
                self.active = None
                self.output.put(("reply", msg_id, {}))
            elif method == "Page.screencastFrameAck":
                if self.active is not None and params.get("screencastId") == self.active:
                    if self.in_flight:
                        self.in_flight -= 1
                    self.acks_accepted += 1
                    self.not_before = 0.0
                else:
                    self.acks_ignored += 1
            self._maybe_frame()

    def _maybe_frame(self):
        if (self.active is not None and self.in_flight < 1
                and time.monotonic() >= self.not_before):
            self.in_flight += 1
            self.frames_sent += 1
            self.output.put(("event", "Page.screencastFrame", {
                "data": "/9j/ZmFrZQ==", "deviceWidth": 640, "deviceHeight": 480,
                "timestamp": time.time()}))

    def close(self):
        self._closing = True
        self.output.put(None)
        self._engine.join(2)
        self._reader.join(2)


def _page(engine):
    from invisible_playwright._juggler.server import JugglerServer, PageDispatcher
    server = JugglerServer()
    up = []
    server.attach(type("R", (), {"emit_message": lambda self, m: up.append(m)})())
    page = object.__new__(PageDispatcher)
    page.server = server
    page.guid = "page@1"
    page.session = "session-1"
    page.disposed = False
    page._screencast_id = None
    page._screencast_lock = threading.Lock()
    page._screencast_starting = False
    page._screencast_early = None
    page.context = SimpleNamespace(browser=SimpleNamespace(conn=engine))
    engine.page = page
    return page, up


def _frames(up):
    return [m for m in up if m.get("method") == "screencastFrame"]


def _wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.0005)
    return predicate()


START = {"sendFrames": True, "record": False}


def test_a_frame_right_behind_the_start_reply_is_acknowledged_not_dropped():
    """The ordering, written out: the reply to `Page.startScreencast` and then,
    before the caller has run its next line, the first frame. Nothing is
    random here: the frame is delivered from inside `send`, after the reply
    exists and before `send` returns, which is the interleaving the reader
    thread produces when the caller is slow to wake.

    On the old code the frame found no id, was dropped, and was not acked: the
    engine's single in-flight slot stayed taken and no frame ever came again.
    """
    posted = []

    class Conn(EventListeners):
        page = None

        def send(self, method, params=None, session=None, timeout=30):
            if method == "Page.startScreencast":
                # The reply is decided; the reader hands the frame behind it
                # to the page before the caller's thread runs again.
                self.page._on_juggler_event("Page.screencastFrame", {
                    "data": "/9j/ZmFrZQ==", "deviceWidth": 1270,
                    "deviceHeight": 922, "timestamp": 1.5})
                return {"screencastId": "cast-1"}
            return {}

        def post(self, method, params=None, session=None):
            posted.append((method, params, session))

    conn = Conn()
    from invisible_playwright._juggler.server import JugglerServer, PageDispatcher
    server = JugglerServer()
    up = []
    server.attach(type("R", (), {"emit_message": lambda self, m: up.append(m)})())
    page = object.__new__(PageDispatcher)
    page.server = server
    page.guid = "page@1"
    page.session = "session-1"
    page.disposed = False
    page._screencast_id = None
    page._screencast_lock = threading.Lock()
    page._screencast_starting = False
    page._screencast_early = None
    page.context = SimpleNamespace(browser=SimpleNamespace(conn=conn))
    conn.page = page

    page.op_screencast_start(START)

    assert posted == [("Page.screencastFrameAck", {"screencastId": "cast-1"},
                       "session-1")], (
        "the first frame arrived while the start was being answered and was "
        "never acknowledged: the engine keeps one frame in flight until it is, "
        "so this stream is over before it began (%r)" % (posted,))
    frames = _frames(up)
    assert len(frames) == 1 and frames[0]["params"]["viewportWidth"] == 1270


def test_start_with_the_frame_behind_the_reply_never_stalls_200_times():
    """Many cycles, several threads, random gaps between reply and frame.

    A stall is "fewer than three frames in a quarter of a second after start
    returned" - with the engine's own flow control, a single lost ack is
    enough, and nothing wakes the stream again.
    """
    cycles_per_thread, threads = 50, 4  # 200 cycles
    stalls = []
    errors = []
    lock = threading.Lock()

    def worker(seed):
        rng = random.Random(seed)
        engine = FakeEngine()
        page, up = _page(engine)
        try:
            for cycle in range(cycles_per_thread):
                engine.frame_delay = rng.choice((0.0, 0.0, 0.0002, 0.001, 0.004))
                before = len(_frames(up))
                page.op_screencast_start(START)
                got = _wait_for(lambda: len(_frames(up)) - before >= 3, 0.25)
                if not got:
                    with lock:
                        stalls.append((seed, cycle, engine.frame_delay,
                                       len(_frames(up)) - before))
                page.op_screencast_stop({})
            with lock:
                errors.extend(engine.errors)
        finally:
            engine.close()

    pool = [threading.Thread(target=worker, args=(1000 + n,)) for n in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(120)
    assert not any(t.is_alive() for t in pool)
    assert not errors, errors
    assert not stalls, (
        "%d of %d start cycles delivered fewer than 3 frames in 0.25 s (a lost "
        "ack ends the stream): %r" % (len(stalls), cycles_per_thread * threads,
                                      stalls[:5]))


def test_stop_in_the_middle_of_a_frame_leaves_nothing_behind():
    """Stop at a random moment relative to the frame: the id is gone when stop
    returns, no frame reaches the client after it, no ack goes out for a
    screencast nobody is streaming, and nothing raised on the reader thread."""
    rng = random.Random(7)
    engine = FakeEngine()
    page, up = _page(engine)
    try:
        for cycle in range(200):
            engine.frame_delay = rng.choice((0.0, 0.0002, 0.001))
            page.op_screencast_start(START)
            time.sleep(rng.choice((0.0, 0.0001, 0.0005, 0.002)))
            page.op_screencast_stop({})
            assert page._screencast_id is None
            # Whatever was already behind the stop in the pipe is dropped by
            # the page; give the reader time to hand it over, then count.
            time.sleep(0.001)
            settled = len(_frames(up))
            posted_after = engine.acks_accepted + engine.acks_ignored
            time.sleep(0.001)
            assert len(_frames(up)) == settled, "a frame reached the client after stop"
            assert engine.acks_accepted + engine.acks_ignored == posted_after
        assert not engine.errors, engine.errors
        # Stopping twice stays a no-op.
        assert page.op_screencast_stop({}) is None
    finally:
        engine.close()


def test_a_failed_start_does_not_leave_the_page_waiting_for_a_start():
    """If the engine refuses the start, the page is back to "no screencast":
    the next start is accepted and a frame meanwhile is not held for ever."""
    class Refusing(EventListeners):
        def __init__(self):
            EventListeners.__init__(self)
            self.refuse = True
            self.posted = []

        def send(self, method, params=None, session=None, timeout=30):
            if method == "Page.startScreencast":
                if self.refuse:
                    raise RuntimeError("invalid screencast size")
                return {"screencastId": "cast-2"}
            return {}

        def post(self, method, params=None, session=None):
            self.posted.append(method)

    conn = Refusing()
    page, up = _page(conn)
    with pytest.raises(RuntimeError):
        page.op_screencast_start(START)
    assert page._screencast_id is None and not page._screencast_starting
    page._on_juggler_event("Page.screencastFrame", {"data": "x"})
    assert not _frames(up) and not conn.posted
    conn.refuse = False
    page.op_screencast_start(START)
    assert page._screencast_id == "cast-2"


def test_closing_a_page_with_a_running_screencast_stops_it_once():
    """`_detach_listeners` asks the engine to stop a running screencast, and a
    frame that comes after is neither delivered nor acknowledged."""
    engine = FakeEngine()
    page, up = _page(engine)
    try:
        page.lifecycle = SimpleNamespace(detach=lambda: None)
        page.injected = SimpleNamespace(detach=lambda: None)
        page.op_screencast_start(START)
        assert _wait_for(lambda: len(_frames(up)) >= 1, 1.0)
        page._detach_listeners()
        assert page._screencast_id is None
        assert _wait_for(lambda: engine.active is None, 1.0), "the engine was not asked to stop"
        settled = len(_frames(up))
        time.sleep(0.01)
        assert len(_frames(up)) == settled
        assert not engine.errors, engine.errors
    finally:
        engine.close()
