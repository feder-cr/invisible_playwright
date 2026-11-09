"""scripts/compare_canvas_text_runs.py names the cases two runs draw differently."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "compare_canvas_text_runs.py"


def _module():
    spec = importlib.util.spec_from_file_location("compare_canvas_text_runs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_equal_runs_have_nothing_to_report():
    m = _module()
    assert m.compare({"a": "1", "b": "2"}, {"b": "2", "a": "1"}) == ([], [], [])


def test_a_differing_and_a_missing_case_are_named():
    m = _module()
    only_a, only_b, differ = m.compare({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "9", "d": "4"})
    assert (only_a, only_b, differ) == (["c"], ["d"], ["b"])


def test_a_reference_file_loads_as_case_hashes(tmp_path):
    m = _module()
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"cases": {"x": {"sha256": "ab", "width": 3}}}), encoding="utf-8")
    assert m.load(str(p)) == {"x": "ab"}
