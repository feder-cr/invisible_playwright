"""What the canvas text e2e tests share: the persona pin, the bundled fonts and the server.

tests/test_canvas_text_is_windows_e2e.py (black text, 342 cases) and
tests/test_canvas_text_colors_and_cleartype_is_windows_e2e.py (coloured text and the
ClearType keys) both compare the RGBA bytes of a canvas drawn by the engine with the ones
retail Firefox 159.0 on Windows drew, for pages served on localhost with the engine's own
bundled font files. This module holds what the two do the same way.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import threading
from pathlib import Path

DATA = Path(__file__).parent / "data" / "canvas_text_ref"

#: What the reference's ``cleartype`` field says the retail machine had: FontSmoothing 2 with
#: FontSmoothingType 2 is ClearType (system text quality 5). The other tuning values are an
#: untuned Windows machine's (gamma 1.8, contrast 1.0, level 1.0), which refs.json does not
#: record.
UNTUNED = {"font.cleartype_gamma": 1800, "font.cleartype_contrast": 100,
           "font.cleartype_level": 100}


def pin(refs):
    """The prefs that make the engine the machine ``refs["cleartype"]`` describes."""
    ct = refs["cleartype"]
    smoothing, kind = str(ct["FontSmoothing"]), str(ct["FontSmoothingType"])
    if smoothing == "2" and kind in ("2", "0x2"):
        quality = 5
    elif smoothing == "2":
        quality = 4
    else:
        quality = 0
    return {"screen.dpr": 1.0, "font.system_text_quality": quality, **UNTUNED}


def bundle_font(fonts_dir, entry):
    """The bundled file a reference entry names."""
    return Path(fonts_dir) / entry["bundle_file"]


def wrong_fonts(fonts_dir, entries):
    """One line for each reference font whose bundled file is not the one measured."""
    wrong = []
    for e in entries:
        f = bundle_font(fonts_dir, e)
        got = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
        if got != e["sha256"]:
            wrong.append("%s: the engine's %s has sha256 %s, the reference was made with %s"
                         % (e["family"], f.name, got, e["sha256"]))
    return wrong


def serve(fonts_dir, entries, pages):
    """``pages`` (url path -> HTML bytes) and, under /bundle/<name>, the engine's bundled
    font files. Returns the running server; the caller shuts it down."""
    by_url = {"/bundle/" + e["bundle_file"]: bundle_font(fonts_dir, e) for e in entries}

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in pages:
                body, kind = pages[path], "text/html"
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


_SPECS_LOOP = "    window.R = {};\n    for (const sp of SPECS) {"


def only_cases(html, ids):
    """canvas_text_ref/index.html as text, drawing only the cases ``ids``.

    The page draws all 342 cases (the 400 px ones take minutes); a test that wants a few of
    them serves this. The drawing code is untouched, so a case gives the bytes it gives in
    the whole page."""
    if _SPECS_LOOP not in html:
        raise AssertionError("canvas_text_ref/index.html no longer has the loop over SPECS "
                             "that only_cases() narrows: update the helper")
    return html.replace(
        _SPECS_LOOP,
        "    window.R = {};\n    const ONLY = new Set(%s);\n"
        "    for (const sp of SPECS.filter(s => ONLY.has(s.id))) {" % json.dumps(sorted(ids)))


def diff_lines(want, got, label=""):
    """One line for each case of ``want`` (id -> sha256) that ``got`` does not draw as
    expected: its name, the hash retail gave, the hash the engine gave (or "not drawn")."""
    lines = []
    for cid in sorted(want):
        have = got.get(cid)
        if have != want[cid]:
            lines.append("%s%s: expected %s, got %s"
                         % (label, cid, want[cid], have if have is not None else "not drawn"))
    lines += ["%s%s: drawn but not in the reference" % (label, cid)
              for cid in sorted(set(got) - set(want))]
    return lines
