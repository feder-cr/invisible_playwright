"""A probe of the web fonts WebRender keeps, for the e2e of the fonts of closed contexts.

`run(binary, contexts)` opens `contexts` contexts, each with a page that uses ONE web font whose bytes are its own
(WebRender keeps one copy per distinct bytes), loads it, and closes the context; it asks the engine for a memory report
(about:memory's "Measure and save", through Marionette in chrome scope: no hook in the engine) with a few contexts open
and after all of them are closed, and reads `explicit/gfx/webrender/resource-cache/fonts` of every process (the
reporter is in the GPU process where there is one, in the parent otherwise).

Why Marionette and not the wire: the report is the engine's own, and no protocol command asks for it.
"""
from __future__ import annotations

import gzip
import http.server
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

MARIONETTE_PORT = 28431
FONTS_REPORTER = "explicit/gfx/webrender/resource-cache/fonts"


def a_font_file(binary: str) -> Path:
    """A TrueType file to derive the page fonts from: the engine's own bundle when it has one, else the system's."""
    engine = Path(binary).resolve().parent
    candidates: List[Path] = []
    for folder in (engine / "fonts", engine):
        candidates += sorted(folder.glob("*.ttf"))
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates += [windir / "cour.ttf", windir / "arial.ttf", windir / "segoeui.ttf"]
    else:
        candidates += sorted(Path("/usr/share/fonts").rglob("*.ttf"))
    for path in candidates:
        if path.is_file() and path.stat().st_size > 50_000:
            return path
    raise FileNotFoundError("no TrueType font file found to derive web fonts from")


def unique_font(base: bytes, n: int) -> bytes:
    """`base` with the `modified` timestamp of its `head` table set to n: a valid font with bytes of its own.

    The sfnt table directory is read, not assumed: the 12-byte header, then 16 bytes per table (tag, checksum, offset,
    length). The checksum of `head` is not recomputed: it is the one table whose checksum a font file is allowed to get
    wrong (checkSumAdjustment makes the whole file's sum), and the font sanitizer does not reject on it.
    """
    num_tables = struct.unpack(">H", base[4:6])[0]
    for i in range(num_tables):
        tag, _checksum, offset, length = struct.unpack(">4sIII", base[12 + 16 * i: 28 + 16 * i])
        if tag == b"head":
            out = bytearray(base)
            out[offset + 28: offset + 36] = struct.pack(">q", n)  # modified: LONGDATETIME
            return bytes(out)
    raise ValueError("no head table: not a TrueType font")


class _Marionette:
    def __init__(self, port: int, timeout: float = 120.0):
        end = time.time() + timeout
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                self.sock.settimeout(300)
                self._read()
                break
            except (OSError, ValueError):
                if time.time() > end:
                    raise
                time.sleep(1)
        self.n = 0

    def _read(self):
        head = b""
        while not head.endswith(b":"):
            c = self.sock.recv(1)
            if not c:
                raise OSError("closed")
            head += c
        size = int(head[:-1])
        data = b""
        while len(data) < size:
            data += self.sock.recv(size - len(data))
        return json.loads(data)

    def call(self, name: str, params: Optional[dict] = None):
        self.n += 1
        body = json.dumps([0, self.n, name, params or {}]).encode()
        self.sock.sendall(str(len(body)).encode() + b":" + body)
        while True:
            msg = self._read()
            if msg[0] == 1 and msg[1] == self.n:
                if msg[2]:
                    raise RuntimeError("%s: %s" % (msg[2].get("error"), str(msg[2].get("message"))[:200]))
                return msg[3]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


_DUMP = ("let cb = arguments[arguments.length - 1];"
         "let d = Cc['@mozilla.org/memory-info-dumper;1'].getService(Ci.nsIMemoryInfoDumper);"
         "d.dumpMemoryReportsToNamedFile(arguments[0], {callback() { cb('ok'); }}, null, false, false);")
_MINIMIZE = ("let cb = arguments[arguments.length - 1];"
             "Services.obs.notifyObservers(null, 'memory-pressure', 'heap-minimize');"
             "Services.tm.mainThread.dispatch(() => { Cu.forceGC(); Cu.forceCC(); cb('ok'); }, 0);")


def fonts_mb(marionette: _Marionette, folder: str, name: str) -> float:
    """The web fonts WebRender holds, in MB, summed over the processes of a fresh memory report."""
    path = os.path.normpath(os.path.join(folder, name + ".json.gz"))
    marionette.call("Marionette:SetContext", {"value": "chrome"})
    marionette.call("WebDriver:ExecuteAsyncScript", {"script": _MINIMIZE, "args": []})
    time.sleep(2)
    marionette.call("WebDriver:ExecuteAsyncScript", {"script": _DUMP, "args": [path]})
    reports = json.load(gzip.open(path))["reports"]
    return sum(r["amount"] for r in reports if r["path"] == FONTS_REPORTER) / 2 ** 20


def run(binary: str, contexts: int = 20, open_at_once: int = 3) -> Dict[str, float]:
    """The numbers of the probe: fonts held before, with `open_at_once` contexts open, and after all are closed."""
    from invisible_playwright import InvisiblePlaywright

    base = a_font_file(binary).read_bytes()
    fonts: Dict[int, bytes] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/font/"):
                n = int(path[6:].split(".")[0])
                fonts.setdefault(n, unique_font(base, n))
                self._send(fonts[n], "font/ttf")
            elif path.startswith("/page/"):
                n = int(path[6:].split(".")[0])
                html = ("<!doctype html><style>@font-face{font-family:F%d;src:url(/font/%d.ttf)}p{font:24px F%d,serif}</style>"
                        "<p>The quick brown fox %d</p><script>document.fonts.load('24px F%d').then(f=>{window.__font=f.length})</script>"
                        % (n, n, n, n, n))
                self._send(html.encode(), "text/html")
            else:
                self._send(b"<!doctype html><title>x</title>x", "text/html")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = "http://127.0.0.1:%d" % server.server_address[1]
    os.environ.setdefault("MOZ_REMOTE_ALLOW_SYSTEM_ACCESS", "1")
    folder = tempfile.mkdtemp(prefix="webfont_probe_")
    result: Dict[str, float] = {"contexts": contexts}
    marionette = None
    try:
        with InvisiblePlaywright(binary_path=binary, headless=True, humanize=False, extra_args=["-marionette"],
                                 extra_prefs={"marionette.port": MARIONETTE_PORT}) as browser:
            keep = browser.new_context()
            keep_page = keep.new_page()
            keep_page.goto(root + "/blank.html")
            marionette = _Marionette(MARIONETTE_PORT)
            marionette.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            marionette.call("WebDriver:SetTimeouts", {"script": 240000})
            result["before_mb"] = fonts_mb(marionette, folder, "before")
            loaded = 0
            open_now = []
            for n in range(1, contexts + 1):
                context = browser.new_context()
                page = context.new_page()
                page.goto("%s/page/%d.html" % (root, n))
                page.wait_for_function("window.__font !== undefined", timeout=20000)
                loaded += 1 if page.evaluate("window.__font") == 1 else 0
                open_now.append(context)
                if n == open_at_once:
                    result["open_mb"] = fonts_mb(marionette, folder, "open")
                if len(open_now) >= open_at_once:
                    open_now.pop(0).close()
            for context in open_now:
                context.close()
            result["loaded"] = loaded
            result["served"] = len(fonts)
            result["after_mb"] = fonts_mb(marionette, folder, "after")
    finally:
        if marionette is not None:
            marionette.close()
        server.shutdown()
    return result


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20)))
