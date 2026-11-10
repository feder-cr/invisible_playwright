"""Tagged images drawn on a canvas read back as retail Windows Firefox reads them.

Retail Firefox 159.0 on Windows with the default prefs converts an image that carries a colour
tag (an iCCP chunk, cICP, gAMA + cHRM, an sRGB chunk, a JPEG APP2 profile) from its own space
to the output profile with qcms, and the output profile is the monitor's: on a stock Windows
that is "sRGB Color Space Profile.icm", which qcms does not take for sRGB, so the bytes
getImageData returns for drawImage and createImageBitmap change by a few units. The engine
declares that profile (it never reads the host's) and converts like retail from firefox-62.
Until firefox-59 it skipped the conversion (force_srgb): 27 of the 72 probes below differ
there, and this test fails on such an engine, naming each of them. It is gated on
firefox-62 only so that those releases skip it instead of failing the run.

tests/data/canvas_icc_ref/index.html, a page served on localhost, measures 72 probes:

  40  the ten images of gen_images.py (iccp Display P3, Adobe RGB, Rec.2020 primaries,
      ProPhoto, sRGB primaries; cicp-p3; gama-chrm-p3; srgb-chunk; untagged; a JPEG with a
      Display P3 profile) each drawn with drawImage (16 px), drawImage scaled to 32 px,
      createImageBitmap with the default colorSpaceConversion and with "none"
  22  fillRect with CSS colours (color(display-p3 ...), color-mix(), oklab, ...)
   1  a canvas opened with colorSpace "display-p3"
   9  environment values (color-gamut, dynamic-range, colour depth, devicePixelRatio, the
      colour scheme); the colour scheme is the machine's theme and is not compared

The 27 that depend on the profile are the drawImage, drawImage at 32 px and
createImageBitmap-default probes of the nine tagged images. Each probe's RGBA bytes
(getImageData) are hashed with SHA-256. The images are not files of the repository: the test
builds them with tests/data/canvas_icc_ref/gen_images.py (standard library, the bytes the
measurement used, checked against refs.json before the browser starts) and serves them.

tests/data/canvas_icc_ref/refs.json holds the hashes retail Firefox 159.0 gave on Windows
(build 20261022150525, firefox.exe sha256 d8b5ed82...) with default prefs, gfx.canvas.accelerated
off and the monitor profile above (sha256 2b3aa164...), measured twice with equal results; for
the 27 probes it also holds the hash without the conversion (force_srgb=true on the same
machine), which the failure message names when the engine gave that value. The file is built
by scripts/build_canvas_icc_refs.py from the run files.

Every differing probe is listed (name, expected hash, hash got), not just the first. Set
INVISIBLE_CANVAS_ICC_OUT to a directory and the run writes the hashes it got there
(canvas_icc_<tag>.json).

Run: pytest tests/test_canvas_icc_readback_is_windows_e2e.py -m e2e -v
"""
from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import threading
from pathlib import Path

import pytest

from invisible_playwright import InvisiblePlaywright

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / "data" / "canvas_icc_ref"

#: The page's result without the pixel hex (only the hashes are compared; the hex of every
#: probe would cross the pipe for nothing).
_RESULT = """
() => {
  const strip = (o) => {
    if (o === null || typeof o !== "object") return o;
    const out = {};
    for (const [k, v] of Object.entries(o)) if (k !== "rgba_hex") out[k] = strip(v);
    return out;
  };
  return strip(window.R);
}
"""


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _serve(imgs, listing):
    """index.html, images.json and the generated images, on 127.0.0.1."""
    page = (DATA / "index.html").read_bytes()
    routes = {"/": (page, "text/html"), "/index.html": (page, "text/html")}
    routes["/images.json"] = (json.dumps(listing).encode(), "application/json")
    for iid, ext, data, _what in imgs:
        kind = "image/jpeg" if ext == "jpg" else "image/png"
        routes["/images/%s.%s" % (iid, ext)] = (data, kind)

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hit = routes.get(self.path.split("?")[0])
            if hit is None:
                self.send_error(404)
                return
            body, kind = hit
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.e2e
@pytest.mark.timeout(300, method="thread")
def test_tagged_images_read_back_as_retail_windows_reads_them(firefox_binary, engine_at_least):
    engine_at_least("firefox-62")
    refs_path = DATA / "refs.json"
    if not refs_path.is_file():
        pytest.fail("tests/data/canvas_icc_ref/refs.json is missing: it is the reference "
                    "retail Firefox on Windows wrote for this page (see the module docstring)")
    refs = json.loads(refs_path.read_text(encoding="utf-8"))
    want = refs["probes"]

    gen = _load(DATA / "gen_images.py", "canvas_icc_gen_images")
    imgs, _profiles = gen.build_images()
    drift = ["%s: %s bytes, sha256 %s, the reference was made with %s"
             % (iid, len(data), hashlib.sha256(data).hexdigest(), refs["images"][iid]["sha256"])
             for iid, _ext, data, _what in imgs
             if hashlib.sha256(data).hexdigest() != refs["images"][iid]["sha256"]]
    assert not drift, ("gen_images.py no longer writes the images the reference was measured "
                       "with, so it compares nothing:\n  " + "\n  ".join(drift))

    builder = _load(ROOT / "scripts" / "build_canvas_icc_refs.py", "canvas_icc_build_refs")
    server = _serve(imgs, gen.images_json(imgs))
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        with InvisiblePlaywright(seed=21, binary_path=firefox_binary, headless=True,
                                 pin={"screen.dpr": 1.0}) as b:
            page = b.new_context().new_page()
            page.goto(url, timeout=60_000)
            page.wait_for_function("window.DONE === true || window.ERR", timeout=240_000)
            err = page.evaluate("window.ERR || null")
            assert err is None, "the reference page failed: %s" % err
            result = page.evaluate(_RESULT)
    finally:
        server.shutdown()

    try:
        got = builder.probes(result)
    except builder.RefError as e:
        pytest.fail("the page did not give a hash for every probe: %s" % e)

    out_dir = os.environ.get("INVISIBLE_CANVAS_ICC_OUT")
    if out_dir:
        from invisible_core.seal import active_seal
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / ("canvas_icc_%s.json" % active_seal().tag)).write_text(
            json.dumps({k: v["sha256"] for k, v in got.items()}, indent=1, sort_keys=True),
            encoding="utf-8")

    compared = sorted(k for k, v in want.items() if v.get("compared", True))
    missing = sorted(k for k in want if k not in got)
    extra = sorted(set(got) - set(want))
    differ = [k for k in compared if k in got and got[k]["sha256"] != want[k]["sha256"]]

    lines = []
    for k in differ:
        note = ""
        if got[k]["sha256"] == want[k].get("without_conversion_sha256"):
            note = "  (the value without the conversion: qcms did not convert)"
        elif "without_conversion_sha256" in want[k]:
            note = "  (depends on the display profile)"
        shown = ("; reference value %r, got %r" % (want[k]["value"], got[k]["value"])
                 if want[k]["kind"] == "env" else "")
        lines.append("%s: expected %s, got %s%s%s"
                     % (k, want[k]["sha256"], got[k]["sha256"], shown, note))
    lines += ["not drawn: " + k for k in missing]
    if extra:
        lines.append("drawn but not in the reference: " + ", ".join(extra))
    unconverted = [k for k in differ
                   if got[k]["sha256"] == want[k].get("without_conversion_sha256")]
    assert not lines, (
        "%d of %d canvas colour probes differ from retail Firefox 159.0 on Windows, %d were not "
        "drawn; %d of the %d that depend on the display profile gave the value without the "
        "conversion.\n  %s"
        % (len(differ), len(compared), len(missing), len(unconverted),
           refs["profile_dependent_count"], "\n  ".join(lines)))
