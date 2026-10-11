"""A request's `resource_type`, read from the ENGINE's own vocabulary.

`tests/test_a_request_says_what_it_is.py` proves the table maps the strings it
is given. It cannot prove those are the strings that arrive, and that is the
exact gap the defect lived in: the table was keyed `document`, `fetch`,
`xmlhttprequest`, Juggler sends `TYPE_DOCUMENT`, `TYPE_FETCH`,
`TYPE_XMLHTTPREQUEST`, and nothing that only reads the table could see the two
disagree. So here a real page, served locally, makes one request of each kind,
and the names come from the engine.
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

PAGE = (b"<!doctype html><html><head>"
        b"<link rel=stylesheet href=/s.css><script src=/j.js></script>"
        b"</head><body><img src=/i.png><script>"
        b"fetch('/f').then(() => {"
        b"  const x = new XMLHttpRequest();"
        b"  x.onloadend = () => { document.title = 'done'; };"
        b"  x.open('GET', '/x'); x.send();"
        b"});"
        b"</script></body></html>")
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
BODIES = {
    "/": ("text/html", PAGE),
    "/s.css": ("text/css", b"body{}"),
    "/j.js": ("application/javascript", b"window.j=1;"),
    "/i.png": ("image/png", PNG),
    "/f": ("text/plain", b"f"),
    "/x": ("text/plain", b"x"),
}


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ctype, body = BODIES.get(self.path, ("text/plain", b""))
            self.send_response(200 if self.path in BODIES else 404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % port


@pytest.mark.e2e
def test_each_kind_of_request_is_named_by_the_engine(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, base = _serve()
    seen = {}
    try:
        with InvisiblePlaywright(seed=4244, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.on("request", lambda r: seen.setdefault(
                r.url[len(base):] or "/", r.resource_type))
            page.goto(base + "/", wait_until="load", timeout=20_000)
            page.wait_for_function("document.title === 'done'", timeout=10_000)
    finally:
        srv.shutdown()

    expected = {"/": "document", "/s.css": "stylesheet", "/j.js": "script",
                "/i.png": "image", "/f": "fetch", "/x": "xhr"}
    assert {k: seen.get(k) for k in expected} == expected, seen
