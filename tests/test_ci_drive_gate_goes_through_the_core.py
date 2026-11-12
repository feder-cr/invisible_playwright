"""The drive gate launches the binary through the wrapper and the core, never through bare Playwright.

Since firefox-63 the engine holds every channel that asks the proxy service until the client has said how the session
leaves the host (`Browser.setBrowserProxy`, or `Browser.setNoBrowserProxy` for a session without a proxy). The core's launch
sends that decision first; stock Playwright sends it only when it is given a proxy. A drive gate that launches with
`p.firefox.launch_persistent_context(executable_path=...)` therefore waits for a decision nobody takes: run 43134774602,
`Page.goto: Timeout 30000ms` on every leg, three attempts.

The core also declares the first run (terms of use, pre-onboarding), which the gate used to copy into user.js
(`FIRST_RUN`, tied to the core by a test of its own): with the launch through the core the copy has no reason to exist.
This test reads the script; it launches nothing.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[1] / "scripts" / "ci_drive_gate.py"


def _tree() -> ast.Module:
    return ast.parse(_GATE.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_the_drive_gate_imports_no_playwright_client():
    imported = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    bare = [m for m in imported if m == "playwright" or m.startswith("playwright.")]
    assert not bare, "the drive gate imports %s: a bare Playwright never tells the engine the road of the session" % bare


@pytest.mark.unit
def test_the_drive_gate_launches_with_the_wrapper_on_the_binary_under_test():
    calls = [n for n in ast.walk(_tree()) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", None)) == "InvisiblePlaywright"]
    assert len(calls) == 1, "the drive gate must launch with InvisiblePlaywright, once"
    keywords = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(keywords.get("binary_path"), ast.Name) and keywords["binary_path"].id == "exe", \
        "the binary under test is the gate's argument, handed over as binary_path="
    assert isinstance(keywords.get("headless"), ast.Constant) and keywords["headless"].value is True


@pytest.mark.unit
def test_the_drive_gate_keeps_no_copy_of_the_first_run_declaration():
    names = {t.id for n in ast.walk(_tree()) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert "FIRST_RUN" not in names, "the core declares the first run; a copy in the gate can only drift from it"
