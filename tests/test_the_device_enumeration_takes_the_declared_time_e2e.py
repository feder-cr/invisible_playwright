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

import glob
import http.server
import threading

import pytest

# `wall_clock`: the e2e run gives this file the machine alone, after the
# parallel tests: the lower bounds below still read the page's clock.
pytestmark = [pytest.mark.e2e, pytest.mark.wall_clock]

_SEED = 42


class _Page(http.server.BaseHTTPRequestHandler):
    """A page on 127.0.0.1: a potentially trustworthy origin, so mediaDevices exists."""

    def do_GET(self):
        body = b"<!doctype html><title>e</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _measure(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with InvisiblePlaywright(seed=_SEED, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto("http://127.0.0.1:%d/" % srv.server_address[1])
            return page.evaluate(MEASURE)
    finally:
        srv.shutdown()


MEASURE ="""async () => {
  const out = [];
  for (let i = 0; i < 2; i++) {
    const t0 = performance.now();
    const list = await navigator.mediaDevices.enumerateDevices();
    out.push([performance.now() - t0, list.map(d => d.kind).join(',')]);
  }
  return out;
}"""


def test_the_first_enumeration_takes_the_declared_time(firefox_binary):
    (first, kinds), (second, _) = _measure(firefox_binary)
    from invisible_core._fpforge import generate_profile
    declared = generate_profile(_SEED).hardware.media_enumeration_ms
    assert kinds == "audioinput,videoinput"
    # The declared time is waited: load can only add to it.
    assert first >= declared, (first, declared)
    # Kept, as Firefox keeps it: a list asked again would wait the declared
    # time again.
    assert second < declared / 2, (second, declared)
    # ⛔ NO UPPER BOUND HERE ANY MORE. "Nothing on top of the declared time"
    # was measured with the page's clock against a runner whose load delays the
    # parent's main thread by 150-200 ms (741 and 702 against 543 + 143 on the
    # same day, green on rerun). Whether the host was asked is now read from
    # inside the engine: the next test.


def _engine_release() -> int:
    import invisible_core
    return int(invisible_core.__version__.split(".")[0])


def test_the_engine_waits_the_declared_time_and_never_asks_the_host(firefox_binary, tmp_path, monkeypatch):
    """From inside the engine, whatever the load of the machine: the declared
    enumeration logs the wait it dispatched, and the host's speakers are never
    asked (MOZ_LOG=StealthMediaEnumeration, from firefox-57)."""
    if _engine_release() < 57:
        pytest.skip("the engine logs the enumeration from firefox-57")
    from invisible_core._fpforge import generate_profile

    log = tmp_path / "enum"
    monkeypatch.setenv("MOZ_LOG", "sync,StealthMediaEnumeration:4")
    monkeypatch.setenv("MOZ_LOG_FILE", str(log))
    _measure(firefox_binary)
    text ="".join(open(f, encoding="utf-8", errors="replace").read()
                   for f in glob.glob(str(log) + "*"))
    declared = generate_profile(_SEED).hardware.media_enumeration_ms
    assert text.count("declared wait: %d ms" % declared) == 1, text[-2000:]
    assert "host speakers asked: yes" not in text, text[-2000:]
