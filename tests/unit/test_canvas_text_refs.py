"""The merged canvas text reference: tests/data/canvas_text_ref/refs.json."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
REFS = ROOT / "tests" / "data" / "canvas_text_ref" / "refs.json"
PAGE = ROOT / "tests" / "data" / "canvas_text_ref" / "index.html"
SCRIPT = ROOT / "scripts" / "build_canvas_text_refs.py"

GROUPS = {"0_base": 211, "1_alpha_false": 25, "2a_rot_alpha_true_controllo": 25,
          "2b_rot_alpha_false": 25, "3_emoji_colr0": 12, "4_soglie": 20,
          "5_variabile_bahnschrift": 12, "6_variabile_seguivar": 12}


def _builder():
    spec = importlib.util.spec_from_file_location("build_canvas_text_refs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refs():
    return json.loads(REFS.read_text(encoding="utf-8"))


def test_the_merged_reference_has_every_case_once_in_its_group():
    refs = _refs()
    cases = refs["cases"]
    assert refs["case_count"] == len(cases) == sum(GROUPS.values()) == 342
    assert refs["groups"] == GROUPS
    got = {}
    for cid, c in cases.items():
        assert c["id"] == cid, "the key and the id of a case are one"
        assert len(c["sha256"]) == 64
        got[c["gruppo"]] = got.get(c["gruppo"], 0) + 1
    assert got == GROUPS
    assert len(cases) == len({c["id"] for c in cases.values()})
    assert not any("png" in c for c in cases.values()), "no PNG path: the repository holds none"


def test_every_font_the_page_loads_is_named_with_its_hash():
    refs = _refs()
    files = {f["bundle_file"]: f for f in refs["fonts"]}
    assert {"TwemojiMozilla.ttf", "bahnschrift2_07.ttf", "seguivar2_03.ttf"} <= set(files)
    assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in files.values())
    page = PAGE.read_text(encoding="utf-8")
    assert set(re.findall(r'url\("bundle/([^"]+)"\)', page)) == set(files)
    families = {c["family"] for c in refs["cases"].values()}
    assert families <= {f["family"] for f in refs["fonts"]}


def test_the_alpha_false_cases_say_so_and_the_page_opens_them_so():
    cases = _refs()["cases"]
    opaque = [c for c in cases.values() if c["gruppo"] in ("1_alpha_false", "2b_rot_alpha_false")]
    assert opaque and all(c["alpha"] is False and c["id"].endswith("|alpha=false") for c in opaque)
    assert 'getContext("2d", {alpha: sp.alpha' in PAGE.read_text(encoding="utf-8")


def _two(**over):
    base = {"text": "T", "firefox": {"v": 1}, "canvas": {}, "cleartype": {"a": 1},
            "fonts": [{"bundle_file": "a.ttf", "sha256": "11"}],
            "cases": {"x": {"id": "x", "sha256": "aa", "png": "cases/x.png"}}}
    ext = {**base, "cases": {"x": {"id": "x", "sha256": "aa", "gruppo": "2a"},
                             "y": {"id": "y", "sha256": "bb", "gruppo": "5"}}}
    ext.update(over)
    return base, ext


def test_a_shared_case_is_kept_once_with_the_extension_group():
    m = _builder()
    refs = m.merge(*_two())
    assert refs["case_count"] == 2
    assert refs["cases"]["x"]["gruppo"] == "2a" and "png" not in refs["cases"]["x"]
    assert refs["groups"] == {"2a": 1, "5": 1}


def test_two_hashes_for_one_case_are_refused():
    m = _builder()
    b, e = _two()
    e["cases"]["x"]["sha256"] = "zz"
    with pytest.raises(m.MergeError, match="two different hashes"):
        m.merge(b, e)


def test_two_measurements_of_different_machines_are_refused():
    m = _builder()
    b, e = _two(firefox={"v": 2})
    with pytest.raises(m.MergeError, match="firefox"):
        m.merge(b, e)


def test_the_variable_cases_have_three_distinct_weights_at_every_size():
    """A reference whose three weights hash alike proves nothing about the axes."""
    cases = _refs()["cases"]
    for group, family in (("5_variabile_bahnschrift", "Bahnschrift"),
                          ("6_variabile_seguivar", "Segoe UI Variable")):
        by = {}
        for c in cases.values():
            if c["gruppo"] == group:
                by.setdefault((c["size"], c["stretch"]), {})[c["weight"]] = c["sha256"]
        assert by and all(len(w) == 3 and len(set(w.values())) == 3 for w in by.values()), family
        assert all(c["family"] == family for c in cases.values() if c["gruppo"] == group)
