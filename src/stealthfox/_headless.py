"""Invisible-but-headed browser windows.

Playwright's ``headless=True`` flips Firefox onto a different code path —
no widget tree, software-only rendering, distinct timing — and anti-bot
systems can spot the divergence. Running the browser *headed* on a
virtual display gives us the real rendering pipeline while keeping the
windows off the user's screen.

Linux: spawns its own ``Xvfb`` instance, points ``DISPLAY`` at it.
Windows: creates a hidden desktop via ``CreateDesktop`` and binds the
calling thread to it, so Playwright's child processes inherit it.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from typing import Optional


# Inherited from WSLg / GNOME / etc. these env vars make Firefox prefer a
# Wayland compositor over the X11 DISPLAY we set, so the window leaks onto
# the real desktop. Strip them all before starting.
_WAYLAND_LEAK_VARS = (
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "PULSE_SERVER",
    "WSL2_GUI_APPS_ENABLED",
)


class _LinuxVirtualDisplay:
    """Standalone Xvfb instance owned by this Stealthfox session."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self._geometry = f"{width}x{height}x24"
        self._proc: Optional[subprocess.Popen] = None
        self._display: Optional[str] = None
        self._saved_env: dict[str, Optional[str]] = {}

    def start(self) -> None:
        if not _binary_on_path("Xvfb"):
            raise RuntimeError(
                "stealthfox headless=True requires Xvfb. "
                "Install it: sudo apt install xvfb"
            )
        # Retry: when many workers start in parallel they can pick the same
        # display number before any has created its lockfile. Xvfb on the
        # losing side exits immediately — try again with a fresh number.
        last_err: Optional[Exception] = None
        for _ in range(10):
            display = self._pick_display()
            try:
                self._spawn(display)
                self._wait_until_ready(display)
                self._display = display
                self._apply_env(display)
                return
            except RuntimeError as e:
                last_err = e
                if self._proc is not None and self._proc.poll() is None:
                    self._proc.kill()
                self._proc = None
        raise RuntimeError(f"Xvfb failed to start after 10 attempts: {last_err}")

    def _spawn(self, display: str) -> None:
        self._proc = subprocess.Popen(
            [
                "Xvfb", display,
                "-screen", "0", self._geometry,
                "+extension", "GLX",
                "+extension", "RENDER",
                "-nolisten", "unix",
                "-listen", "tcp",
                "-ac",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _pick_display(self) -> str:
        for n in range(99, 400):
            if not os.path.exists(f"/tmp/.X{n}-lock"):
                return f":{n}"
        raise RuntimeError("no free X display number in :99–:399")

    def _wait_until_ready(self, display: str) -> None:
        # We start Xvfb with -nolisten unix → no /tmp/.X11-unix socket appears.
        # Xvfb creates /tmp/.X{n}-lock immediately though — wait for that.
        lockfile = f"/tmp/.X{display[1:]}-lock"
        deadline = time.monotonic() + 3.0
        assert self._proc is not None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"Xvfb {display} exited immediately")
            if os.path.exists(lockfile):
                return
            time.sleep(0.02)
        raise RuntimeError(f"Xvfb {display} did not become ready in 3s")

    def _apply_env(self, display: str) -> None:
        keys = ("DISPLAY", "MOZ_ENABLE_WAYLAND", "GDK_BACKEND") + _WAYLAND_LEAK_VARS
        for k in keys:
            self._saved_env[k] = os.environ.get(k)
        for k in _WAYLAND_LEAK_VARS:
            os.environ.pop(k, None)
        os.environ["DISPLAY"] = display
        os.environ["MOZ_ENABLE_WAYLAND"] = "0"
        os.environ["GDK_BACKEND"] = "x11"

    def stop(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._saved_env.clear()

        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._proc = None
        self._display = None


class _WindowsVirtualDesktop:
    """A hidden Windows desktop the calling thread is bound to.

    Playwright's child processes (node driver → firefox.exe) inherit the
    desktop because their ``STARTUPINFO.lpDesktop`` is NULL — Windows uses
    the calling thread's desktop in that case.

    pywin32 ships ``CreateDesktop`` in ``win32service`` but doesn't expose
    ``SetThreadDesktop`` / ``GetThreadDesktop`` as module functions. We
    call them directly via ctypes against ``user32.dll``.
    """

    def __init__(self) -> None:
        self._desktop = None      # PyHDESK from win32service.CreateDesktop
        self._original_handle = 0  # raw HDESK int of the previous desktop

    def start(self) -> None:
        try:
            import win32con  # type: ignore
            import win32service  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "stealthfox headless=True on Windows requires pywin32. "
                "Install it: pip install pywin32"
            ) from e

        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Save the current desktop handle so we can restore it on stop().
        get_thread_desktop = user32.GetThreadDesktop
        get_thread_desktop.argtypes = [wintypes.DWORD]
        get_thread_desktop.restype  = wintypes.HANDLE
        self._original_handle = get_thread_desktop(kernel32.GetCurrentThreadId())

        name = f"sf_{secrets.token_hex(4)}"
        self._desktop = win32service.CreateDesktop(
            name, 0, win32con.GENERIC_ALL, None
        )

        # Bind the calling thread to the new desktop. Children spawned
        # afterwards (Playwright driver → firefox.exe) inherit it because
        # their STARTUPINFO.lpDesktop is NULL.
        set_thread_desktop = user32.SetThreadDesktop
        set_thread_desktop.argtypes = [wintypes.HANDLE]
        set_thread_desktop.restype  = wintypes.BOOL
        if not set_thread_desktop(int(self._desktop)):
            err = ctypes.get_last_error()
            raise RuntimeError(
                f"SetThreadDesktop failed (GetLastError={err}). "
                "The thread cannot have any windows or hooks; close them first."
            )

    def stop(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        if self._original_handle:
            try:
                set_thread_desktop = user32.SetThreadDesktop
                set_thread_desktop.argtypes = [wintypes.HANDLE]
                set_thread_desktop.restype  = wintypes.BOOL
                set_thread_desktop(self._original_handle)
            except Exception:
                pass
            self._original_handle = 0

        if self._desktop is not None:
            try:
                self._desktop.CloseDesktop()
            except Exception:
                pass
            self._desktop = None


def make_virtual_display():
    """Return a started/stoppable virtual-display object for this platform.

    Stealthfox supports Windows x86_64 and Linux x86_64 only.
    """
    if sys.platform == "win32":
        return _WindowsVirtualDesktop()
    if sys.platform.startswith("linux"):
        return _LinuxVirtualDisplay()
    raise RuntimeError(
        f"stealthfox supports Windows and Linux only (got {sys.platform!r})"
    )


def _binary_on_path(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None
