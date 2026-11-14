"""Hidden-desktop guard (e2e): ``headless=True`` on Windows keeps the real
rendering pipeline and puts the whole browser tree on a Win32 desktop nobody
looks at.

Three things are asserted, and the first is a CONTROL: a headed session must
show up on the interactive desktop, or an enumeration that finds nothing on it
proves nothing. Then the hidden session must be absent from that desktop and
present on its own, and must still render (a non-blank screenshot and a live
WebGL context: Playwright's native headless has neither the window nor the
context).

Linux is skipped - there the wrapper hides through Xvfb, and the Linux e2e
runs under ``xvfb-run`` which is that path end to end.

This replaces ``test_cloak.py`` (2026-06-11 to 2026-09-20), which asserted a
DWMWA_CLOAK attribute the binary set on its own window. The engine is stock on
that surface now; what hides the window is WHERE it is created.
"""
from __future__ import annotations

import http.server
import sys
import threading
import time

import pytest

from invisible_playwright import InvisiblePlaywright

from _servers import LocalServer

# The page the session loads: served by this test from 127.0.0.1, so nothing
# here depends on a site of the real web.
_PAGE = (b"<!doctype html><html><head><meta charset=utf-8><title>hidden desktop</title>"
         b"</head><body style=\"background:#fff;color:#000;font:24px sans-serif\">"
         b"<h1>hidden desktop</h1><p>a page the test serves itself</p></body></html>")


@pytest.fixture
def served():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE)))
            self.end_headers()
            self.wfile.write(_PAGE)

        def log_message(self, *a):
            pass

    with LocalServer(("127.0.0.1", 0), H) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d/" % srv.server_address[1]
        finally:
            srv.shutdown()


def _engine_screencast_lines(page):
    """The lines the engine printed that start with ``screencast:``, from the
    browser's own stdout/stderr (the core keeps the last 200 lines of the
    pipe for the whole life of the process, `ProcessOutput`). Where the
    engine has no line for it the answer says so, so a failure message never
    comes out empty."""
    try:
        impl = page._impl_obj
        server = impl._connection._transport._server
        output = server.object(impl._guid).conn.output
        lines = output.tail(output.KEEP)
    except Exception as failure:  # the message is the point, not the lookup
        return "(the engine output could not be read: %r)" % (failure,)
    mine = [l for l in lines if l.lstrip().startswith("screencast:")]
    return "\n".join(mine) if mine else "(the engine printed no line starting with 'screencast:')"


_WEBGL_RENDERER = """() => {
  const g = document.createElement('canvas').getContext('webgl');
  if (!g) return 'NO-WEBGL';
  const d = g.getExtension('WEBGL_debug_renderer_info');
  return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : (g.getParameter(g.RENDERER) || '');
}"""


def _moz_windows_on(desktop_name, session):
    """How many ``MozillaWindowClass`` top-level windows of THIS session live
    on a desktop: the caller's own when ``desktop_name`` is None, else the
    named one.

    ⛔ ONLY THE SESSION'S OWN WINDOWS COUNT. This used to count every Mozilla
    window on the desktop, so any other Firefox open on the machine - another
    test run, another session, the user's own browser - made a correct build
    fail with "a headless=True session put a window on the interactive
    desktop": measured 5 foreign windows, and the same red on firefox-35 and
    on the build under test. A window belongs to the session when its process
    carries the session's token, the same stamp the reaper reads.
    """
    import ctypes
    from ctypes import wintypes

    from invisible_playwright._reaper import find_processes

    mine = {p.pid for p in find_processes(session._session_token)}
    assert mine, "the session's token matches no process, so nothing could be counted"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def cb(hwnd, _):
        c = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, c, 256)
        if c.value == "MozillaWindowClass":
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in mine:
                found.append(hwnd)
        return True

    if desktop_name is None:
        user32.EnumWindows(ENUM(cb), 0)
    else:
        desktop_enumerate = 0x0100
        h = user32.OpenDesktopW(desktop_name, 0, False, desktop_enumerate)
        assert h, "cannot open desktop %r (WinError %d)" % (
            desktop_name, ctypes.get_last_error())
        user32.EnumDesktopWindows(wintypes.HANDLE(h), ENUM(cb), 0)
        user32.CloseDesktop(wintypes.HANDLE(h))
    return len(found)


@pytest.mark.e2e
@pytest.mark.skipif(sys.platform != "win32",
                    reason="the hidden desktop is the Windows path; Linux hides via Xvfb")
def test_hidden_desktop_takes_the_window_off_screen_but_keeps_rendering(firefox_binary, served):
    # Control arm: the enumeration sees a headed window where it should be.
    headed = InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=False)
    with headed as browser:
        page = browser.new_page()
        page.goto(served, timeout=30_000)
        time.sleep(1.0)
        assert _moz_windows_on(None, headed) >= 1, (
            "a headed session left no window on the interactive desktop, so "
            "the enumeration below could not tell hidden from broken")

    ip = InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True)
    with ip as browser:
        page = browser.new_page()
        page.goto(served, timeout=30_000)
        time.sleep(2)

        desktop = ip._virtual_display.name
        assert desktop and desktop != "Default"

        # 1) not on the desktop the caller is on ...
        assert _moz_windows_on(None, ip) == 0, (
            "a headless=True session put a window on the interactive desktop")
        # 2) ... but really on its own.
        assert _moz_windows_on(desktop, ip) >= 1, (
            "no Firefox window on the session's desktop %r" % desktop)

        # 3) still the headed pipeline: a real screenshot and a WebGL context.
        #    GPU-less CI hosts answer with a software context, which is still
        #    a context; a missing one is the native-headless path and a fail.
        shot = page.screenshot()
        assert len(shot) > 3000, "hidden window produced a blank screenshot"
        renderer = page.evaluate(_WEBGL_RENDERER)
        assert renderer and renderer != "NO-WEBGL", (
            "no WebGL context on the hidden desktop: %r" % renderer)

        # 4) the page does not read a backgrounded browser.
        assert page.evaluate("document.visibilityState") == "visible"

        # 5) the WINDOW can still be watched from there. The engine's cropping
        #    capturer used to crop a visible window from the screen, which
        #    shows the input desktop only, and moved the capture thread there
        #    for good: 0 frames in 15 s on firefox-33. firefox-34 keeps a
        #    window of another desktop on the window capturer. This is the
        #    line that makes the engine's release gate prove it per build.
        frames = []

        def on_frame(frame):
            frames.append(frame)

        page.screencast.start(on_frame=on_frame)
        deadline = time.time() + 15
        while not frames and time.time() < deadline:
            time.sleep(0.1)
        page.screencast.stop()
        assert frames, ("no screencast frame arrived from the hidden desktop "
                        "in 15 s: the window capturer is not being used there\n"
                        "engine output (lines starting with 'screencast:'):\n%s"
                        % _engine_screencast_lines(page))
        assert frames[0]["data"][:3] == b"\xff\xd8\xff", "not a JPEG"
