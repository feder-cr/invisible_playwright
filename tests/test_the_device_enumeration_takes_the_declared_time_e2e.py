"""The first enumerateDevices() of a session takes the declared time on every
host ([B300]).

Until firefox-45 it took the host's: the engine asked the machine's audio
stack for its speakers even with fake devices, 287-351 ms on our Windows
build, 15-38 ms on our Linux one, against 249-278 ms on retail Windows; a
script that sends its payload before the list arrives on Windows saw a device
field only on our Linux build. The session declares 240-290 ms
(`zoom.stealth.media.enumeration_ms`); the list is then kept, as Firefox
keeps it.
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

MEASURE = """async () => {
  const out = [];
  for (let i = 0; i < 2; i++) {
    const t0 = performance.now();
    const list = await navigator.mediaDevices.enumerateDevices();
    out.push([performance.now() - t0, list.map(d => d.kind).join(',')]);
  }
  return out;
}"""


def test_the_first_enumeration_takes_the_declared_time(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><title>e</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto("http://127.0.0.1:%d/" % srv.server_address[1])
            (first, kinds), (second, _) = page.evaluate(MEASURE)
    finally:
        srv.shutdown()
    assert kinds == "audioinput,videoinput"
    # The declared time, plus the page's own round trip to the parent.
    assert 230 <= first <= 400, first
    # Kept, as Firefox keeps it.
    assert second < 50, second
