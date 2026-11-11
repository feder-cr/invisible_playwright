"""Coloured canvas text, and text under other ClearType keys, is what retail Windows draws.

tests/test_canvas_text_is_windows_e2e.py compares 342 cases of black text on white, drawn
with one ClearType state (the seed's: gamma 1.8, contrast 1.0). The text of a page is not
always black, and a machine's ClearType gamma and contrast are not always the untuned ones
(the registry values the Windows tuner writes), so this test adds what that one does not
reach, each with the hashes retail Firefox 159.0 on Windows gave for the same page:

  coloured text   tests/data/canvas_text_ref_colors/index.html draws Arial 16 and 33 px, fillText,
                  of seven colour pairs (black, teal, rgba(102,204,0,0.2) on white, grey, white on
                  black, red, white on blue): 14 cases, with the seed's configuration. An
                  engine that quantises its A8 text masks to 17 levels (a hook, which firefox-64
                  removes) moves a coloured edge pixel by a few units, and black on white hides
                  it: 12 of the 14 differ on such an engine.
  ClearType keys  gamma/contrast 1800/100 (the seed's), 1400/50 and 2200/0: 39 cases for each,
                  the 14 coloured ones and 25 black ones of tests/data/canvas_text_ref (Arial,
                  Calibri, Consolas, Segoe UI at 9, 12.5, 16 and 33 px; stroke, opaque canvas
                  above 48 px, rotated and scaled, Twemoji COLR v0). The engine is launched with
                  the pins font.cleartype_gamma and font.cleartype_contrast of the key, one
                  launch for each; the page is the same, the bytes are not (the keys give
                  different hashes in refs.json, which tests/unit/test_canvas_text_colors_refs.py
                  checks, so the test tells them apart).

Each canvas is read back with getImageData and its RGBA bytes are hashed with SHA-256. The
fonts are the engine's own bundled files, served by this test (their sha256 is checked
against the reference first). tests/data/canvas_text_ref_colors/refs.json is built by
scripts/build_canvas_text_colors_refs.py from the bytes retail drew.

Every differing case is listed (name, expected hash, hash got), not just the first.

Run: pytest tests/test_canvas_text_colors_and_cleartype_is_windows_e2e.py -m e2e -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _canvas_text import DATA as BASE_DATA, diff_lines, only_cases, pin, serve, wrong_fonts
from invisible_playwright import InvisiblePlaywright

DATA = Path(__file__).parent / "data" / "canvas_text_ref_colors"
SEED = 21


def _refs():
    path = DATA / "refs.json"
    if not path.is_file():
        pytest.fail("tests/data/canvas_text_ref_colors/refs.json is missing: it is the reference "
                    "retail Firefox on Windows wrote for these pages (see the module docstring)")
    return json.loads(path.read_text(encoding="utf-8"))


def _draw(url, page):
    """The sha256 of each case the page at ``url`` draws, by case id."""
    page.goto(url, timeout=60_000)
    page.wait_for_function("window.DONE === true || window.ERR", timeout=300_000)
    err = page.evaluate("window.ERR || null")
    assert err is None, "the reference page %s failed: %s" % (url, err)
    return page.evaluate(
        "Object.fromEntries(Object.entries(window.R).map(([k, v]) => [k, v.sha256]))")


def _launch(firefox_binary, refs, key, pages_for):
    """One engine launch with the persona's pin (and the ClearType ``key`` when given, a
    (gamma, contrast) pair); returns what each page of ``pages_for`` ({name: (path, ids)})
    drew."""
    base = json.loads((BASE_DATA / "refs.json").read_text(encoding="utf-8"))
    fonts_dir = Path(firefox_binary).parent / "fonts"
    wrong = wrong_fonts(fonts_dir, base["fonts"])
    assert not wrong, (
        "the reference was measured with other font files than the engine's bundle, so it "
        "compares nothing; rebuild the references from the bundled files:\n  "
        + "\n  ".join(wrong))

    prefs = pin(base)
    if key is not None:
        prefs["font.cleartype_gamma"], prefs["font.cleartype_contrast"] = key
    colors_page = (DATA / "index.html").read_bytes()
    black_page = only_cases((BASE_DATA / "index.html").read_text(encoding="utf-8"),
                            pages_for.get("black", []))
    server = serve(fonts_dir, base["fonts"],
                   {"/": colors_page, "/index.html": colors_page,
                    "/black.html": black_page.encode("utf-8")})
    try:
        root = "http://127.0.0.1:%d" % server.server_address[1]
        with InvisiblePlaywright(seed=SEED, binary_path=firefox_binary, headless=True,
                                 pin=prefs) as b:
            page = b.new_context().new_page()
            got = {}
            if pages_for.get("black"):
                got["black"] = _draw(root + "/black.html", page)
            got["colors"] = _draw(root + "/", page)
            return got
    finally:
        server.shutdown()


def _want(cases):
    return {cid: c["sha256"] for cid, c in cases.items()}


@pytest.mark.e2e
@pytest.mark.timeout(600, method="thread")
def test_coloured_text_is_what_retail_windows_draws(firefox_binary, engine_at_least):
    engine_at_least("firefox-64")
    refs = _refs()
    assert refs["colors"]["count"] == len(refs["colors"]["cases"]) == 14
    got = _launch(firefox_binary, refs, None, {})
    lines = diff_lines(_want(refs["colors"]["cases"]), got["colors"])
    assert not lines, (
        "%d of %d coloured canvas text cases differ from retail Firefox on Windows "
        "(seed ClearType state, gamma 1800 contrast 100):\n  %s"
        % (len(lines), len(refs["colors"]["cases"]), "\n  ".join(lines)))


def _keys():
    # collected without the reference file: a missing one fails the test, not the collection
    path = DATA / "refs.json"
    if not path.is_file():
        return ["1800_100", "1400_50", "2200_0"]
    return list(json.loads(path.read_text(encoding="utf-8"))["keys"])


@pytest.mark.e2e
@pytest.mark.timeout(900, method="thread")
@pytest.mark.parametrize("tag", _keys())
def test_text_under_a_cleartype_key_is_what_retail_windows_draws(
        tag, firefox_binary, engine_at_least):
    engine_at_least("firefox-64")
    refs = _refs()
    k = refs["keys"][tag]
    want_black, want_colors = _want(k["black"]), _want(k["colors"])
    assert len(want_black) + len(want_colors) == k["count"] == 39

    got = _launch(firefox_binary, refs, (k["gamma"], k["contrast"]), {"black": list(want_black)})
    lines = (diff_lines(want_black, got["black"], "black ")
             + diff_lines(want_colors, got["colors"], "colour "))
    assert not lines, (
        "%d of %d canvas text cases differ from retail Firefox on Windows under ClearType "
        "gamma %d contrast %d:\n  %s"
        % (len(lines), k["count"], k["gamma"], k["contrast"], "\n  ".join(lines)))
