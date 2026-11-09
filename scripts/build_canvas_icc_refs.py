"""Builds tests/data/canvas_icc_ref/refs.json, the reference of the canvas ICC readback test.

One measurement of retail Firefox 159.0 on Windows with the default prefs (the run
``a_win_default``: qcms converts a tagged image to the monitor profile, here the stock
"sRGB Color Space Profile.icm"), and one control run on the same machine with
gfx.color_management.force_srgb=true (``b_win_force_srgb``: the bytes without that
conversion), both made by index.html and the images of gen_images.py:

    python -I scripts/build_canvas_icc_refs.py <a_win_default.json> <b_win_force_srgb.json> \
        --repeat <a2_win_default_repeat.json> --host-profile <host_display_profile.json> \
        --source <name of the capture> [--out tests/data/canvas_icc_ref/refs.json]

The script stops if the repeat run has other hashes than the reference run, if the runs
were not made on the same Firefox build, or if the prefs of the two runs are not the default
and force_srgb=true. The probes are the 72 of the page: for each of the 10 images drawImage,
drawImage at 32 px, createImageBitmap with the default colour space conversion and with
``none``; the 22 fill colours; the canvas colorSpace probe; the 9 environment values.

Standard library only: it does not import invisible_core, and the repository holds no PNG
and no run's pixel hex, only hashes and sizes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests" / "data" / "canvas_icc_ref" / "refs.json"
IMAGE_PROBES = ("drawImage", "drawImage_scaled32", "bitmap_default", "bitmap_none")
#: Value probes that are not colour management: the host's theme, which the retail machine
#: had dark; the test does not compare it (the persona's colour scheme is checked elsewhere).
NOT_COMPARED = ("env | prefers-color-scheme:dark",)


GEN_IMAGES = DEFAULT_OUT.parent / "gen_images.py"


def image_files():
    """{image id: {file, bytes, sha256}} of the ten images gen_images.py writes: the reference
    names them, so a generator that drifts is seen before the engine is blamed."""
    spec = importlib.util.spec_from_file_location("gen_images", GEN_IMAGES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    imgs, _profiles = mod.build_images()
    return {iid: {"file": "images/%s.%s" % (iid, ext), "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}
            for iid, ext, data, _what in imgs}


class RefError(ValueError):
    """The runs cannot be one reference."""


def env_sha256(value):
    """The hash of an environment value: SHA-256 of its compact JSON (as JSON.stringify)."""
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def probes(R):
    """{probe name: {kind, sha256, bytes (pixel probes), value (env)}} of a page result R
    (``window.R`` of index.html). ``bytes`` is the RGBA length: 4 x width x height."""
    out = {}
    for iid, v in R["images"].items():
        for k in IMAGE_PROBES:
            e = v.get(k)
            if not isinstance(e, dict) or "sha256" not in e:
                raise RefError("image %s probe %s has no hash: %r" % (iid, k, e))
            size = 32 if k == "drawImage_scaled32" else 16
            out["%s | %s" % (iid, k)] = {"kind": "image", "sha256": e["sha256"],
                                         "bytes": 4 * size * size}
    for f, v in R["fills"].items():
        if "sha256" not in v:
            raise RefError("fill %s has no hash: %r" % (f, v))
        out["fill | " + f] = {"kind": "fill", "sha256": v["sha256"], "bytes": 4 * 16 * 16}
    p3 = R["canvas_colorspace"].get("p3_fill_p3_canvas") or {}
    if "sha256" not in p3:
        raise RefError("the canvas colorSpace probe has no hash")
    out["canvas colorSpace p3 fill"] = {"kind": "colorspace", "sha256": p3["sha256"],
                                        "bytes": 4 * 16 * 16}
    for k, v in R["env"].items():
        out["env | " + k] = {"kind": "env", "sha256": env_sha256(v), "value": v}
    return out


def build(a, b, repeat, host_profile, source):
    """The reference from the default run ``a``, the control run ``b`` (force_srgb), the
    repeat of ``a`` and the host profile description; all as loaded JSON."""
    for key in ("buildid", "exe_sha256", "version", "os"):
        if a["firefox"][key] != b["firefox"][key] or a["firefox"][key] != repeat["firefox"][key]:
            raise RefError("the runs differ in the Firefox %s" % key)
    pa, pb, pr = probes(a["R"]), probes(b["R"]), probes(repeat["R"])
    if pa != pr:
        bad = sorted(k for k in pa if pa[k] != pr.get(k))
        raise RefError("the repeat run differs from the reference run on %d probes: %s"
                       % (len(bad), ", ".join(bad[:5])))
    if set(pa) != set(pb):
        raise RefError("the control run has other probes than the reference run")
    prefs_a, prefs_b = a["prefs_read_back"], b["prefs_read_back"]
    if prefs_a.get("gfx.color_management.display_profile") not in ("", None):
        raise RefError("the reference run has a display_profile pref: it must read the host's")
    if prefs_a.get("gfx.color_management.force_srgb") is not False:
        raise RefError("the reference run is not made with the default force_srgb (false)")
    if prefs_b.get("gfx.color_management.force_srgb") is not True:
        raise RefError("the control run is not made with force_srgb=true")
    for k, v in prefs_a.items():
        if k != "gfx.color_management.force_srgb" and prefs_b.get(k) != v:
            raise RefError("the control run differs in %s" % k)

    out = {}
    dependent = []
    for name, p in pa.items():
        e = dict(p)
        if pb[name]["sha256"] != p["sha256"]:
            e["without_conversion_sha256"] = pb[name]["sha256"]
            dependent.append(name)
        if name in NOT_COMPARED:
            e["compared"] = False
        out[name] = e
    return {
        "test_page": "index.html",
        "image_generator": "gen_images.py",
        "source": {
            "capture": source,
            "reference_run": "runs/a_win_default.json",
            "repeat_run": "runs/a2_win_default_repeat.json (same hashes on all probes)",
            "control_run": "runs/b_win_force_srgb.json (gfx.color_management.force_srgb=true)",
            "date": "2026-11-09",
        },
        "firefox": {**a["firefox"], "prefs": prefs_a},
        "display_profile": {"file": host_profile["file"], "size": host_profile["size"],
                            "sha256": host_profile["sha256"], "api": "GetICMProfileW"},
        "images": image_files(),
        "probe_count": len(out),
        "profile_dependent_count": len(dependent),
        "profile_dependent": sorted(dependent),
        "probes": out,
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference", help="run json of the retail default run (a_win_default)")
    ap.add_argument("control", help="run json of the force_srgb run (b_win_force_srgb)")
    ap.add_argument("--repeat", required=True, help="run json of the repeat of the default run")
    ap.add_argument("--host-profile", required=True, help="host_display_profile.json")
    ap.add_argument("--source", default="canvas-color/canvas-color-icc-readback",
                    help="name of the capture the runs come from")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv[1:])

    def load(p):
        return json.loads(Path(p).read_text(encoding="utf-8"))

    try:
        refs = build(load(a.reference), load(a.control), load(a.repeat),
                     load(a.host_profile), a.source)
    except RefError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    Path(a.out).write_text(json.dumps(refs, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print("%d probes (%d depend on the display profile) -> %s"
          % (refs["probe_count"], refs["profile_dependent_count"], a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
