"""The canvas ICC readback reference: tests/data/canvas_icc_ref/refs.json."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DATA = ROOT / "tests" / "data" / "canvas_icc_ref"
REFS = DATA / "refs.json"
SCRIPT = ROOT / "scripts" / "build_canvas_icc_refs.py"
HEX64 = re.compile(r"[0-9a-f]{64}")

IMAGES = ["iccp-displayp3", "iccp-adobergb", "iccp-rec2020", "iccp-prophoto", "iccp-srgb",
          "cicp-p3", "gama-chrm-p3", "srgb-chunk", "untagged", "jpeg-icc-displayp3"]
#: The images that carry a colour tag: qcms converts them to the output profile.
TAGGED = [i for i in IMAGES if i != "untagged"]
CONVERTED_PROBES = ("drawImage", "drawImage_scaled32", "bitmap_default")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _builder():
    return _load(SCRIPT, "build_canvas_icc_refs")


def _refs():
    return json.loads(REFS.read_text(encoding="utf-8"))


def test_the_reference_has_72_probes_with_a_hash_each():
    refs = _refs()
    probes = refs["probes"]
    assert refs["probe_count"] == len(probes) == 72
    kinds = {}
    for name, p in probes.items():
        assert HEX64.fullmatch(p["sha256"]), name
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    assert kinds == {"image": 40, "fill": 22, "colorspace": 1, "env": 9}
    for name in (f"{i} | {k}" for i in IMAGES
                 for k in ("drawImage", "drawImage_scaled32", "bitmap_default", "bitmap_none")):
        p = probes[name]
        assert p["bytes"] == (4 * 32 * 32 if name.endswith("scaled32") else 4 * 16 * 16)


def test_the_reference_names_the_retail_build_and_the_monitor_profile():
    refs = _refs()
    ff = refs["firefox"]
    assert ff["version"] == "159.0" and ff["buildid"] == "20261022150525"
    assert ff["exe_sha256"].startswith("d8b5ed82") and HEX64.fullmatch(ff["exe_sha256"])
    assert ff["prefs"]["gfx.color_management.force_srgb"] is False
    assert ff["prefs"]["gfx.color_management.display_profile"] == ""
    prof = refs["display_profile"]
    assert prof["file"] == "sRGB Color Space Profile.icm" and prof["size"] == 3144
    assert prof["sha256"] == "2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e"
    assert refs["source"]["capture"].endswith("canvas-color-icc-readback")


def test_the_27_probes_that_depend_on_the_profile_are_there_and_differ_from_no_conversion():
    """Without the conversion (force_srgb=true on the same machine, the bytes the engine
    gave until firefox-59) these 27 probes hash otherwise: the test compares them, so it
    fails on an engine that skips the conversion."""
    refs = _refs()
    probes = refs["probes"]
    want = sorted(f"{i} | {k}" for i in TAGGED for k in CONVERTED_PROBES)
    assert len(want) == 27
    assert refs["profile_dependent"] == want
    assert refs["profile_dependent_count"] == 27
    for name in want:
        p = probes[name]
        assert HEX64.fullmatch(p["without_conversion_sha256"]), name
        assert p["without_conversion_sha256"] != p["sha256"], name
    others = [n for n, p in probes.items() if "without_conversion_sha256" in p]
    assert sorted(others) == want, "no other probe depends on the profile"


def test_the_untagged_bitmap_none_and_fill_probes_do_not_depend_on_the_profile():
    probes = _refs()["probes"]
    for i in IMAGES:
        assert "without_conversion_sha256" not in probes[f"{i} | bitmap_none"]
    assert all("without_conversion_sha256" not in p for n, p in probes.items()
               if n.startswith("fill | "))
    # untagged images and bitmap_none are the same 16x16 gradient: one hash for them all
    plain = {probes[f"{i} | bitmap_none"]["sha256"] for i in IMAGES if i != "jpeg-icc-displayp3"}
    assert len(plain) == 1


def test_the_generator_gives_the_images_the_reference_names():
    refs = _refs()
    gen = _load(DATA / "gen_images.py", "gen_images")
    imgs, profiles = gen.build_images()
    assert [i[0] for i in imgs] == IMAGES == list(refs["images"])
    assert len(profiles) == 5
    for iid, ext, data, _what in imgs:
        want = refs["images"][iid]
        assert want["file"] == f"images/{iid}.{ext}"
        assert (len(data), hashlib.sha256(data).hexdigest()) == (want["bytes"], want["sha256"])
    assert [m["file"] for m in gen.images_json(imgs)] == [v["file"] for v in refs["images"].values()]


def test_the_page_reads_the_probes_the_reference_names():
    page = (DATA / "index.html").read_text(encoding="utf-8")
    for needle in ('drawImage_scaled32', "createImageBitmap(blob", "color-gamut:p3",
                   'fetch("images.json")', "p3_fill_p3_canvas"):
        assert needle in page, needle
    assert "http://" not in page and "https://" not in page, "the page loads nothing external"


def _run(force_srgb, **pixel):
    """A run json as run_color.py writes it, with the pixel hashes of ``pixel`` varying."""
    def img(prefix, tagged):
        e = {}
        for k in ("drawImage", "drawImage_scaled32", "bitmap_default", "bitmap_none"):
            conv = tagged and k != "bitmap_none" and not force_srgb
            e[k] = {"sha256": hashlib.sha256((prefix + k + ("c" if conv else "")).encode()).hexdigest()}
        return e
    return {
        "firefox": {"buildid": "1", "exe_sha256": "ab" * 32, "version": "159.0", "os": "Windows_NT"},
        "prefs_read_back": {"gfx.color_management.display_profile": "",
                            "gfx.color_management.force_srgb": force_srgb,
                            "gfx.color_management.mode": 2},
        "R": {"images": {"t": img("t", True), "u": img("u", False)},
              "fills": {"#336699": {"sha256": "11" * 32}},
              "canvas_colorspace": {"p3_fill_p3_canvas": {"sha256": "22" * 32}},
              "env": {"colorDepth": 24, "prefers-color-scheme:dark": True}},
    }


HOST = {"file": "x.icm", "size": 1, "sha256": "33" * 32}


def test_the_builder_keeps_the_no_conversion_hash_of_what_the_profile_changes():
    m = _builder()
    refs = m.build(_run(False), _run(True), _run(False), HOST, "cap")
    assert refs["profile_dependent"] == ["t | bitmap_default", "t | drawImage",
                                         "t | drawImage_scaled32"]
    assert refs["probe_count"] == 4 + 4 + 1 + 1 + 2
    p = refs["probes"]["t | drawImage"]
    assert p["without_conversion_sha256"] == _run(True)["R"]["images"]["t"]["drawImage"]["sha256"]
    assert refs["probes"]["env | prefers-color-scheme:dark"]["compared"] is False
    assert refs["probes"]["env | colorDepth"]["value"] == 24


def test_a_repeat_run_with_other_hashes_is_refused():
    m = _builder()
    other = _run(False)
    other["R"]["fills"]["#336699"]["sha256"] = "44" * 32
    with pytest.raises(m.RefError, match="repeat run differs"):
        m.build(_run(False), _run(True), other, HOST, "cap")


def test_runs_of_other_builds_or_prefs_are_refused():
    m = _builder()
    b = _run(True)
    b["firefox"]["buildid"] = "2"
    with pytest.raises(m.RefError, match="buildid"):
        m.build(_run(False), b, _run(False), HOST, "cap")
    with pytest.raises(m.RefError, match="force_srgb=true"):
        m.build(_run(False), _run(False), _run(False), HOST, "cap")
    a = _run(False)
    a["prefs_read_back"]["gfx.color_management.display_profile"] = "/p3.icc"
    with pytest.raises(m.RefError, match="display_profile"):
        m.build(a, _run(True), copy.deepcopy(a), HOST, "cap")
    b = _run(True)
    b["prefs_read_back"]["gfx.color_management.mode"] = 1
    with pytest.raises(m.RefError, match="mode"):
        m.build(_run(False), b, _run(False), HOST, "cap")
