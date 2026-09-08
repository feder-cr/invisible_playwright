"""`page.screencast.start(on_frame=...)` streams JPEG frames of the WINDOW.

The engine's screencast came back with firefox-28, rebuilt on Firefox's own
window capture and with one flag of ours, `fullWindow`, that keeps the tab
strip, the address bar and the chrome-side pointer in every frame. This file
pins the server half: what the in-process Juggler server sends the engine,
what it hands the vendored client, and what it refuses.

The unit half builds a `PageDispatcher` without a browser: `conn` is a
property over `context.browser.conn`, so a fake connection that records
`send` and `post` calls is enough, and `emit` goes to the server's receiver.
Known-bad inputs, each run before this file was trusted:

* `fullWindow` dropped from the engine call -> the first test goes red;
* the ack sent with `send` instead of `post` -> the frame test goes red, and
  in a real browser it would be a deadlock on the reader thread;
* `screencastStart` put back into `perimeter.OUTSIDE` -> the perimeter test
  goes red and, one level up, the client would be refused with a reason
  written for a driver that no longer exists.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
import time
from types import SimpleNamespace

import pytest

from invisible_playwright._juggler.connection import EventListeners

PAGE = b"""<!doctype html><html><head><title>screencast</title>
<style>html,body{margin:0;background:#FF00FF;height:100%}</style>
</head><body></body></html>"""


class RecordingConnection(EventListeners):
    """Records every command, and answers `Page.startScreencast` with an id."""

    def __init__(self):
        EventListeners.__init__(self)
        self.sent = []
        self.posted = []

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params or {}, session))
        if method == "Page.startScreencast":
            return {"screencastId": "cast-1"}
        return {}

    def post(self, method, params=None, session=None):
        self.posted.append((method, params or {}, session))


def _bare_page(conn):
    """A PageDispatcher with only what the screencast ops touch."""
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
    page.context = SimpleNamespace(browser=SimpleNamespace(conn=conn))
    return page, up


def test_start_asks_the_engine_for_the_whole_window():
    """The one line that makes this OUR screencast and not upstream's:
    `fullWindow: True` on the engine call, so the pointer is in the picture."""
    conn = RecordingConnection()
    page, _ = _bare_page(conn)
    result = page.op_screencast_start({"sendFrames": True, "record": False,
                                       "size": {"width": 640, "height": 480},
                                       "quality": 70})
    assert result == {}, "the client reads an optional artifact; there is none"
    method, params, session = conn.sent[-1]
    assert method == "Page.startScreencast"
    assert session == "session-1", "the command must land on THIS page"
    assert params["fullWindow"] is True
    assert (params["width"], params["height"], params["quality"]) == (640, 480, 70)
    assert page._screencast_id == "cast-1"


def test_a_frame_is_handed_up_and_acknowledged_without_waiting():
    """The engine keeps ONE frame in flight until it is acknowledged, and the
    handler runs on the reader thread, so the ack has to be a `post`: a
    `send` there would wait for a reply only that same thread can deliver."""
    conn = RecordingConnection()
    page, up = _bare_page(conn)
    page.op_screencast_start({"sendFrames": True, "record": False})
    page._on_juggler_event("Page.screencastFrame", {
        "data": "/9j/ZmFrZQ==", "deviceWidth": 1270, "deviceHeight": 922,
        "timestamp": 12.5})
    frames = [m for m in up if m.get("method") == "screencastFrame"]
    assert len(frames) == 1, up
    params = frames[0]["params"]
    assert params["data"] == "/9j/ZmFrZQ==", "base64 stays base64: the client decodes"
    # The vendored client reads viewportWidth/Height; the engine says device.
    assert (params["viewportWidth"], params["viewportHeight"]) == (1270, 922)
    assert params["timestamp"] == 12.5
    assert conn.posted == [("Page.screencastFrameAck", {"screencastId": "cast-1"},
                            "session-1")]
    assert not [s for s in conn.sent if s[0] == "Page.screencastFrameAck"], (
        "the ack went through send(): on the reader thread that is a deadlock")


def test_a_frame_after_stop_is_dropped_and_stop_reaches_the_engine():
    conn = RecordingConnection()
    page, up = _bare_page(conn)
    page.op_screencast_start({"sendFrames": True, "record": False})
    page.op_screencast_stop({})
    assert conn.sent[-1][0] == "Page.stopScreencast"
    assert page._screencast_id is None
    page._on_juggler_event("Page.screencastFrame", {"data": "x",
                                                    "deviceWidth": 1, "deviceHeight": 1})
    assert not [m for m in up if m.get("method") == "screencastFrame"]
    assert not conn.posted, "no ack for a frame nobody is streaming"
    # Stopping twice is not an error: the client calls stop() from dispose.
    assert page.op_screencast_stop({}) is None


def test_a_video_file_is_refused_with_the_reason():
    """`path=` used to produce a white .webm with no error (client-fork 3.7).
    Now it is refused by name: there is no encoder in the engine."""
    from invisible_playwright._juggler.dispatcher import ProtocolException
    conn = RecordingConnection()
    page, _ = _bare_page(conn)
    with pytest.raises(ProtocolException) as refused:
        page.op_screencast_start({"sendFrames": False, "record": True})
    assert "video" in str(refused.value) and "encoder" in str(refused.value)
    assert not conn.sent, "nothing was asked of the engine"
    with pytest.raises(ProtocolException) as nobody:
        page.op_screencast_start({"sendFrames": False, "record": False})
    assert "on_frame" in str(nobody.value)


def test_start_and_stop_left_the_perimeter_and_the_overlay_did_not():
    from invisible_playwright._juggler import perimeter
    assert "screencastStart" not in perimeter.OUTSIDE
    assert "screencastStop" not in perimeter.OUTSIDE
    # The recorder's captions and chapters still need the driver's overlay.
    assert perimeter.OUTSIDE["screencastShowActions"] == "video"


def _serve(body):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H


@pytest.mark.e2e
def test_a_screencast_frame_is_a_jpeg_of_the_whole_window(firefox_binary):
    """Through the public API, against a real engine: the frames are JPEG
    bytes and TALLER than the content viewport, which is the chrome above it.

    ⛔ THE SIGNATURE, NOT THE LENGTH. A frame that is the string of a base64
    blob nobody decoded is still non-empty bytes; the JPEG magic is what
    separates a picture from a plausible one.
    """
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    frames = []

    # A plain function, not `frames.append`: the sync client tags the handler
    # with an attribute, and a builtin method cannot carry one.
    def on_frame(frame):
        frames.append(frame)

    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.set_viewport_size({"width": 800, "height": 600})
            page.goto(url)
            try:
                page.screencast.start(on_frame=on_frame,
                                      size={"width": 4000, "height": 4000})
            except Exception as refused:
                # ⛔ A SKIP THAT NAMES THE ENGINE, not a pass and not a red.
                # The CI drives the SEALED engine, and until the seal rolls to
                # firefox-28 that engine has no `Page.startScreencast` at all:
                # the first run of this test in CI went red on exactly that
                # sentence, against firefox-27, with 206 other tests green.
                # Anything else the engine answers is still a failure.
                if "Page.startScreencast" in str(refused) and "not supported" in str(refused):
                    pytest.skip("this engine has no screencast (it needs "
                                "firefox-28 or later): %s" % refused)
                raise
            deadline = time.time() + 15
            while len(frames) < 3 and time.time() < deadline:
                time.sleep(0.1)
            page.screencast.stop()
    finally:
        srv.shutdown()
    assert frames, "no frame arrived in 15 s"
    first = frames[0]
    assert first["data"][:3] == b"\xff\xd8\xff", "not a JPEG"
    assert first["viewportHeight"] > 600, (
        "the frame is %dx%d, no taller than the 800x600 viewport: the chrome "
        "is not in it, so this is the page and not the window"
        % (first["viewportWidth"], first["viewportHeight"]))


def test_the_caller_chooses_the_frame_rate():
    """⛔ IT IGNORED THE CALLER, and that was the second half of a slow live
    view. The client's own pause was fixed first and the pane still could not
    go past ten frames a second, because this passed the wrapper's constant to
    the engine whatever was asked for. The engine has taken an `fps` all along.

    Measured 2026-09-08 on the same page, interleaved arms: asking for 10
    delivers 9.6-9.8 fps at 257 KB/s, asking for 25 delivers 23.8-24.0 at
    629 KB/s. That is why the DEFAULT does not move - raising it would put two
    and a half times the bandwidth on every consumer, including a batch job
    that never looks at a frame - and why the caller can ask.

    Known-bad: pass `self.SCREENCAST_FPS` unconditionally again. The second
    assertion still holds, because the default is what it always was.
    """
    conn = RecordingConnection()
    page, _ = _bare_page(conn)

    page.op_screencast_start({"sendFrames": True, "record": False, "fps": 25})
    assert conn.sent[-1][1]["fps"] == 25, conn.sent[-1][1]

    # A second start on the same page is refused, correctly: the default is
    # asked of a page of its own rather than by stopping and restarting this
    # one, which would be testing the stop as much as the rate.
    page, _ = _bare_page(conn)
    page.op_screencast_start({"sendFrames": True, "record": False})
    from invisible_playwright._juggler.server import PageDispatcher

    assert conn.sent[-1][1]["fps"] == PageDispatcher.SCREENCAST_FPS
    assert PageDispatcher.SCREENCAST_FPS == 10, (
        "the default rate moved; raising it imposes the bandwidth of a live "
        "view on every consumer, which is what the parameter exists to avoid")


def test_the_rate_reaches_the_engine_from_the_public_api():
    """A parameter the wrapper honours and the public signature does not offer
    is a parameter nobody outside can use.

    Known-bad: drop `fps` from either generated API. The impl accepts it and no
    caller can pass it.
    """
    import inspect

    from invisible_playwright._pw.async_api._generated import Screencast as A
    from invisible_playwright._pw.sync_api._generated import Screencast as S
    from invisible_playwright._pw._impl._screencast import Screencast as Impl

    for cls, what in ((A, "async"), (S, "sync"), (Impl, "impl")):
        assert "fps" in inspect.signature(cls.start).parameters, (
            "%s screencast.start() cannot be asked for a frame rate" % what)

    # ⛔ AND THAT IT IS FORWARDED, not only declared. The first version of this
    # asserted the signature alone and the mutation that deletes `fps=fps` from
    # the generated wrapper SURVIVED it: a parameter a caller can pass and that
    # goes nowhere is the inert-lever defect this project has met before, and
    # it is worse than a missing one because it looks like it works.
    #
    # Every parameter, not just this one: the property is that a public
    # signature forwards what it declares.
    for cls, what in ((A, "async"), (S, "sync")):
        body = inspect.getsource(cls.start)
        head, _, tail = body.partition("return")
        dropped = [name for name in inspect.signature(cls.start).parameters
                   if name not in ("self",)
                   and ("%s=%s" % (name, name)) not in tail
                   and ("onFrame=self._wrap_handler(%s)" % name) not in tail]
        assert not dropped, (
            "%s screencast.start() declares parameters it never passes on, so "
            "a caller setting them changes nothing: %s" % (what, dropped))


@pytest.mark.e2e
def test_asking_for_a_higher_rate_actually_delivers_more_frames(firefox_binary):
    """⛔ THE PARAMETER REACHES THE ENGINE, which the unit test cannot say. That
    one proves the wrapper puts a number on the wire; only a real engine can say
    the number does anything, and a lever nobody verified from inside the system
    it moves is a lever this project has been fooled by before.

    Interleaved is not possible here - a screencast is per page and the rate is
    fixed at start - so the arms are two pages of the same browser, same page
    served, same duration, and the assertion is on the RATIO rather than on
    either count: the absolute rate depends on the machine, and the claim is
    that asking for more gets more.

    Measured 2026-09-08 on this machine: 10 asked delivered 9.6-9.8 fps, 25
    asked delivered 23.8-24.0. The floor below is deliberately far from that -
    it is testing that the lever is connected, not re-measuring it.
    """
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright

    def count(page, fps, seconds=3.0):
        got = []

        def on_frame(frame):
            got.append(frame)

        page.goto(url)
        try:
            page.screencast.start(on_frame=on_frame, fps=fps)
        except Exception as refused:
            if "Page.startScreencast" in str(refused) and "not supported" in str(refused):
                pytest.skip("this engine has no screencast (it needs "
                            "firefox-28 or later): %s" % refused)
            raise
        time.sleep(seconds)
        page.screencast.stop()
        return len(got)

    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            slow = count(browser.new_page(), 5)
            fast = count(browser.new_page(), 25)
    finally:
        srv.shutdown()

    assert slow and fast, "no frames at all: %d and %d" % (slow, fast)
    assert fast > slow * 1.5, (
        "asking for 25 frames a second delivered %d in three seconds and "
        "asking for 5 delivered %d: the rate the caller asks for is not "
        "reaching the engine" % (fast, slow))
