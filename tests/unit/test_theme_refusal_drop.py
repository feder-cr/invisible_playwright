"""The helper of the missing-system-theme-pref e2e removes exactly the prefs it is told to.

Its first regex had an unterminated character set (a lone backslash before the closing
bracket) and every parametrised case of the e2e died with re.error before launching a browser
(candidate 42624473465's Windows gate). This runs the helper on a user.js of its own.

Known-bad input: the regex as it was, which raises re.error on the first line.
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

E2E = Path(__file__).resolve().parents[1] / "test_a_missing_system_theme_pref_refuses_the_launch_e2e.py"
BACKSLASH = chr(92)
NEWLINE = chr(10)


def _helper():
    spec = importlib.util.spec_from_file_location("theme_refusal_e2e", E2E)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._drop


def _line(name, value):
    return "user_pref(%s, %s);" % (json.dumps(name), json.dumps(value))


def test_drop_removes_the_named_pref_and_nothing_else(tmp_path):
    user_js = tmp_path / "user.js"
    lines = [
        _line("ui.highlight", "rgb(1, 2, 3)"),
        _line("ui.highlight.dark", "rgb(4, 5, 6)"),
        _line("ui.-moz-field", "rgb(7, 8, 9)"),
        _line("ui.font.menu", "Segoe UI"),
        _line("ui.font.menu.size", "12"),
        _line("browser.path", "C:" + BACKSLASH + "Users" + BACKSLASH + "x"),
        _line("a.quote", 'say "hi"'),
    ]
    user_js.write_text(NEWLINE.join(lines) + NEWLINE, encoding="utf-8")
    drop = _helper()
    assert drop(user_js, {"ui.highlight"}) == 1
    left = user_js.read_text(encoding="utf-8")
    assert json.dumps("ui.highlight") not in left
    for kept in ("ui.highlight.dark", "ui.-moz-field", "ui.font.menu", "ui.font.menu.size",
                 "browser.path", "a.quote"):
        assert json.dumps(kept) in left, kept


def test_drop_leaves_a_pref_that_is_not_there_alone(tmp_path):
    user_js = tmp_path / "user.js"
    user_js.write_text(_line("a.b", 1) + NEWLINE, encoding="utf-8")
    assert _helper()(user_js, {"c.d"}) == 0


def test_the_refusal_is_not_expected_on_windows(monkeypatch):
    """Windows does not refuse a missing ui.* pref (the engine returns unless it is GTK), so the e2e that
    expects a refusal must skip there: candidate 42673795394's Windows gate ran it and got DID NOT RAISE
    three times. Known-bad input: the test without the skip mark."""
    monkeypatch.setattr("sys.platform", "win32")
    spec = importlib.util.spec_from_file_location("theme_refusal_e2e_win", E2E)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marks = module.test_a_missing_pref_refuses_the_launch.pytestmark
    skips = [m for m in marks if m.name == "skipif"]
    assert skips and skips[0].args[0] is True
    control = getattr(module.test_every_pref_declared_the_launch_succeeds, "pytestmark", [])
    assert not [m for m in control if m.name == "skipif"], "the control launch must still run on Windows"
