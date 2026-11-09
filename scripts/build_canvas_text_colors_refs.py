"""Builds tests/data/canvas_text_ref_colors/refs.json from the bytes retail Firefox drew.

The reference of tests/test_canvas_text_colors_and_cleartype_is_windows_e2e.py: coloured
canvas text, and 39 cases (25 of black text on white, 14 coloured) under three ClearType
keys, all measured on retail Firefox 159.0 on Windows with tests/data/canvas_text_ref_colors/
index.html (the 14 coloured cases) and tests/data/canvas_text_ref/index.html (the 25 black
ones). The measuring harness saved, for every case, the RGBA bytes of the canvas
(<name>.rgba) and a <name>.json with its id, width, height and sha256:

    <BYTES>/colors/retail/<name>.{json,rgba}          the 14 coloured cases, retail's own
                                                       ClearType state (the seed's)
    <BYTES>/keys/<gamma>_<contrast>/retail/<name>.{json,rgba}
                                                       the 39 cases under that key; the case
                                                       id of a coloured one is "color:<id>"

    python -I scripts/build_canvas_text_colors_refs.py --bytes <BYTES> \
        [--keys 1800_100,1400_50,2200_0] [--out tests/data/canvas_text_ref_colors/refs.json]

The repository holds no .rgba: they are 270 kB each. The script hashes an .rgba when the
directory has it (and stops if that is not the sha256 its .json says, or the size is not
width x height x 4); when only the .json is there (a byte file kept only where the engine
differed) it takes the hash the harness wrote. It stops too when the black cases under
1800/100 are not the ones tests/data/canvas_text_ref/refs.json holds for the same ids, or
when the coloured cases under 1800/100 differ from the ones of colors/retail (same machine,
same key: they are one measurement). Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data" / "canvas_text_ref_colors"
BASE_REFS = ROOT / "tests" / "data" / "canvas_text_ref" / "refs.json"
DEFAULT_KEYS = "1800_100,1400_50,2200_0"
SEED_KEY = "1800_100"
DATE = "2026-11-09"
#: The prefs of the retail profile for the keys (user.js), gamma and contrast per key.
RETAIL_PREFS = {
    "gfx.canvas.accelerated": False,
    "gfx.font_rendering.cleartype_params.gamma": "<gamma>",
    "gfx.font_rendering.cleartype_params.enhanced_contrast": "<contrast>",
    "gfx.font_rendering.cleartype_params.cleartype_level": 100,
    "gfx.font_rendering.cleartype_params.pixel_structure": 1,
    "gfx.font_rendering.cleartype_params.rendering_mode": 0,
}
#: The coloured cases of colors/retail were measured with the machine's own ClearType prefs,
#: and gfx.canvas.accelerated off.
COLORS_RETAIL_PREFS = {"gfx.canvas.accelerated": False}


class BuildError(ValueError):
    """The saved bytes cannot be this reference."""


def colors_of_page(html):
    """[[name, fg, bg], ...] and [sizes] from the measuring page, as the page declares them."""
    m = re.search(r"const COLORS = (\[\[.*?\]\]);", html, re.S)
    s = re.search(r"const SIZES = (\[[^\]]*\]);", html)
    if not m or not s:
        raise BuildError("index.html declares no COLORS / SIZES")
    return json.loads(m.group(1)), json.loads(s.group(1))


def read_dir(d):
    """{case id: {"width", "height", "sha256"}} of the .json files of one directory."""
    d = Path(d)
    if not d.is_dir():
        raise BuildError("%s is not a directory" % d)
    out = {}
    for j in sorted(d.glob("*.json")):
        m = json.loads(j.read_text(encoding="utf-8"))
        sha, w, h = m["sha256"], int(m["w"]), int(m["h"])
        rgba = j.with_suffix(".rgba")
        if rgba.is_file():
            data = rgba.read_bytes()
            if len(data) != w * h * 4:
                raise BuildError("%s: %d bytes, not %d x %d x 4" % (rgba, len(data), w, h))
            got = hashlib.sha256(data).hexdigest()
            if got != sha:
                raise BuildError("%s: sha256 %s, its json says %s" % (rgba, got, sha))
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise BuildError("%s: sha256 %r is not 64 hex digits" % (j, sha))
        if m["id"] in out:
            raise BuildError("%s: case %r twice in %s" % (j.name, m["id"], d))
        out[m["id"]] = {"width": w, "height": h, "sha256": sha}
    return out


def build(bytes_dir, keys, page_html, base):
    colors, sizes = colors_of_page(page_html)
    color_ids = ["%s|%s" % (c[0], s) for c in colors for s in sizes]
    bytes_dir = Path(bytes_dir)
    seed = read_dir(bytes_dir / "colors" / "retail")
    if sorted(seed) != sorted(color_ids):
        raise BuildError("colors/retail has %s, the page draws %s" % (sorted(seed), sorted(color_ids)))
    spec = {"%s|%s" % (c[0], s): {"name": c[0], "fg": c[1], "bg": c[2], "size": s}
            for c in colors for s in sizes}
    out_colors = {i: {"id": i, **spec[i], **seed[i]} for i in color_ids}

    out_keys = {}
    for tag in keys:
        gamma, contrast = (int(x) for x in tag.split("_"))
        cases = read_dir(bytes_dir / "keys" / tag / "retail")
        black = {i: v for i, v in cases.items() if not i.startswith("color:")}
        col = {i[len("color:"):]: v for i, v in cases.items() if i.startswith("color:")}
        if sorted(col) != sorted(color_ids):
            raise BuildError("key %s: coloured cases %s, the page draws %s"
                             % (tag, sorted(col), sorted(color_ids)))
        for i in black:
            if i not in base["cases"]:
                raise BuildError("key %s: %r is not a case of canvas_text_ref" % (tag, i))
        out_keys[tag] = {
            "gamma": gamma, "contrast": contrast, "count": len(black) + len(col),
            "black": {i: {"id": i, **black[i]} for i in sorted(black)},
            "colors": {i: {"id": i, **col[i]} for i in color_ids},
        }
    if SEED_KEY in out_keys:
        k = out_keys[SEED_KEY]
        for i, v in k["black"].items():
            if v["sha256"] != base["cases"][i]["sha256"]:
                raise BuildError("%s under %s: %s, canvas_text_ref/refs.json has %s"
                                 % (i, SEED_KEY, v["sha256"], base["cases"][i]["sha256"]))
        for i, v in k["colors"].items():
            if v["sha256"] != out_colors[i]["sha256"]:
                raise BuildError("%s under %s differs from colors/retail: %s vs %s"
                                 % (i, SEED_KEY, v["sha256"], out_colors[i]["sha256"]))
    arial = [f for f in base["fonts"] if f["family"] == "Arial"]
    if len(arial) != 1:
        raise BuildError("canvas_text_ref/refs.json has no single Arial entry")
    return {
        "test_page": "index.html",
        "black_page": "../canvas_text_ref/index.html",
        "text": base["text"],
        "firefox": base["firefox"],
        "canvas": base["canvas"],
        "cleartype": base["cleartype"],
        "fonts": arial,
        "capture": {
            "date": DATE,
            "origin": "retail Firefox 159.0 (build %s, firefox.exe sha256 %s) on Windows, "
                      "headless, canvases read back with getImageData; bytes saved by the "
                      "measuring harness: colors/retail (the 14 coloured cases) and "
                      "keys/<gamma>_<contrast>/retail (39 cases per key) of the win-asset "
                      "analysis of 2026-11-09"
                      % (base["firefox"]["buildid"], base["firefox"]["exe_sha256"]),
            "colors_prefs": COLORS_RETAIL_PREFS,
            "keys_prefs": RETAIL_PREFS,
        },
        "colors": {"count": len(out_colors), "cases": out_colors},
        "keys": out_keys,
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bytes", required=True, metavar="DIR",
                    help="directory with colors/retail and keys/<gamma>_<contrast>/retail")
    ap.add_argument("--keys", default=DEFAULT_KEYS,
                    help="ClearType keys, gamma_contrast, comma separated (default %(default)s)")
    ap.add_argument("--out", default=str(DATA / "refs.json"))
    a = ap.parse_args(argv[1:])
    keys = [k for k in a.keys.split(",") if k]
    try:
        refs = build(a.bytes, keys, (DATA / "index.html").read_text(encoding="utf-8"),
                     json.loads(BASE_REFS.read_text(encoding="utf-8")))
    except (BuildError, KeyError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    Path(a.out).write_text(json.dumps(refs, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8", newline="\n")
    print("%d coloured cases, %d keys x %d cases -> %s"
          % (refs["colors"]["count"], len(refs["keys"]),
             next(iter(refs["keys"].values()))["count"], a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
