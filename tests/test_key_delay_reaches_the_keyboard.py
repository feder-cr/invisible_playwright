"""`delay` on `type` and `press` reaches the keyboard, on every path.

⛔ THE DEFECT THIS FILE EXISTS FOR. `keyboard.type(text, delay=...)` and the
element-handle `type` honoured `delay`; `page.type(sel, text, delay=100)`,
`locator.type`, `press_sequentially` and `locator.press(delay=...)` accepted it
and dropped it, because each server operation read `params["delay"]` on its own
and the selector ones never did. Measured: the intervals with `delay=100` were
the intervals without it.

The fix is one reader, `_key_delay`, and the structural test below keeps it
the only one.
"""
from __future__ import annotations

import ast
import http.server
import inspect
import threading
from pathlib import Path

import pytest

from invisible_playwright._juggler import server as server_module
from invisible_playwright._juggler.actions import Actions
from invisible_playwright._juggler.server import ElementHandleDispatcher, FrameDispatcher


class _Recorder:
    """Records what an operation hands the action layer, by keyword."""

    def __init__(self):
        self.calls = []

    def type_text(self, selector, text, **kw):
        self.calls.append(("type_text", kw))

    def press(self, selector, key, **kw):
        self.calls.append(("press", kw))


class _Frame:
    def __init__(self):
        self.actions = _Recorder()
        self.frame_id = "frame-main"

    def enter_frames(self, selector):
        return self.frame_id, selector

    def _timeout(self, params):
        return 1.0

    def _act_opts(self, params):
        return {}


class _Handle:
    def __init__(self):
        self.frame = _Frame()
        self.object_id = "obj-1"

    _act_args = ElementHandleDispatcher._act_args


def _frame_op(name, params):
    frame = _Frame()
    frame.actions = _Recorder()
    getattr(FrameDispatcher, name)(_FrameSelf(frame), params)
    return frame.actions.calls[-1]


class _FrameSelf:
    """A FrameDispatcher as far as its key operations look at it."""

    def __init__(self, frame):
        self.actions = frame.actions
        self.frame_id = frame.frame_id
        self.enter_frames = frame.enter_frames
        self._timeout = frame._timeout
        self._act_opts = frame._act_opts


def _handle_op(name, params):
    handle = _Handle()
    getattr(ElementHandleDispatcher, name)(handle, params)
    return handle.frame.actions.calls[-1]


@pytest.mark.parametrize("op", [_frame_op, _handle_op], ids=["selector", "handle"])
def test_type_hands_the_gap_to_the_action(op):
    name, kw = op("op_type", {"selector": "#f", "text": "ab", "delay": 120})
    assert name == "type_text"
    assert kw["delay"] == 120.0


@pytest.mark.parametrize("op", [_frame_op, _handle_op], ids=["selector", "handle"])
def test_press_hands_the_dwell_to_the_action(op):
    name, kw = op("op_press", {"selector": "#f", "key": "a", "delay": 300})
    assert name == "press"
    assert kw["dwell_ms"] == 300.0


@pytest.mark.parametrize("op", [_frame_op, _handle_op], ids=["selector", "handle"])
def test_without_delay_the_session_hand_decides(op):
    _, kw = op("op_press", {"selector": "#f", "key": "a"})
    assert kw["dwell_ms"] is None
    _, kw = op("op_type", {"selector": "#f", "text": "ab"})
    assert kw["delay"] == 0.0


def test_press_gives_the_keyboard_the_dwell():
    pressed = []

    class _Keyboard:
        def press(self, key, *, dwell_ms=None):
            pressed.append((key, dwell_ms))

    class _Inj:
        def call(self, *a, **kw):
            return None

    class _Self:
        keyboard = _Keyboard()
        inj = _Inj()

        def _retry(self, selector, run, **kw):
            return run("frame", "element", (0, 0))

    Actions.press(_Self(), "#f", "Enter", dwell_ms=250.0)
    assert pressed == [("Enter", 250.0)]


def test_only_one_place_reads_the_key_delay():
    """`_pointer` reads the click's `delay` (mousedown to mouseup), a pointer
    option with its own reader. Any other `params.get("delay")` is a key
    operation reading it by itself, which is how four of them dropped it."""
    tree = ast.parse(Path(inspect.getfile(server_module)).read_text(encoding="utf-8"))
    readers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == "delay"):
                readers.add(node.name)
    assert readers == {"_key_delay", "_pointer"}, readers


PAGE = b"""<!doctype html><meta charset=utf-8>
<input id=f>
<script>
window.__ev = [];
for (const k of ["keydown", "keyup"])
  document.getElementById("f").addEventListener(k, e =>
    __ev.push([k, e.key, performance.now()]));
</script>"""


@pytest.fixture(scope="module")
def page_url():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_port
    srv.shutdown()


@pytest.mark.e2e
def test_the_page_sees_the_delay_the_caller_asked_for(firefox_binary, page_url):
    """With the hand switched off the keys go out a few ms apart, so a gap of
    150 ms and a dwell of 300 ms can only come from the caller's `delay`."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(binary_path=firefox_binary, headless=True,
                             humanize=False) as browser:
        page = browser.new_page()
        page.goto(page_url)
        page.locator("#f").type("abcd", delay=150)
        downs = [t for k, _, t in page.evaluate("__ev") if k == "keydown"]
        page.evaluate("__ev.length = 0")
        page.locator("#f").press("x", delay=300)
        ev = page.evaluate("__ev")

    gaps = [b - a for a, b in zip(downs, downs[1:])]
    assert len(gaps) == 3 and min(gaps) >= 120, gaps
    down = next(t for k, key, t in ev if k == "keydown" and key == "x")
    up = next(t for k, key, t in ev if k == "keyup" and key == "x")
    assert up - down >= 250, (down, up)
