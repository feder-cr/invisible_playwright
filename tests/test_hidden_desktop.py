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

import sys
import time

import pytest

from invisible_playwright import InvisiblePlaywright

_WEBGL_RENDERER = """() => {
  const g = document.createElement('canvas').getContext('webgl');
  if (!g) return 'NO-WEBGL';
  const d = g.getExtension('WEBGL_debug_renderer_info');
  return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : (g.getParameter(g.RENDERER) || '');
}"""


def _moz_windows_on(desktop_name):
    """How many ``MozillaWindowClass`` top-level windows live on a desktop:
    the caller's own when ``desktop_name`` is None, else the named one."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def cb(hwnd, _):
        c = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, c, 256)
        if c.value == "MozillaWindowClass":
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
def test_hidden_desktop_takes_the_window_off_screen_but_keeps_rendering(firefox_binary):
    # Control arm: the enumeration sees a headed window where it should be.
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=False) as browser:
        page = browser.new_page()
        page.goto("https://example.com", timeout=30_000)
        time.sleep(1.0)
        assert _moz_windows_on(None) >= 1, (
            "a headed session left no window on the interactive desktop, so "
            "the enumeration below could not tell hidden from broken")

    ip = InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True)
    with ip as browser:
        page = browser.new_page()
        page.goto("https://example.com", timeout=30_000)
        time.sleep(2)

        desktop = ip._virtual_display.name
        assert desktop and desktop != "Default"

        # 1) not on the desktop the caller is on ...
        assert _moz_windows_on(None) == 0, (
            "a headless=True session put a window on the interactive desktop")
        # 2) ... but really on its own.
        assert _moz_windows_on(desktop) >= 1, (
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
                        "in 15 s: the window capturer is not being used there")
        assert frames[0]["data"][:3] == b"\xff\xd8\xff", "not a JPEG"
