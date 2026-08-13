"""A long browsing session must survive heavy text shaping.

History. Between firefox-18 and firefox-19 a browsing session died on
its own, without the harness asking it to close, on 6 sessions out of
10. The Linux core dumps named the frame: ``SIGSEGV`` inside HarfBuzz,
reached from ``gfxHarfBuzzShaper::GetDesignFont``. The cause was
ownership, not arithmetic: ``GetHBFace()`` returns an ``AutoHBFace``
RAII object, and binding it to a raw ``hb_face_t*`` destroyed the
temporary at the semicolon. The pointer that survived was already
disowned, so the face's reference count went one under, the entry the
font list still cached was freed underneath it, and the next page that
asked for the same family shaped through freed memory.

That defect is only reachable by SHAPING, repeatedly, across many
families, over a session long enough for a face to be released and
asked for again. Every existing e2e test opens a page, asserts one
thing and closes: none of them shapes enough text, and none of them
lives long enough. All of them were green while 6 sessions in 10 were
dying.

The test below reproduces the shape of the real workload and stays
hermetic. It serves one local page that renders every family the core
DECLARES (parsed from the profile's own manifest, so a change to the
bundle moves the test with it rather than leaving it asserting a stale
list), and it walks that page in a single session while pages are
opened and closed around it, which is what returns a face to the cache
so the next navigation can ask for it again.

It runs entirely offline: one HTTP server on 127.0.0.1 and no external
site, so a red result is the browser and never the network.
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from invisible_core._fpforge import generate_profile
from invisible_playwright import InvisiblePlaywright


_SEED = 970411

# How long the session is. Each round is a fresh document over every
# declared family, so the shaping work is round * families.
_ROUNDS = 6

# One round costs seconds, not milliseconds, and the reason is a
# measured defect rather than the workload: this build reloads every
# declared face on EVERY document, where a retail Firefox 151 loads
# them once per process. Measured 2026-08-11 on the second document of
# a session: retail 7ms of layout and 9ms of canvas, ours 5166ms and
# 4202ms. The default 30s navigation timeout is therefore not a safety
# margin here, it is a coin flip, and a test that fails on a timeout
# reports "the session died" for a browser that was merely busy. See
# 70-known-bugs.md "ogni documento ricarica i font dichiarati".
_GOTO_TIMEOUT_MS = 120_000

# Strings chosen so the shaper takes more than one path: Latin, a
# script with per-script fallback, a combining sequence, and a symbol
# that lands in the colour-presentation fallback list.
_SPECIMENS = (
    "The quick brown fox jumps over the lazy dog 0123456789",
    "Nel mezzo del cammin di nostra vita mi ritrovai",
    "你好世界 こんにちは",
    "éàîõü — ❤ ✈ ☀",
)


def _declared_families() -> list[str]:
    """Every family the core declares, read from the profile manifest.

    The manifest is the single source (engine rule 7): the families are
    not written down a second time here.
    """
    manifest = generate_profile(_SEED).font.manifest
    fams = [
        line[2:].strip()
        for line in manifest.splitlines()
        if line.startswith("F|")
    ]
    assert fams, "the profile manifest declares no family - parse broke"
    return fams


def _page_html(families: list[str]) -> bytes:
    rows = []
    for i, fam in enumerate(families):
        spec = _SPECIMENS[i % len(_SPECIMENS)]
        safe = fam.replace("'", "")
        for size in (11, 17, 29):
            rows.append(
                "<div style=\"font-family:'" + safe + "';font-size:"
                + str(size) + "px\">" + spec + "</div>"
            )

    script = """
<canvas id=c width=600 height=80></canvas>
<script>
var FAMS = __FAMS__;
var SPECS = __SPECS__;
var ctx = document.getElementById('c').getContext('2d');
var shaped = 0, widths = 0;
for (var i = 0; i < FAMS.length; i++) {
  for (var s = 0; s < 3; s++) {
    var px = [11, 17, 29][s];
    ctx.font = px + "px '" + FAMS[i] + "'";
    var t = SPECS[(i + s) % SPECS.length];
    var m = ctx.measureText(t);
    widths += m.width;
    ctx.fillText(t, 2, 20 + s * 20);
    shaped++;
  }
}
window.__shaped = shaped;
window.__widths = Math.round(widths);
document.title = 'shaped:' + shaped;
</script>
"""
    import json as _json
    script = script.replace("__FAMS__", _json.dumps(families))
    script = script.replace("__SPECS__", _json.dumps(list(_SPECIMENS)))

    html = (
        "<!doctype html><meta charset=utf-8><title>t</title>"
        "<body>" + "".join(rows) + script + "</body>"
    )
    return html.encode("utf-8")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _SilentHandler(BaseHTTPRequestHandler):
    PAYLOAD = b""

    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self.PAYLOAD)


def _serve(payload: bytes, port: int) -> ThreadingHTTPServer:
    """Threading, deliberately.

    A single-threaded HTTPServer serves round 0 and then blocks: the
    browser leaves a speculative connection open, the accept loop sits
    inside it waiting for a request line that never arrives, and the
    next navigation times out with the server perfectly healthy. That
    is a harness fault reported as a browser fault, which is the exact
    confusion this test exists to avoid.
    """
    cls = type("_H", (_SilentHandler,), {"PAYLOAD": payload})
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# La scadenza per test di `run_e2e.py` e' 420s e questo test la sfiora. Non
# perche' sia scritto male: e' l'unico che paga per intero il difetto APERTO del
# layout che non mette in cache i font fra documenti su Windows (8 famiglie danno
# 8ms, 68 ne danno 5166 - `72-next-steps.md`). Misurato il 2026-08-13 sullo stesso
# commit, con il cane da guardia di conftest: **4,3 secondi su Linux in CI e oltre
# 300 su Windows**, 69 volte tanto, mentre l'intera suite e' solo 3 volte piu'
# lenta. Su una macchina piu' carica supererebbe i 420 e verrebbe ucciso, e un
# test legittimo ucciso da una protezione produce un rosso identico
# all'impiccamento che quella protezione esiste per trovare.
#
# La scadenza globale resta stretta e QUESTO test riceve la sua. Va tolta quando
# il difetto del layout viene chiuso, non prima.
@pytest.mark.timeout(900)
@pytest.mark.e2e
def test_a_long_session_survives_heavy_text_shaping(firefox_binary):
    """The session must still be alive after shaping every declared
    family, repeatedly, with pages opening and closing around it.

    A dead session is the assertion. The failure mode this exists for
    does not raise a JavaScript error and does not return a wrong
    number: the process disappears, and Playwright reports the target
    as closed on whichever call happens to be in flight. So the test
    reports the round it reached, which is the number that separates
    "died immediately" from "died deep into a session".
    """
    families = _declared_families()
    port = _free_port()
    srv = _serve(_page_html(families), port)
    base = "http://127.0.0.1:" + str(port) + "/"

    rounds_done = 0
    shaped_seen: list[int] = []
    try:
        # The launch is deliberately OUTSIDE the guard below. A binary
        # that cannot start is a different fault from a session that
        # dies mid-flight, and reporting the first as "died at round 0"
        # is a message that sends the reader to the wrong place.
        with InvisiblePlaywright(seed=_SEED, binary_path=firefox_binary,
                                 headless=True, humanize=False) as ip:
            page = ip.new_page()
            try:
                for r in range(_ROUNDS):
                    page.goto(base + "?round=" + str(r), wait_until="load",
                              timeout=_GOTO_TIMEOUT_MS)
                    shaped = page.evaluate("window.__shaped")
                    widths = page.evaluate("window.__widths")

                    assert shaped == len(families) * 3, (
                        "round " + str(r) + ": the page shaped "
                        + str(shaped) + " runs instead of "
                        + str(len(families) * 3)
                        + " - a family failed to resolve"
                    )
                    assert widths and widths > 0, (
                        "round " + str(r) + ": measureText returned no "
                        "width at all, so nothing was actually shaped"
                    )
                    shaped_seen.append(shaped)

                    # Open and close a second page every few rounds.
                    # This is what lets a face be released and asked
                    # for again, which is the sequence the ownership
                    # defect needed.
                    if r % 3 == 2:
                        other = ip.new_page()
                        other.goto(base + "?aux=" + str(r),
                                   wait_until="load",
                                   timeout=_GOTO_TIMEOUT_MS)
                        other.evaluate("window.__shaped")
                        other.close()

                    rounds_done = r + 1

                # The session is still ours to drive: prove it with a
                # call that has to reach the browser process, not a
                # cached value.
                assert page.evaluate("1 + 1") == 2
                assert page.title().startswith("shaped:")
            except AssertionError:
                raise
            except Exception as exc:  # noqa: BLE001 - message is the point
                raise AssertionError(
                    "the session did not survive: it died at round "
                    + str(rounds_done) + " of " + str(_ROUNDS)
                    + " (" + str(len(families)) + " families per round). "
                    + type(exc).__name__ + ": " + str(exc)[:300]
                ) from exc
    finally:
        srv.shutdown()

    assert rounds_done == _ROUNDS
    assert len(set(shaped_seen)) == 1, (
        "the number of shaped runs changed between rounds: "
        + str(sorted(set(shaped_seen)))
        + " - the font list is not answering the same way twice"
    )
