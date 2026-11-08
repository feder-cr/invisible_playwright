"""Canvas 2D text is drawn as retail Firefox on Windows draws it, on every OS.

Retail Firefox on Windows draws canvas text with Skia over DirectWrite. The engine draws it
with Skia over idwr (DirectWrite 10.0.26100.9711 in portable C) from firefox-61, so the
same page gives the same RGBA bytes on Windows, on Debian 12 and on Ubuntu 24.04.

tests/data/canvas_text_ref/index.html draws 240 cases (Arial, Segoe UI, Times New Roman,
Consolas and Calibri at 9, 12.5, 16, 33, 72 and 300 px, with and without a rotate(15deg)
scale(1.5, 2) transform; fillText, strokeText, synthetic italic and bold), reads each canvas
back with getImageData and hashes the RGBA bytes with SHA-256. The fonts are @font-face
webfonts: the files of the engine's own ``fonts`` directory, served by this test, so the
comparison does not depend on what the host has installed. tests/data/canvas_text_ref/
refs.json holds the hashes retail Firefox 159.0 on Windows gave for the same page and the
same files (gfx.canvas.accelerated off, white background); rebuild.py of the reference
writes it. Its ``fonts`` entries carry the sha256 of each file, which must be the engine's
bundled one: a reference made with other font files compares nothing.

Every differing case is listed, not just the first. Set INVISIBLE_CANVAS_TEXT_OUT to a
directory and the run writes its hashes there (canvas_text_<tag>.json); scripts/
compare_canvas_text_runs.py says which cases two runs, the Debian one and the Ubuntu one,
draw differently.

Run: pytest tests/test_canvas_text_is_windows_e2e.py -m e2e -v
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import threading
from pathlib import Path

import pytest

from invisible_playwright import InvisiblePlaywright

DATA = Path(__file__).parent / "data" / "canvas_text_ref"

#: The ClearType tuning of the machine the reference was measured on. refs.json may carry its
#: own under "cleartype" (the pin keys of the declared persona); this is retail's default.
_DEFAULT_PIN = {"screen.dpr": 1.0, "font.cleartype_gamma": 1800,
                "font.cleartype_contrast": 100}


def _bundle_font(fonts_dir, entry):
    """The bundled file a reference entry stands for: ``bundle_file`` when the reference
    names it, else the entry's own file name."""
    return fonts_dir / entry.get("bundle_file", Path(entry["file"]).name)


def _serve(fonts_dir, entries):
    """The page and, under fonts/<name from the page>, the engine's bundled files."""
    by_url = {"/" + e["file"]: _bundle_font(fonts_dir, e) for e in entries}
    page = (DATA / "index.html").read_bytes()

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                body, kind = page, "text/html"
            elif path in by_url and by_url[path].is_file():
                body, kind = by_url[path].read_bytes(), "font/ttf"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.e2e
def test_canvas_text_is_what_retail_windows_draws(firefox_binary, engine_at_least):
    engine_at_least("firefox-61")
    refs_path = DATA / "refs.json"
    if not refs_path.is_file():
        pytest.fail("tests/data/canvas_text_ref/refs.json is missing: it is the reference "
                    "retail Firefox on Windows wrote for this page (see the module docstring)")
    refs = json.loads(refs_path.read_text(encoding="utf-8"))
    fonts_dir = Path(firefox_binary).parent / "fonts"

    wrong = []
    for e in refs["fonts"]:
        f = _bundle_font(fonts_dir, e)
        got = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
        if got != e["sha256"]:
            wrong.append("%s: %s has sha256 %s, the reference was made with %s"
                         % (e["family"], f.name, got, e["sha256"]))
    assert not wrong, (
        "the reference was measured with other font files than the engine's bundle, so it "
        "compares nothing; rebuild refs.json from the bundled files:\n  " + "\n  ".join(wrong))

    server = _serve(fonts_dir, refs["fonts"])
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        with InvisiblePlaywright(seed=21, binary_path=firefox_binary, headless=True,
                                 pin=refs.get("cleartype", _DEFAULT_PIN)) as b:
            page = b.new_context().new_page()
            page.goto(url, timeout=60_000)
            page.wait_for_function("window.DONE === true || window.ERR", timeout=300_000)
            err = page.evaluate("window.ERR || null")
            assert err is None, "the reference page failed: %s" % err
            got = page.evaluate(
                "Object.fromEntries(Object.entries(window.R).map(([k, v]) => [k, v.sha256]))")
    finally:
        server.shutdown()

    out_dir = os.environ.get("INVISIBLE_CANVAS_TEXT_OUT")
    if out_dir:
        from invisible_core.seal import active_seal
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / ("canvas_text_%s.json" % active_seal().tag)).write_text(
            json.dumps(got, indent=1, sort_keys=True), encoding="utf-8")

    want = {k: v["sha256"] for k, v in refs["cases"].items()}
    missing = sorted(set(want) - set(got))
    differ = sorted(k for k in want if k in got and got[k] != want[k])
    assert not missing and not differ, (
        "%d of %d canvas text cases differ from retail Firefox on Windows, %d were not drawn.\n"
        "Differ:\n  %s\nNot drawn:\n  %s"
        % (len(differ), len(want), len(missing), "\n  ".join(differ[:60]),
           "\n  ".join(missing[:20])))
