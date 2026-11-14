"""What the Fission frame tests (`test_fission_frames_*_e2e.py`) share: three sites, and a proof of a process.

Three SITES on one machine, told apart by HOST and never by port (`127.0.0.1:1` and `127.0.0.1:2` are one site,
so a frame served from the other port stays in the process of its page):

    A  127.0.0.1   the top page, and the A inside A > B > A > C
    B  localhost   the iframe of another site
    C  127.0.0.2   the third site

The wrapper runs Fission the way retail Firefox does (strategy 1, one content process per site), so with no pref a
frame of B inside a page of A runs in another process.

`assert_other_process` is how a test proves that, from inside the pages and with no help from the driver: the
frame spins for 600 ms and the observer frame times a 10 ms timer, which stalls only when the two share a process.
A test file calls it at least once, so the suite cannot silently turn into a same-process suite.
"""
from __future__ import annotations

import json
import struct
import threading
import zlib
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from _servers import LocalServer

HOSTS = {"A": "127.0.0.1", "B": "localhost", "C": "127.0.0.2"}

#: How long the frame keeps its main thread busy, and the longest gap of the observer's timer that still means
#: "another process" (a process shared with the frame is stalled for about the whole spin).
SPIN_MS = 600
OWN_PROCESS_GAP_MS = 300

HTML = "text/html; charset=utf-8"


class Sites:
    """Three local servers, one per site, and the routes they serve.

    ``route(site, path, body, ...)`` registers a page; ``body`` is a string or bytes, or a function of the request
    that returns the same (the request is the handler: ``.headers``, ``.query``). ``requests`` lists what arrived,
    as dicts with ``site``, ``path``, ``query``, ``method`` and ``headers``.
    """

    def __init__(self):
        self._routes = {}
        self.requests = []
        self.servers = {}
        self.origin = {}
        sites = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 10

            def log_message(self, *args):
                pass

            def _answer(self):
                url = urlsplit(self.path)
                self.query = parse_qs(url.query)
                site = self.server.site
                length = int(self.headers.get("Content-Length") or 0)
                self.body = self.rfile.read(length) if length else b""
                sites.requests.append({"site": site, "path": url.path, "query": self.query,
                                       "method": self.command, "headers": dict(self.headers)})
                spec = sites._routes.get((site, url.path))
                if spec is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = spec["body"](self) if callable(spec["body"]) else spec["body"]
                if isinstance(body, str):
                    body = body.encode("utf-8")
                if spec["delay"]:
                    time.sleep(spec["delay"])
                try:
                    self.send_response(spec["status"])
                    self.send_header("Content-Type", spec["ctype"])
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    for name, value in spec["headers"]:
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = _answer
            do_POST = _answer

        self._handler = Handler

    def __enter__(self):
        for site, host in HOSTS.items():
            server = LocalServer((host if host != "localhost" else "127.0.0.1", 0), self._handler)
            server.site = site
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers[site] = server
            self.origin[site] = "http://%s:%d" % (host, server.server_address[1])
        return self

    def __exit__(self, *exc):
        for server in self.servers.values():
            server.shutdown()
            server.server_close()

    def url(self, site, path=""):
        return self.origin[site] + path

    def route(self, site, path, body, ctype=HTML, status=200, headers=(), delay=0):
        self._routes[(site, path)] = {"body": body, "ctype": ctype, "status": status,
                                      "headers": list(headers), "delay": delay}

    def seen(self, site, path):
        return [r for r in self.requests if r["site"] == site and r["path"] == path]


def wait_for(page, predicate, seconds=10.0):
    """Until ``predicate()``, with the client's events being read (the sync client takes the engine's events only
    inside its own calls, so a plain sleep would never see a frame arrive)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        page.wait_for_timeout(100)
    return bool(predicate())


def frame_of(page, origin, path=None):
    """The frame of ``page`` whose url starts with ``origin`` (and ``path``), or None."""
    prefix = origin + (path or "")
    for frame in page.frames:
        if frame.url.startswith(prefix):
            return frame
    return None


def wait_frame(page, origin, path=None, seconds=10.0):
    wait_for(page, lambda: frame_of(page, origin, path) is not None, seconds)
    frame = frame_of(page, origin, path)
    assert frame is not None, "no frame at %s%s; page.frames = %r" % (origin, path or "", [f.url for f in page.frames])
    return frame


def launch(binary, humanize=False, **kwargs):
    """The browser of every test here: seed 42, headless, no humanizing unless asked."""
    from invisible_playwright import InvisiblePlaywright

    return InvisiblePlaywright(seed=42, binary_path=binary, humanize=humanize, headless=True, **kwargs)


def new_page(browser, **context_kwargs):
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    page.set_default_timeout(8000)
    page.set_default_navigation_timeout(30000)
    return context, page


def longest_timer_gap(observer, frame, spin_ms=SPIN_MS):
    """Make ``frame`` spin for ``spin_ms`` and return the longest gap (ms) between two ticks of a 10 ms timer that
    ``observer`` (a frame) runs meanwhile."""
    observer.evaluate("""() => {
        window.__gap = 0;
        let last = performance.now();
        window.__tick = setInterval(() => {
            const now = performance.now();
            window.__gap = Math.max(window.__gap, now - last);
            last = now;
        }, 10);
    }""")
    frame.evaluate("""(ms) => { setTimeout(() => {
        const end = performance.now() + ms;
        while (performance.now() < end) { /* spin */ }
    }, 50); }""", spin_ms)
    observer.page.wait_for_timeout(spin_ms + 800)
    return observer.evaluate("() => { clearInterval(window.__tick); return window.__gap; }")


def assert_other_process(observer, frame):
    """``frame`` runs in a process of its own, not the one of ``observer``: a spin in it does not stall the timer."""
    gap = longest_timer_gap(observer, frame)
    assert gap < OWN_PROCESS_GAP_MS, (
        "the frame at %s shares the process of the observer at %s: a %d ms spin stalled the observer's timer for "
        "%.0f ms" % (frame.url, observer.url, SPIN_MS, gap))


def js(value):
    """``value`` as a JavaScript literal, for a page built from a template."""
    return json.dumps(value)


def decode_png(data):
    """``(width, height, pixel)`` of a PNG made by the browser (8 bits, no interlace); ``pixel(x, y)`` is the
    ``(r, g, b)`` tuple. Enough to ask what colour a screenshot has at a point, with no imaging library."""
    assert data[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10]), "not a PNG"
    pos, idat, width = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and interlace == 0 and color in (2, 6), (depth, color, interlace)
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * channels
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filter_type, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            upleft = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 255
            elif filter_type == 2:
                line[i] = (line[i] + up) & 255
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif filter_type == 4:
                estimate = left + up - upleft
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - upleft)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else upleft)) & 255
        rows.append(line)
        previous = line

    def pixel(x, y):
        offset = x * channels
        return tuple(rows[y][offset:offset + 3])

    return width, height, pixel


def near(color, expected, tolerance=3):
    """``color`` is ``expected`` give or take ``tolerance`` per channel."""
    return all(abs(a - b) <= tolerance for a, b in zip(color, expected))
