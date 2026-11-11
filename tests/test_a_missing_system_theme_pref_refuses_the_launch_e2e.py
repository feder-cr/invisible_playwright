"""A launch missing a system font or colour declaration is refused.

The engine reads the CSS system fonts (ui.font.*) and the system colours no
stand-in covers (ui.highlight, ui.-moz-field, ...) from what invisible_core
declares, and never from the toolkit. With the stealth engine on and one of
them missing, the browser must refuse to start - before a page can load - and
say which pref, instead of answering from GTK. With every pref declared the
same launch succeeds (the control).

The profile and its user.js are the ones build_launch_plan writes, with the
named pref removed; the engine is started the way the Juggler connection tests
start it.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

MISSING = [
    "ui.highlight",
    "ui.-moz-field",
    "ui.font.menu",
]


def _drop(user_js: Path, names) -> int:
    """Remove the user_pref lines of exactly these names (the .dark and .size
    variants are other prefs and stay). Returns how many lines went."""
    kept, dropped = [], 0
    for line in user_js.read_text(encoding="utf-8").splitlines():
        m = re.match(r'user_pref\(("(?:[^"\\]|\\.)*"),', line)
        if m and json.loads(m.group(1)) in names:
            dropped += 1
        else:
            kept.append(line)
    user_js.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return dropped


def _launch(firefox_binary, drop):
    from invisible_core.juggler import connection as conn
    from invisible_core.launch import build_launch_plan

    profile_dir = tempfile.mkdtemp(prefix="system_theme_")
    try:
        plan = build_launch_plan(6023, profile_dir=profile_dir, binary_path=firefox_binary,
                                 timezone="UTC", locale="en-US")
        if drop:
            assert _drop(Path(profile_dir) / "user.js", {drop}) == 1, (
                "%s is not declared by this core, so there is nothing to remove" % drop)
        return conn.launch(firefox_binary, profile_dir, headless=True,
                           # A refusal ends in an abort on purpose, and the crash reporter would open
                           # a window on the desktop of whoever runs the test.
                           env=dict(plan.env, MOZ_CRASHREPORTER_DISABLE="1"),
                           ready_timeout=60.0), profile_dir
    except BaseException:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise


# Linux only: on Windows the engine takes these from the Windows toolkit as retail does
# (StealthVerifySystemTheme in widget/StealthSystemFonts.cpp returns unless MOZ_WIDGET_GTK), so a
# missing ui.* pref does not refuse there (candidate 42673795394's Windows gate: DID NOT RAISE).
ON_WINDOWS = sys.platform == "win32"


@pytest.mark.e2e
@pytest.mark.skipif(ON_WINDOWS, reason="the system theme is declared, and its absence refused, on Linux only")
@pytest.mark.parametrize("pref", MISSING)
def test_a_missing_pref_refuses_the_launch(firefox_binary, engine_at_least, pref):
    engine_at_least("firefox-64")
    with pytest.raises(RuntimeError) as err:
        c, profile_dir = _launch(firefox_binary, pref)
        # Not reached when the engine refuses: close what must not be running.
        c.close()
        shutil.rmtree(profile_dir, ignore_errors=True)
    message = str(err.value)
    assert "exited during startup" in message, message
    assert pref in message, "the refusal does not name %s:\n%s" % (pref, message)
    assert "is not declared" in message, message


@pytest.mark.e2e
def test_every_pref_declared_the_launch_succeeds(firefox_binary, engine_at_least):
    engine_at_least("firefox-64")
    c, profile_dir = _launch(firefox_binary, None)
    try:
        assert c.ready_seen, "the browser did not announce the protocol"
        assert c._process.poll() is None, "the browser is not running"
    finally:
        c.close()
        shutil.rmtree(profile_dir, ignore_errors=True)
