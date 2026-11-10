"""Canvas 2D text is drawn as retail Firefox on Windows draws it, on every OS.

Retail Firefox on Windows draws canvas text with Skia over DirectWrite. The engine draws it
with Skia over idwr (DirectWrite 10.0.26100.9711 in portable C) from firefox-61, so the
same page gives the same RGBA bytes on Windows, on Debian 12 and on Ubuntu 24.04.

tests/data/canvas_text_ref/index.html draws 342 cases, in groups (the ``gruppo`` field of
each case in refs.json):

  0_base                       211  Arial, Segoe UI, Times New Roman, Consolas and Calibri at 9,
                                    12.5, 16, 33, 72 and 300 px, with and without a rotate(15deg)
                                    scale(1.5, 2) transform; fillText, strokeText, synthetic
                                    italic and bold
  1_alpha_false                 25  the same faces at 5 sizes on getContext('2d', {alpha: false})
                                    (an opaque canvas, where LCD text is possible)
  2a_rot_alpha_true_controllo   25  rotated and scaled, alpha true (the base's cases, drawn once)
  2b_rot_alpha_false            25  rotated and scaled, alpha false
  3_emoji_colr0                 12  COLR v0 emoji, Twemoji Mozilla, alpha true and false
  4_soglie                      20  255, 256, 257, 300 and 400 px, fillText and strokeText
  5_variabile_bahnschrift       12  font-weight 300/400/700 by font-stretch 75%/100%
  6_variabile_seguivar          12  Segoe UI Variable, font-weight 300/400/700 at 9, 12.5, 16 and
                                    33 px (opsz follows the size): the instance the scaled font
                                    asks of idwr, on every OS

Each canvas is read back with getImageData and its RGBA bytes are hashed with SHA-256. The
fonts are @font-face webfonts: the files of the engine's own ``fonts`` directory, served by
this test, so the comparison does not depend on what the host has installed.
tests/data/canvas_text_ref/refs.json holds the hashes retail Firefox 159.0 on Windows gave for
the same page and the same bundled files (gfx.canvas.accelerated off, white background), and
the ClearType state of that machine, which the test declares as the persona (system text
quality 5). Its ``fonts`` entries carry the sha256 of each file, which must be the engine's
bundled one (Arial 05c7da5fe195...; Twemoji Mozilla, Bahnschrift and Segoe UI Variable too): a reference made with
other font files compares nothing. The file is built by scripts/build_canvas_text_refs.py from
the two measurements.

The largest canvases (400 px: 7727 x 3373, 100 MB of RGBA) are drawn one at a time and
released after their hash is taken, as the measuring page did.

Every differing case is listed, group by group, not just the first. Set INVISIBLE_CANVAS_TEXT_OUT to a
directory and the run writes its hashes there (canvas_text_<tag>.json); scripts/
compare_canvas_text_runs.py says which cases two runs, the Debian one and the Ubuntu one,
draw differently.

Run: pytest tests/test_canvas_text_is_windows_e2e.py -m e2e -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _canvas_text import DATA, pin as _pin, serve as _serve, wrong_fonts
from invisible_playwright import InvisiblePlaywright


@pytest.mark.e2e
@pytest.mark.timeout(1800, method="thread")
def test_canvas_text_is_what_retail_windows_draws(firefox_binary, engine_at_least):
    engine_at_least("firefox-61")
    refs_path = DATA / "refs.json"
    if not refs_path.is_file():
        pytest.fail("tests/data/canvas_text_ref/refs.json is missing: it is the reference "
                    "retail Firefox on Windows wrote for this page (see the module docstring)")
    refs = json.loads(refs_path.read_text(encoding="utf-8"))
    fonts_dir = Path(firefox_binary).parent / "fonts"

    wrong = wrong_fonts(fonts_dir, refs["fonts"])
    assert not wrong, (
        "the reference was measured with other font files than the engine's bundle, so it "
        "compares nothing; rebuild refs.json from the bundled files:\n  " + "\n  ".join(wrong))

    pin = _pin(refs)
    page = (DATA / "index.html").read_bytes()
    server = _serve(fonts_dir, refs["fonts"], {"/": page, "/index.html": page})
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        with InvisiblePlaywright(seed=21, binary_path=firefox_binary, headless=True,
                                 pin=pin) as b:
            page = b.new_context().new_page()
            page.goto(url, timeout=60_000)
            page.wait_for_function("window.DONE === true || window.ERR", timeout=1_500_000)
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

    cases = refs["cases"]
    want = {k: v["sha256"] for k, v in cases.items()}
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    differ = sorted(k for k in want if k in got and got[k] != want[k])

    lines = []
    for group in sorted({v["gruppo"] for v in cases.values()}):
        ids = sorted(k for k, v in cases.items() if v["gruppo"] == group)
        bad = [k for k in ids if k in differ]
        gone = [k for k in ids if k in missing]
        if bad or gone:
            lines.append("%s: %d of %d differ, %d not drawn" % (group, len(bad), len(ids), len(gone)))
            lines += ["    differs: " + k for k in bad[:40]]
            if len(bad) > 40:
                lines.append("    ... and %d more" % (len(bad) - 40))
            lines += ["    not drawn: " + k for k in gone[:20]]
    if extra:
        lines.append("drawn but not in the reference: " + ", ".join(extra[:20]))
    assert not lines, (
        "%d of %d canvas text cases differ from retail Firefox on Windows, %d were not drawn.\n%s"
        % (len(differ), len(want), len(missing), "\n".join(lines)))
