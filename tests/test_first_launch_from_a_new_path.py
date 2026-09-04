"""The first launch from a path Firefox has never run from works on Windows.

⛔ MEASURED 2026-09-04, AND THREE GATES HAD BEEN SAYING IT FOR FOUR DAYS. On
Windows the very first launch after a fresh install exited with 3221226505
(0xC0000409), before the protocol could be used and without printing a line;
the second launch on the same machine worked, and every one after that. The
daily user-install run on windows-latest was red from 2026-08-31, the release
pipeline's Windows gate was red on the last two engines, and the full e2e
against a fresh cache reproduced it, and nobody read them as one thing.

What died was Firefox's LAUNCHER process, not the browser. On a path it has
never run from, firefox.exe runs as the launcher first, forwards the Juggler
pipe to the real browser process, and records that in the registry per
executable path. The forwarding code read the pipe from CRT descriptors 3 and
4, which is what a Node.js parent provides. This client passes the pipe as
PW_PIPE_READ / PW_PIPE_WRITE handles and opens no descriptor 3, so
_get_osfhandle(3) tripped the CRT's invalid-parameter handler and the launcher
died before creating anything. On the next run Firefox found a launcher
timestamp with no browser timestamp, disabled the launcher for that path, and
started the browser directly. That is why it failed exactly once per machine,
and why every experiment on the CONTENT of the cache directory came back clean:
the state was keyed by the path and lived in the registry.

This test puts the engine back in the state a stranger's machine is in, by
removing the registry values for the binary under test, and launches once. It
is the reproduction, made permanent. Against an engine before firefox-27 it
fails; from firefox-27 on, the launcher forwards the handles named in the
environment and the launch succeeds with the launcher process still enabled.

Windows only: no other platform has a launcher process. e2e: it launches the
engine. It touches HKCU for one executable path, which the launcher rewrites
on the next launch anyway.
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="the launcher process exists only on Windows"),
]

LAUNCHER_KEY = r"Software\Mozilla\Firefox\Launcher"
#: The values the launcher keeps per executable path, from LauncherRegistryInfo.cpp.
LAUNCHER_VALUES = ("Image", "Launcher", "Browser", "Telemetry", "Blocklist")


def _forget_this_path(exe: str) -> None:
    """Delete the launcher's registry values for `exe`: the state of a machine
    that has never run this binary from this path."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAUNCHER_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for suffix in LAUNCHER_VALUES:
            try:
                winreg.DeleteValue(key, exe + "|" + suffix)
            except FileNotFoundError:
                pass


def _timestamps(exe: str) -> dict:
    """The launcher and browser timestamps the two processes wrote, or None each."""
    import winreg

    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAUNCHER_KEY) as key:
        for suffix in ("Launcher", "Browser"):
            try:
                out[suffix] = winreg.QueryValueEx(key, exe + "|" + suffix)[0]
            except FileNotFoundError:
                out[suffix] = None
    return out


def test_the_first_launch_from_a_new_path_works(firefox_binary):
    """⛔ Both halves matter. The launch must succeed, AND the launcher process
    must have run and created the browser: a launcher timestamp followed by a
    later browser timestamp. A success with the launcher disabled would be the
    second-launch path, which always worked, and would prove nothing.
    """
    from invisible_playwright import InvisiblePlaywright

    exe = os.path.normpath(firefox_binary)
    _forget_this_path(exe)

    with InvisiblePlaywright(binary_path=exe, headless=True, seed=1) as browser:
        page = browser.new_context().new_page()
        page.goto("about:blank")
        assert page.evaluate("1 + 1") == 2

    stamps = _timestamps(exe)
    assert stamps["Launcher"], "the launcher process never recorded a start, so this was not a first launch"
    assert stamps["Browser"], (
        "the launcher recorded a start and the browser never did: the launcher "
        "died before creating it, which is the first-launch failure this test exists for")
    assert stamps["Browser"] != 0, "the launcher was disabled after a failure instead of running"
    assert stamps["Launcher"] < stamps["Browser"], (
        "the browser timestamp predates the launcher's, so the browser that ran "
        "was not the one this launcher created")
