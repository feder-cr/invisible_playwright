"""A launch that fails before the browser starts must still stop the display.

`headless=True` starts the hidden surface - an Xvfb process on Linux, a Win32
desktop handle on Windows - inside `_resolve_headless`, and only `_teardown`
stops it. Composing the prefs, the proxy and the environment comes after that
and can raise (`build_env` refuses a font manifest the engine does not carry),
so it has to sit inside the `try` whose handler calls `_teardown`, or every
such failure leaves one display behind in the caller's process.
"""
from __future__ import annotations

import asyncio
import types

import pytest
from invisible_core import SessionLocale
from invisible_core.launch import FontManifestMismatch

from invisible_playwright import _session, async_api, launcher


class RecordingDisplay:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def launch_env(self):
        return {}


@pytest.fixture
def display(monkeypatch):
    vd = RecordingDisplay()
    geo = types.SimpleNamespace(timezone="Europe/Rome",
                                locale=SessionLocale.of("it-IT"),
                                egress_ip=None,
                                geolocation_override=lambda: None)

    def refuse(**_kw):
        raise FontManifestMismatch("the engine does not carry these faces")

    def no_playwright():
        raise AssertionError("the launch must fail before Playwright starts")

    monkeypatch.delenv(_session.TRUE_HEADLESS_ENV, raising=False)
    monkeypatch.setattr(_session, "make_virtual_display", lambda: vd)
    monkeypatch.setattr(_session, "resolve_executable", lambda _p: "firefox")
    monkeypatch.setattr(_session, "build_env", refuse)
    for mod in (launcher, async_api):
        monkeypatch.setattr(mod, "prepare_session_geo", lambda *_a, **_k: geo)
        monkeypatch.setattr(mod, "resolve_executable", lambda _p: "firefox")
    monkeypatch.setattr(launcher, "sync_playwright", no_playwright)
    monkeypatch.setattr(async_api, "async_playwright", no_playwright)
    return vd


def test_the_sync_launch_stops_the_display_it_started(display):
    session = launcher.InvisiblePlaywright(seed=7, headless=True)
    with pytest.raises(FontManifestMismatch):
        session.__enter__()
    assert display.started == 1
    assert display.stopped == 1, "the hidden display outlived a failed launch"
    assert session._virtual_display is None


def test_the_async_launch_stops_the_display_it_started(display):
    session = async_api.InvisiblePlaywright(seed=7, headless=True)
    with pytest.raises(FontManifestMismatch):
        asyncio.run(session.__aenter__())
    assert display.started == 1
    assert display.stopped == 1, "the hidden display outlived a failed launch"
    assert session._virtual_display is None
