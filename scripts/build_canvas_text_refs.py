"""Builds tests/data/canvas_text_ref/refs.json, the one reference of the canvas text test.

Two measurements of retail Firefox 159.0 on Windows, made with the same page code, the same
bundled font files and the same machine, are merged by case id:

    python -I scripts/build_canvas_text_refs.py <canvas_ref_bundle/refs.json> \
        <canvas_ref_ext/refs.json> [--out tests/data/canvas_text_ref/refs.json]

The bundle file has the base cases (no ``gruppo`` field: they become group ``0_base``), the
extension file has the cases of the other groups. A case id that is in both files is kept
once, with the extension's group; the script stops if the two hashes of such a case differ,
or if the two files were not measured on the same Firefox build, with the same ClearType
state and the same font files. Fields that name a file of the measuring machine (the PNG
paths) are dropped: the repository holds no PNG and no font.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_GROUP = "0_base"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests" / "data" / "canvas_text_ref" / "refs.json"
_DROP = ("png",)


class MergeError(ValueError):
    """The two measurements cannot be one reference."""


def _case(case, group):
    out = {k: v for k, v in case.items() if k not in _DROP}
    out.setdefault("alpha", True)
    out.setdefault("stretch", "normal")
    out.setdefault("text", "TEXT")
    out["gruppo"] = group
    return out


def merge(bundle, ext):
    """One reference from the bundle measurement and the extension measurement."""
    for key in ("firefox", "canvas", "cleartype", "text"):
        if bundle[key] != ext[key]:
            raise MergeError("the two measurements differ in %r: %r vs %r"
                             % (key, bundle[key], ext[key]))
    ext_fonts = {f["bundle_file"]: f for f in ext["fonts"]}
    for f in bundle["fonts"]:
        other = ext_fonts.get(f["bundle_file"])
        if other is None or other["sha256"] != f["sha256"]:
            raise MergeError("font %s is not the same file in the two measurements"
                             % f["bundle_file"])

    cases = {}
    for cid, c in bundle["cases"].items():
        cases[cid] = _case(c, c.get("gruppo") or BASE_GROUP)
    for cid, c in ext["cases"].items():
        if not c.get("gruppo"):
            raise MergeError("extension case %r has no group" % cid)
        if cid in cases and cases[cid]["sha256"] != c["sha256"]:
            raise MergeError("case %r has two different hashes: %s and %s"
                             % (cid, cases[cid]["sha256"], c["sha256"]))
        cases[cid] = _case(c, c["gruppo"])

    groups = {}
    for c in cases.values():
        groups[c["gruppo"]] = groups.get(c["gruppo"], 0) + 1
    status = {**bundle.get("font_load_status", {}), **ext.get("font_load_status", {})}
    return {
        "test_page": "index.html",
        "font_source": "bundle",
        "text": bundle["text"],
        "firefox": bundle["firefox"],
        "canvas": bundle["canvas"],
        "cleartype": bundle["cleartype"],
        "fonts": ext["fonts"],
        "font_load_status": status,
        "case_count": len(cases),
        "groups": dict(sorted(groups.items())),
        "cases": cases,
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", help="refs.json of the base measurement (canvas_ref_bundle)")
    ap.add_argument("ext", help="refs.json of the extension measurement (canvas_ref_ext)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv[1:])
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    try:
        refs = merge(load(a.bundle), load(a.ext))
    except MergeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    Path(a.out).write_text(json.dumps(refs, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print("%d cases -> %s" % (refs["case_count"], a.out))
    for g, n in refs["groups"].items():
        print("  %-30s %3d" % (g, n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
