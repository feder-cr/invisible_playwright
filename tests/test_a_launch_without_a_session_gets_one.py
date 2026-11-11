"""A launch that names no session gets one: the profile is always a composed session.

Before, `op_launch` wrote into `user.js` only the prefs the client sent. The launcher
(`InvisiblePlaywright`, `launch_server`) sends a composed session, but a bare
`BrowserType.launch` from any other client of the server (Playwright's own suite, a
script on `invisible_playwright._pw`) sent none, and the browser was the engine with
Mozilla's defaults: no persona, and on Linux the first-run policy tab that makes
`Browser.newPage` refuse with "Unexpected number of tabs in the new window: 2".

Here, with the engine replaced by a stub, what the profile holds is read back from
`user.js`: an unmarked launch gets the session `InvisiblePlaywright()` composes (with
the caller's prefs on top), and a composed one is written as it came.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_playwright import _session
from invisible_playwright._juggler import server as module
from invisible_playwright._juggler.server import (SESSION_SEED_PREF, BrowserTypeDispatcher,
                                                  JugglerServer)

# Read with a default so that, on the code before the fix, the test fails on what the
# profile holds and not on an import.
COMPOSED_PREF = getattr(module, "COMPOSED_PREF", "invisible.server.composed")

_LINE = re.compile(r'^user_pref\("([^"]+)",\s*(.*)\);\s*$')


class _Engine(EventListeners):
    def __init__(self):
        EventListeners.__init__(self)

    def send(self, method, params=None, session=None, timeout=30.0):
        return {"browserContextId": "ctx-1", "targetId": "t-1"}

    def startup_send(self, method, params=None):
        return self.send(method, params)

    def close(self, timeout=5.0):
        pass


def _user_js(monkeypatch, prefs):
    """Launch with these firefoxUserPrefs; return the prefs `user.js` holds."""
    server = JugglerServer()
    server.attach(type("R", (), {"emit_message": lambda self, m: None})())
    seen = {}

    def fake_launch(executable, profile_dir, **kwargs):
        seen.update(_read(pathlib.Path(profile_dir) / "user.js"))
        return _Engine()

    monkeypatch.setattr(module.juggler, "launch", fake_launch)
    try:
        BrowserTypeDispatcher(server).op_launch({"executablePath": "x", "firefoxUserPrefs": prefs})
    finally:
        server.shutdown()
    return seen


def _read(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = json.loads(m.group(2))
    return out


@pytest.mark.unit
def test_a_launch_that_names_no_session_writes_a_composed_one(monkeypatch):
    written = _user_js(monkeypatch, {})
    # The Terms of Use the core declares accepted: without them Linux opens the policy tab.
    assert written.get("termsofuse.acceptedVersion") == 4, (
        "a bare launch wrote no session into the profile: %d prefs" % len(written))
    composed = _session.default_session_prefs("x")
    missing = sorted(k for k in composed if k not in written and not k.startswith("invisible.server."))
    assert not missing, "prefs of the composed session absent from user.js: %s" % missing[:10]
    assert COMPOSED_PREF not in written and SESSION_SEED_PREF not in written, (
        "the server's own keys reached the profile")


@pytest.mark.unit
def test_the_caller_s_prefs_go_on_top_of_the_composed_session(monkeypatch):
    written = _user_js(monkeypatch, {"termsofuse.acceptedVersion": 9, "a.pref.of.the.caller": True})
    assert written["termsofuse.acceptedVersion"] == 9
    assert written["a.pref.of.the.caller"] is True
    assert "fission.webContentIsolationStrategy" in written


@pytest.mark.unit
def test_a_composed_session_is_written_as_it_came(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a composed session was composed again on the server")

    monkeypatch.setattr(_session, "default_session_prefs", refuse)
    written = _user_js(monkeypatch, {COMPOSED_PREF: True, "only.this": 1})
    assert written == {"only.this": 1}


@pytest.mark.unit
def test_the_launcher_marks_what_it_composes():
    """`build_prefs` is what `InvisiblePlaywright` and `launch_server` (`remote._launch_params`,
    through `session._build_prefs`) send as firefoxUserPrefs: each must arrive marked."""
    from invisible_core import decide_session_locale
    from invisible_core._fpforge import generate_profile

    for humanize in (False, True):
        prefs = _session.build_prefs(
            profile=generate_profile(seed=7), locale=decide_session_locale("en-US"),
            timezone="America/New_York", extra_prefs=None, virtual_display=False,
            cursor_engine=_session.ENGINE_BINARY, humanize=humanize, session_seed=7)
        assert prefs.get(COMPOSED_PREF) is True, humanize
