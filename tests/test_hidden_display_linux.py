"""Hidden-display guard (e2e, Linux): ``headless=True`` puts the browser on an
Xvfb the SESSION owns, and nothing of it reaches the process or another
session.

This is the Linux twin of ``test_hidden_desktop.py`` and it exists because
the e2e that already ran under ``xvfb-run`` was green over a real defect: a
headed session opened while an Xvfb session was alive in the same process
was born on the Xvfb (B221, the core wrote ``DISPLAY`` into ``os.environ``),
and after that was fixed the hidden browser still carried ``WAYLAND_DISPLAY``
(the in-process server merged the launch environment over its own). A test
that opens a page and screenshots it sees neither: the page renders on any
display. What sees both is the environment of the launched process, read
from ``/proc/<pid>/environ``, which is what this file asserts.

Three arms, and the first is a CONTROL:

1. a headed session's Firefox is born on the caller's ``DISPLAY``;
2. a hidden session's Firefox is born on the session's own Xvfb, with the
   toolkit pinned to X11 and NONE of the Wayland variables the caller's
   environment may carry - and ``os.environ`` is the same before, during and
   after;
3. a headed session opened WHILE the hidden one is alive is still born on
   the caller's ``DISPLAY``, not on the Xvfb.

The async API is used because two sync sessions cannot share one thread.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from invisible_playwright.async_api import InvisiblePlaywright

# What the hidden session names for removal; asserted absent on the hidden
# browser whatever the caller's environment carries. Mirrors
# ``invisible_core._headless._WAYLAND_LEAK_VARS`` on purpose: this file
# measures the outcome, not the constant.
_WAYLAND_VARS = ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
                 "PULSE_SERVER", "WSL2_GUI_APPS_ENABLED")


#: Set in THIS process before each launch, with a value unique to the arm.
#: The browser's environment is composed from a copy of ``os.environ``, so
#: the browser we launch inherits it and nobody else's does: the e2e runs
#: under xdist with several workers, each a process of its own, launching
#: Firefox at the same time (measured in CI: "expected one new Firefox, saw
#: 2"). Counting processes was the wrong question; carrying a mark is the
#: same road every inherited variable takes.
_MARK = "INVPW_TEST_MARK"


def _firefox_environments(binary: str) -> dict:
    """``{pid: env}`` for every PARENT Firefox process of ``binary``."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                argv = fh.read().split(b"\0")
            if not argv or argv[0] != binary.encode() or b"-contentproc" in argv:
                continue
            with open("/proc/%s/environ" % pid, "rb") as fh:
                pairs = (kv.split(b"=", 1) for kv in fh.read().split(b"\0") if b"=" in kv)
                out[int(pid)] = {k.decode(): v.decode() for k, v in pairs}
        except (OSError, ValueError):
            continue
    return out


def _ours(binary: str, mark: str) -> dict:
    """The environment of the one Firefox born with ``mark``."""
    marked = [env for env in _firefox_environments(binary).values()
              if env.get(_MARK) == mark]
    assert len(marked) == 1, (
        "expected exactly one Firefox carrying %s=%s, saw %d" % (_MARK, mark, len(marked)))
    return marked[0]


@pytest.mark.e2e
@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="Xvfb is the Linux path; Windows hides on a desktop")
def test_hidden_display_stays_with_its_session(firefox_binary, monkeypatch):
    # A caller's environment that carries a Wayland session, as WSLg and GNOME
    # do: the hidden browser must not inherit it, whatever the runner has.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    host_display = os.environ.get("DISPLAY")
    assert host_display, "this test needs a DISPLAY to tell headed from hidden"
    process_env = dict(os.environ)

    async def scenario():
        # 1) control: a headed session lands on the caller's display.
        monkeypatch.setenv(_MARK, "headed-control")
        async with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                       headless=False) as browser:
            page = await browser.new_page()
            await page.goto("about:blank")
            headed = _ours(firefox_binary, "headed-control")
        assert headed["DISPLAY"] == host_display, (
            "a headed session was not born on the caller's DISPLAY, so the "
            "arms below could not tell hidden from broken")

        # 2) hidden: the session's own Xvfb, X11 pinned, Wayland stripped.
        monkeypatch.setenv(_MARK, "hidden")
        hidden_session = InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                             headless=True)
        async with hidden_session as browser:
            page = await browser.new_page()
            await page.goto("about:blank")
            hidden = _ours(firefox_binary, "hidden")
            xvfb = hidden_session._virtual_display._display
            assert xvfb and xvfb != host_display
            assert hidden["DISPLAY"] == xvfb, (
                "the hidden Firefox was not born on the session's Xvfb %r: %r"
                % (xvfb, hidden.get("DISPLAY")))
            assert hidden["MOZ_ENABLE_WAYLAND"] == "0"
            assert hidden["GDK_BACKEND"] == "x11"
            for var in _WAYLAND_VARS:
                assert var not in hidden, (
                    "%s reached the hidden browser: a variable the session "
                    "names for removal came back from the process" % var)
            assert {k: v for k, v in os.environ.items() if k != _MARK} == process_env, (
                "starting the hidden session wrote into os.environ")

            # 3) B221 itself: a headed session opened while the hidden one is
            #    alive is born where it was asked to be.
            monkeypatch.setenv(_MARK, "headed-beside-hidden")
            async with InvisiblePlaywright(seed=7, binary_path=firefox_binary,
                                           headless=False) as other:
                page = await other.new_page()
                await page.goto("about:blank")
                concurrent = _ours(firefox_binary, "headed-beside-hidden")
            assert concurrent["DISPLAY"] == host_display, (
                "a headed session opened beside a hidden one was born on the "
                "hidden one's Xvfb %r" % xvfb)
            assert concurrent.get("WAYLAND_DISPLAY") == "wayland-test", (
                "the headed session lost a variable only the hidden one drops")

        assert {k: v for k, v in os.environ.items() if k != _MARK} == process_env, (
            "stopping the hidden session wrote into os.environ")

    asyncio.run(scenario())
