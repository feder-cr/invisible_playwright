"""The reference of the coloured text and ClearType key e2e: tests/data/canvas_text_ref_colors."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DATA = ROOT / "tests" / "data" / "canvas_text_ref_colors"
REFS = DATA / "refs.json"
PAGE = DATA / "index.html"
BASE = ROOT / "tests" / "data" / "canvas_text_ref"
SCRIPT = ROOT / "scripts" / "build_canvas_text_colors_refs.py"
HEX = re.compile(r"[0-9a-f]{64}")
KEYS = {"1800_100": (1800, 100), "1400_50": (1400, 50), "2200_0": (2200, 0)}
NAMES = ["black", "teal", "limealpha", "grey", "white_on_black", "red", "white_on_blue"]
COLOR_IDS = sorted("%s|%s" % (n, s) for n in NAMES for s in (16, 33))


def _refs():
    return json.loads(REFS.read_text(encoding="utf-8"))


def _base():
    return json.loads((BASE / "refs.json").read_text(encoding="utf-8"))


def _helper():
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import _canvas_text
        return _canvas_text
    finally:
        sys.path.remove(str(ROOT / "tests"))


def _builder():
    spec = importlib.util.spec_from_file_location("build_canvas_text_colors_refs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_reference_says_where_it_comes_from():
    refs = _refs()
    ff = refs["firefox"]
    assert ff["version"] == "159.0" and ff["buildid"] == "20261022150525"
    assert ff["exe_sha256"].startswith("d8b5ed82") and HEX.fullmatch(ff["exe_sha256"])
    base = _base()
    assert ff == base["firefox"] and refs["cleartype"] == base["cleartype"]
    cap = refs["capture"]
    assert cap["date"] == "2026-11-09" and "win-asset" in cap["origin"]
    assert cap["keys_prefs"]["gfx.canvas.accelerated"] is False
    assert set(cap["keys_prefs"]) >= {"gfx.font_rendering.cleartype_params.gamma",
                                      "gfx.font_rendering.cleartype_params.enhanced_contrast"}
    assert refs["test_page"] == "index.html"
    assert refs["black_page"].endswith("canvas_text_ref/index.html")


def test_there_are_fourteen_coloured_cases_each_with_its_colours_and_hash():
    colors = _refs()["colors"]
    assert colors["count"] == len(colors["cases"]) == 14
    assert sorted(colors["cases"]) == COLOR_IDS
    page = PAGE.read_text(encoding="utf-8")
    for cid, c in colors["cases"].items():
        assert c["id"] == cid
        assert HEX.fullmatch(c["sha256"])
        assert (c["width"], c["height"]) == (420, 160)
        assert '"%s"' % c["fg"] in page and '"%s"' % c["bg"] in page, cid
    assert len({c["sha256"] for c in colors["cases"].values()}) == 14


def test_there_are_three_cleartype_keys_of_thirty_nine_cases():
    keys = _refs()["keys"]
    assert set(keys) == set(KEYS)
    base = _base()["cases"]
    for tag, k in keys.items():
        assert (k["gamma"], k["contrast"]) == KEYS[tag]
        assert k["count"] == len(k["black"]) + len(k["colors"]) == 39
        assert len(k["black"]) == 25 and sorted(k["colors"]) == COLOR_IDS
        for cid, c in {**k["black"], **k["colors"]}.items():
            assert HEX.fullmatch(c["sha256"]) and c["width"] > 0 and c["height"] > 0, (tag, cid)
        for cid in k["black"]:
            assert cid in base, "a black case is a case of canvas_text_ref: %s" % cid


def test_the_seed_key_is_the_seed_reference():
    refs = _refs()
    base = _base()["cases"]
    seed = refs["keys"]["1800_100"]
    assert ({i: c["sha256"] for i, c in seed["colors"].items()}
            == {i: c["sha256"] for i, c in refs["colors"]["cases"].items()})
    assert ({i: c["sha256"] for i, c in seed["black"].items()}
            == {i: base[i]["sha256"] for i in seed["black"]})


def test_the_keys_tell_each_other_apart():
    """If the keys gave the same hashes the test would compare nothing of ClearType."""
    keys = _refs()["keys"]
    tags = sorted(keys)
    for a in tags:
        for b in tags:
            if a >= b:
                continue
            black = [i for i in keys[a]["black"]
                     if keys[a]["black"][i]["sha256"] != keys[b]["black"][i]["sha256"]]
            colour = [i for i in keys[a]["colors"]
                      if keys[a]["colors"][i]["sha256"] != keys[b]["colors"][i]["sha256"]]
            assert black, "%s and %s give the same hash for every black case" % (a, b)
            assert colour, "%s and %s give the same hash for every coloured case" % (a, b)


def test_the_page_draws_the_ids_of_the_reference():
    html = PAGE.read_text(encoding="utf-8")
    colors, sizes = _builder().colors_of_page(html)
    assert sorted("%s|%s" % (c[0], s) for c in colors for s in sizes) == COLOR_IDS
    assert 'url("bundle/arial7_04.ttf")' in html


def _write(d, cid, data, sha=None):
    d.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._=-]+", "_", cid)
    (d / (name + ".rgba")).write_bytes(data)
    (d / (name + ".json")).write_text(json.dumps(
        {"id": cid, "w": 2, "h": 1, "sha256": sha or hashlib.sha256(data).hexdigest()}))


def _eight(cid):
    return cid.encode().ljust(8, b"-")[:8]


def test_the_builder_hashes_the_bytes_and_refuses_a_wrong_hash(tmp_path):
    mod = _builder()
    base = _base()
    refs = _refs()
    tag = "1400_50"
    for cid in refs["colors"]["cases"]:
        _write(tmp_path / "colors" / "retail", cid, _eight(cid))
    k = refs["keys"][tag]
    for cid in k["black"]:
        _write(tmp_path / "keys" / tag / "retail", cid, _eight("b" + cid))
    for cid in k["colors"]:
        _write(tmp_path / "keys" / tag / "retail", "color:" + cid, _eight(cid))
    page = PAGE.read_text(encoding="utf-8")
    out = mod.build(tmp_path, [tag], page, base)
    assert out["keys"][tag]["count"] == 39 and out["colors"]["count"] == 14
    # a byte file that is not what its json says stops the build
    _write(tmp_path / "colors" / "retail", "black|16", b"12345678", sha="0" * 64)
    with pytest.raises(mod.BuildError, match="its json says"):
        mod.build(tmp_path, [tag], page, base)


def test_the_helper_narrows_the_page_and_lists_the_differences():
    h = _helper()
    page = (BASE / "index.html").read_text(encoding="utf-8")
    narrowed = h.only_cases(page, ["Arial|16|none|normal|normal|fill"])
    assert "ONLY.has(s.id)" in narrowed and "Arial|16|none|normal|normal|fill" in narrowed
    with pytest.raises(AssertionError):
        h.only_cases("<html></html>", ["x"])
    want = {"a": "1" * 64, "b": "2" * 64, "c": "3" * 64}
    got = {"a": "1" * 64, "b": "9" * 64, "d": "4" * 64}
    lines = h.diff_lines(want, got, "k ")
    assert lines == ["k b: expected %s, got %s" % ("2" * 64, "9" * 64),
                     "k c: expected %s, got not drawn" % ("3" * 64),
                     "k d: drawn but not in the reference"]
    assert h.diff_lines(want, dict(want)) == []
