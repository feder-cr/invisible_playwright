"""The first enumerateDevices() of a session takes the declared time on every
host ([B300]).

Until firefox-45 it took the host's: the engine asked the machine's audio
stack for its speakers even with fake devices, 287-351 ms on our Windows
build, 15-38 ms on our Linux one, against 249-278 or 510-637 ms on retail
Windows, depending on the state of the same machine; a script that sends its
payload before the list arrives on Windows saw a device field only on our
Linux build. The session declares the time (`zoom.stealth.media.
enumeration_ms`, the core's `media_enumeration_ms`, read here from the same
profile and never restated); the list is then kept, as Firefox keeps it.
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

_SEED = 42

#: The smallest time the host's audio stack added to an enumeration before
#: firefox-45 (287-351 ms on our Windows build, the docstring above). The
#: margin over the declared time is half of it: wide enough for a loaded
#: runner, narrow enough that the defect it guards cannot hide in it.
#:
#: These bounds used to be absolute - the first within declared + 110 ms, the
#: second under 50 ms - and a CI run with four browsers crossed each of them
#: once in the release of 0.35.10 (656 against 543 + 110, then exactly 50),
#: both green again on the same SHA ([B312]). A bound that the load of the
#: machine can cross is a bound on the machine, not on the engine.
HOST_STACK_MS = 287

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
        with InvisiblePlaywright(seed=_SEED, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto("http://127.0.0.1:%d/" % srv.server_address[1])
            (first, kinds), (second, _) = page.evaluate(MEASURE)
    finally:
        srv.shutdown()
    from invisible_core._fpforge import generate_profile
    declared = generate_profile(_SEED).hardware.media_enumeration_ms
    assert kinds == "audioinput,videoinput"
    # The declared time is waited: load can only add to it.
    assert first >= declared, (first, declared)
    # Kept, as Firefox keeps it: a list asked again would wait the declared
    # time again.
    assert second < declared / 2, (second, declared)
    # And nothing on top of it. The second call is the same round trip to the
    # parent without the wait, so the difference is the declared time plus
    # whatever the host added; the load both calls share cancels out instead of
    # adding up.
    assert first - second < declared + HOST_STACK_MS / 2, (first, second, declared)
