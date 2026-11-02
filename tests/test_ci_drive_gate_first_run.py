"""The drive gate's copy of the first-run declaration must not diverge from the core.

`scripts/ci_drive_gate.py` runs in a job that installs only Playwright, so it
carries FIRST_RUN, a literal copy of what invisible_core declares for the first
run, and writes it into the profile's user.js. Since firefox-55 the engine leaves
Mozilla's first-run lines and the core alone declares that state: without the
copy, run 41388672849 (2026-11-02) showed the terms-of-use modal on Windows
(no mouse event reached the page) and opened Mozilla's privacy page in a second
tab on Linux. This test binds the copy: a first-run pref the core adds, drops or
changes turns it red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[1] / "scripts" / "ci_drive_gate.py"
_FIRST_RUN_PREFIXES = ("termsofuse.", "browser.preonboarding.", "datareporting.policy.")


def _first_run_from_gate() -> dict:
    """FIRST_RUN as the gate defines it, without importing Playwright (imported
    inside `_drive`)."""
    ns: dict = {}
    head = _GATE.read_text(encoding="utf-8").split("\nHTML = (")[0]
    exec(compile(head, str(_GATE), "exec"), ns)  # noqa: S102 - our own file
    return ns["FIRST_RUN"]


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_ci_drive_gate_first_run_matches_the_core(monkeypatch, platform):
    from invisible_core._fpforge import generate_profile
    from invisible_core.prefs import translate_profile_to_prefs

    monkeypatch.setattr(sys, "platform", platform)
    prefs = translate_profile_to_prefs(generate_profile(970411), timezone="America/New_York")
    from_core = {k: v for k, v in prefs.items() if k.startswith(_FIRST_RUN_PREFIXES)}
    from_gate = _first_run_from_gate()
    assert from_core, "the core declares no first-run state: the engine would show the modal"
    assert from_gate == from_core, (
        "FIRST_RUN in scripts/ci_drive_gate.py is no longer what invisible_core declares.\n"
        "  core: %r\n  gate: %r" % (from_core, from_gate))
    for k, v in from_gate.items():
        assert type(v) is type(from_core[k]), k
