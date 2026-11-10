"""The identity a page reads is the same in the top document and in every iframe, whatever
process the iframe runs in.

With ``fission.webContentIsolationStrategy=0`` every iframe shares the content process of
its parent. With strategy 1 (site isolation) an iframe on another site runs in a content
process of its own, and the identity has to reach that process by itself: the seed
(``zoom.stealth.fpp.hw_seed``), the font manifest, the language and the time zone are
declared by the core and read by each process, none of them is inherited from the parent
document. A process that reads an empty or a different declaration draws a different
canvas, renders a different audio buffer, or reports a different language: to a site that
embeds its own iframe, that is a browser whose identity changes from one frame to the
next, the loudest mismatch there is.

What is compared, in the top document and in three iframes (``127.0.0.2`` on another site,
``127.0.0.1`` nested inside that one, and ``127.0.0.3`` on a third site), all served by one
local server so that the hosts, not the ports, tell the sites apart:

- the hash of a fixed canvas drawing (its pixels and its PNG bytes) and of a fixed
  ``OfflineAudioContext`` buffer,
- ``WEBGL_debug_renderer_info`` vendor and renderer,
- ``Intl`` time zone, ``navigator.language``/``languages``/``userAgent``,
- ``screen.width/height/availWidth/availHeight`` and ``devicePixelRatio``,
- ``document.fonts`` and the measured width of a family of the font bundle.

The negative case is the persona, not the canvas: the 2D canvas is drawn as it is, without noise
(decisions/prodotto-06), and the audio buffer carries no per-seed value either, so neither moves
with the seed. What moves is the machine the seed describes: the WebGL renderer and the screen.
A different seed has to give a different machine in every frame, or equal values prove nothing.
The canvas PNG is compared inside one launch only: the bytes of a PNG made on a page served over
HTTP end with a ``deBG`` chunk that differs from one launch to the next.

Not compared: ``window.screenX``/``screenY``. They are the position of the window and the
geometry invariant of an iframe has its own test (``test_cross_origin_iframe.py``).

The values are read BY THE PAGES, from inside each document, and sent to the top with
``postMessage``: what a site sees, not what the protocol reports, and nothing here depends
on how the driver reaches an iframe of another process.

An absence is not an agreement: a context that reports no value, or an error, for a field
the top document answers is a mismatch, never a pass.

Two strategies run: ``0`` (what the core writes today: the iframes share the process) and
``1`` (``fission.autostart=true``, ``fission.webContentIsolationStrategy=1``, passed as
``extra_prefs``; ``dom.ipc.processCount.webIsolated`` stays at its default of 1, which is
one process per site). Each run also proves where the iframes ran, from inside the page: the
iframe on the third site spins for 400 ms and the top times a 10 ms timer, which stalls only
when the two share a process. A run that silently stayed in one process cannot pass for the
isolated case, and the other way round.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import threading
import time
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from _servers import LocalServer  # noqa: E402

SEED = 4242
OTHER_SEED = 4243

#: The three sites. Sites are told apart by host, never by port: the port does not separate them.
HOST_TOP = "127.0.0.1"
HOST_OOP = "127.0.0.2"
HOST_OOP_2 = "127.0.0.3"

#: The four documents, in the order the page builds them.
CONTEXTS = ("top", "oop_127_0_0_2", "nested_127_0_0_1", "oop_127_0_0_3")

#: What every context reads, by the group a test checks. ``screenX``/``screenY`` are read but
#: belong to no group: see the module docstring.
GROUPS = {
    "canvas": ("canvas", "canvas_png"),
    "audio": ("audio",),
    "webgl": ("webgl_vendor", "webgl_renderer"),
    "locale_and_agent": ("time_zone", "language", "languages", "user_agent"),
    "screen": ("screen_width", "screen_height", "screen_avail_width", "screen_avail_height",
               "device_pixel_ratio"),
    "fonts": ("fonts_check", "font_width_arial", "font_width_segoe_ui", "font_width_fallback"),
}

#: How long the second iframe keeps its main thread busy, and the gaps the top then sees in a
#: 10 ms timer: a process shared with the top delays the timer by about the whole spin, a process
#: of its own does not delay it at all.
SPIN_MS = 400
SHARED_PROCESS_GAP_MS = 300
OWN_PROCESS_GAP_MS = 200

#: The strategy-0 prefs, passed explicitly so that the case keeps its meaning on the day the core
#: writes strategy 1 by default.
STRATEGY_0_PREFS = {"fission.autostart": False, "fission.webContentIsolationStrategy": 0}

#: The fields a seed decides: the machine it describes. Compared between two seeds.
PERSONA_FIELDS = ("webgl_renderer", "screen_width", "screen_height")

#: The strategy-1 prefs. ``dom.ipc.processCount.webIsolated`` is not here on purpose: the core
#: writes 1 and the Firefox default is 1, so it is already one process per site.
STRATEGY_1_PREFS = {"fission.autostart": True, "fission.webContentIsolationStrategy": 1}

# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------

#: What every document computes. ``cyrb53`` is a plain string hash: ``crypto.subtle`` needs a
#: secure context and not every one of these hosts is one.
PROBE_JS = r"""
function cyrb53(str, seed) {
  let h1 = 0xdeadbeef ^ (seed || 0), h2 = 0x41c6ce57 ^ (seed || 0);
  for (let i = 0, ch; i < str.length; i++) {
    ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
}
function attempt(fn) {
  try { return fn(); } catch (e) { return 'error:' + e; }
}
function canvasHashes() {
  const c = document.createElement('canvas');
  c.width = 260; c.height = 90;
  const x = c.getContext('2d');
  const g = x.createLinearGradient(0, 0, 260, 90);
  g.addColorStop(0, '#d94f30'); g.addColorStop(1, '#2f6fd9');
  x.fillStyle = g; x.fillRect(0, 0, 260, 90);
  x.fillStyle = 'rgba(255, 255, 255, 0.55)';
  x.beginPath(); x.arc(70, 45, 32, 0, Math.PI * 2); x.fill();
  x.strokeStyle = '#102030'; x.lineWidth = 3;
  x.beginPath(); x.moveTo(10, 80); x.bezierCurveTo(60, 10, 160, 120, 250, 20); x.stroke();
  x.font = '18px Arial'; x.fillStyle = '#101010';
  x.fillText('Fission parity 0123 \u00e9\u00fc', 12, 30);
  x.font = '14px "Segoe UI"'; x.fillStyle = '#ffffff';
  x.fillText('seed identity', 120, 70);
  const px = x.getImageData(0, 0, 260, 90).data;
  let s = '';
  for (let i = 0; i < px.length; i++) s += px[i] + ',';
  const url = c.toDataURL();
  return {pixels: cyrb53(s) + ':' + px.length, png: cyrb53(url) + ':' + url.length};
}
async function audioHash() {
  const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const ctx = new Ctx(1, 5000, 44100);
  const osc = ctx.createOscillator();
  osc.type = 'triangle'; osc.frequency.value = 10000;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -50; comp.knee.value = 40; comp.ratio.value = 12;
  comp.attack.value = 0; comp.release.value = 0.2;
  osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
  const buf = await ctx.startRendering();
  const data = buf.getChannelData(0);
  const bits = new Uint32Array(data.buffer, data.byteOffset, data.length);
  let s = '';
  for (let i = 0; i < bits.length; i++) s += bits[i].toString(16) + ',';
  return cyrb53(s) + ':' + data.length;
}
function webgl() {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
  if (!gl) return null;
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  if (!ext) return {vendor: null, renderer: null};
  return {vendor: String(gl.getParameter(ext.UNMASKED_VENDOR_WEBGL)),
          renderer: String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL))};
}
function measure(family) {
  const s = document.createElement('span');
  s.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font-size:40px;'
    + 'font-family:' + family;
  s.textContent = 'Hamburgefonstiv 0123456789 mmmmmmmmmm';
  document.body.appendChild(s);
  const w = s.getBoundingClientRect().width;
  s.remove();
  return w;
}
// The process probe: a document asked to spin keeps its process's main thread busy.
window.addEventListener('message', e => {
  if (e.data && e.data.fission_busy) {
    const end = performance.now() + e.data.fission_busy;
    while (performance.now() < end) { /* spin */ }
  }
});
async function collect(name) {
  const out = {name: name, origin: location.origin};
  const cv = attempt(canvasHashes);
  out.canvas = typeof cv === 'string' ? cv : cv.pixels;
  out.canvas_png = typeof cv === 'string' ? cv : cv.png;
  try { out.audio = await audioHash(); } catch (e) { out.audio = 'error:' + e; }
  const g = attempt(webgl);
  out.webgl_vendor = g === null ? null : (typeof g === 'string' ? g : g.vendor);
  out.webgl_renderer = g === null ? null : (typeof g === 'string' ? g : g.renderer);
  out.time_zone = attempt(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  out.language = attempt(() => navigator.language);
  out.languages = attempt(() => navigator.languages.join('|'));
  out.user_agent = attempt(() => navigator.userAgent);
  out.screen_width = attempt(() => screen.width);
  out.screen_height = attempt(() => screen.height);
  out.screen_avail_width = attempt(() => screen.availWidth);
  out.screen_avail_height = attempt(() => screen.availHeight);
  out.device_pixel_ratio = attempt(() => window.devicePixelRatio);
  out.fonts_check = attempt(() => document.fonts.check('16px Arial'));
  out.font_width_arial = attempt(() => measure('Arial, monospace'));
  out.font_width_segoe_ui = attempt(() => measure('"Segoe UI", monospace'));
  out.font_width_fallback = attempt(() => measure('monospace'));
  // Read, never compared: see the module docstring.
  out.screen_x = attempt(() => window.screenX);
  out.screen_y = attempt(() => window.screenY);
  return out;
}
"""


def probe_page(name: str, child_url: str | None = None) -> bytes:
    """A document that reads the identity and sends it to the top, with an optional iframe."""
    iframe = '<iframe id="child" src="%s" width="320" height="120"></iframe>' % child_url if child_url else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>%s</title></head><body>%s"
        "<script>%s\n"
        "collect(%s).then(r => window.top.postMessage({fission_parity: r}, '*'));</script>"
        "</body></html>" % (name, iframe, PROBE_JS, json.dumps(name))
    ).encode("utf-8")


def top_page(port: int, hosts=(HOST_TOP, HOST_OOP, HOST_OOP_2)) -> bytes:
    """The top document: three iframes (the middle one holds a fourth) and a collector.

    ``window.__parity`` has one entry per context, and ``window.__parity_done`` turns true
    when all of them have reported.
    """
    top, oop, oop2 = hosts
    nested = "http://%s:%d/probe/nested_127_0_0_1" % (top, port)
    frames = (
        '<iframe id="oop1" src="http://%s:%d/probe/oop_127_0_0_2?child=%s" width="400" height="260"></iframe>'
        % (oop, port, nested),
        '<iframe id="oop2" src="http://%s:%d/probe/oop_127_0_0_3" width="320" height="120"></iframe>'
        % (oop2, port),
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>fission identity parity</title>"
        "<script>window.__parity = {}; window.__parity_done = false;\n"
        "window.addEventListener('message', e => {\n"
        "  const r = e.data && e.data.fission_parity;\n"
        "  if (!r) return;\n"
        "  window.__parity[r.name] = r;\n"
        "  window.__parity_done = Object.keys(window.__parity).length >= %d;\n"
        "});</script></head><body>%s"
        "<script>%s\n"
        "collect('top').then(r => window.top.postMessage({fission_parity: r}, '*'));</script>"
        "</body></html>" % (len(CONTEXTS), "".join(frames), PROBE_JS)
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _absent(value) -> bool:
    return value is None or (isinstance(value, str) and (value == "" or value.startswith("error:")))


def mismatches(results: dict, fields) -> list[str]:
    """Every way the iframes differ from the top document on ``fields``.

    An absent value (``None``, empty, an error) in the top document is reported, because a
    comparison against nothing proves nothing; the same value absent in an iframe is a
    mismatch, because two empty answers are not an agreement.
    """
    problems = []
    top = results.get("top")
    if top is None:
        return ["the top document reported nothing"]
    for name in CONTEXTS[1:]:
        if name not in results:
            problems.append("%s: the document reported nothing" % name)
    for field in fields:
        reference = top.get(field)
        if _absent(reference):
            problems.append("top: %s is absent (%r), nothing to compare against" % (field, reference))
            continue
        for name in CONTEXTS[1:]:
            context = results.get(name)
            if context is None:
                continue
            value = context.get(field)
            if _absent(value):
                problems.append("%s: %s is absent (%r), the top reads %r" % (name, field, value, reference))
            elif value != reference:
                problems.append("%s: %s = %r, the top reads %r" % (name, field, value, reference))
    return problems


def same_persona(first: dict, second: dict) -> list[str]:
    """The contexts of ``second`` that read, on every persona field, what ``first``'s top reads.

    Empty when the two seeds gave two different machines in every frame. An absent value is never
    a difference (an error and an empty string are not two machines), so it is listed too.
    """
    reference = tuple(first["top"].get(f) for f in PERSONA_FIELDS)
    found = []
    for name in CONTEXTS:
        values = tuple(second[name].get(f) for f in PERSONA_FIELDS)
        if any(_absent(v) for v in values) or values == reference:
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# Unit layer: no browser
# ---------------------------------------------------------------------------


def _agreeing_results():
    row = {"canvas": "abc:100", "canvas_png": "png:100", "audio": "def:5000", "webgl_vendor": "Google Inc.",
           "webgl_renderer": "ANGLE", "time_zone": "America/New_York", "language": "en-US",
           "languages": "en-US|en", "user_agent": "UA", "screen_width": 1920, "screen_height": 1080,
           "screen_avail_width": 1920, "screen_avail_height": 1040, "device_pixel_ratio": 1,
           "fonts_check": True, "font_width_arial": 503.5, "font_width_segoe_ui": 498.25,
           "font_width_fallback": 600.0, "screen_x": 0, "screen_y": 0}
    return {name: dict(row, name=name) for name in CONTEXTS}


ALL_FIELDS = tuple(f for group in GROUPS.values() for f in group)


@pytest.mark.unit
def test_contexts_that_agree_have_no_mismatch():
    assert mismatches(_agreeing_results(), ALL_FIELDS) == []


@pytest.mark.unit
def test_an_iframe_with_another_canvas_is_reported():
    """The known-bad input: the iframe's process drew with another seed."""
    results = _agreeing_results()
    results["oop_127_0_0_3"]["canvas"] = "0f0f0f:100"
    found = mismatches(results, GROUPS["canvas"])
    assert len(found) == 1
    assert found[0].startswith("oop_127_0_0_3: canvas = '0f0f0f:100'")
    results["oop_127_0_0_3"]["canvas"] = "abc:100"
    results["oop_127_0_0_3"]["canvas_png"] = "other-png:100"
    assert [p.split(":")[0] for p in mismatches(results, GROUPS["canvas"])] == ["oop_127_0_0_3"]


@pytest.mark.unit
@pytest.mark.parametrize("field,wrong", [
    ("language", "it-IT"), ("time_zone", "UTC"), ("screen_width", 1366), ("device_pixel_ratio", 2),
    ("font_width_arial", 480.0), ("audio", "ffff:5000"), ("webgl_renderer", "llvmpipe"),
])
def test_every_field_that_differs_in_one_iframe_is_reported(field, wrong):
    results = _agreeing_results()
    results["nested_127_0_0_1"][field] = wrong
    group = [g for g, fields in GROUPS.items() if field in fields][0]
    found = mismatches(results, GROUPS[group])
    assert [p.split(":")[0] for p in found] == ["nested_127_0_0_1"], found


@pytest.mark.unit
def test_an_absence_is_not_an_agreement():
    """Two empty answers are not equal values; and a top with no answer compares against nothing."""
    results = _agreeing_results()
    for name in CONTEXTS:
        results[name]["canvas"] = "error:SecurityError"
    found = mismatches(results, GROUPS["canvas"])
    assert any(p.startswith("top: canvas is absent") for p in found)
    results = _agreeing_results()
    results["oop_127_0_0_2"]["language"] = None
    results["oop_127_0_0_3"]["language"] = ""
    found = mismatches(results, GROUPS["locale_and_agent"])
    assert [p.split(":")[0] for p in found] == ["oop_127_0_0_2", "oop_127_0_0_3"]


@pytest.mark.unit
def test_a_document_that_never_reported_is_a_mismatch():
    results = _agreeing_results()
    del results["oop_127_0_0_3"]
    assert mismatches(results, GROUPS["screen"]) == ["oop_127_0_0_3: the document reported nothing"]
    assert mismatches({}, GROUPS["screen"]) == ["the top document reported nothing"]


@pytest.mark.unit
def test_two_seeds_that_give_the_same_machine_are_reported():
    """The known-bad input of the negative case: the iframe's process ignores the seed."""
    first = _agreeing_results()
    second = _agreeing_results()
    for name in CONTEXTS:
        second[name].update(webgl_renderer="ANGLE (NVIDIA)", screen_width=2560, screen_height=1440)
    assert same_persona(first, second) == []
    second["oop_127_0_0_3"].update(webgl_renderer="ANGLE", screen_width=1920, screen_height=1080)
    assert same_persona(first, second) == ["oop_127_0_0_3"]
    second["nested_127_0_0_1"]["screen_height"] = None
    assert same_persona(first, second) == ["nested_127_0_0_1", "oop_127_0_0_3"]


@pytest.mark.unit
def test_screen_position_is_not_a_compared_field():
    assert "screen_x" not in ALL_FIELDS and "screen_y" not in ALL_FIELDS
    results = _agreeing_results()
    results["oop_127_0_0_2"]["screen_x"] = 317
    assert mismatches(results, ALL_FIELDS) == []


@pytest.mark.unit
def test_the_top_page_names_three_sites_and_a_nested_frame():
    html = top_page(8123).decode("utf-8")
    assert html.count("<iframe") == 2
    for host in (HOST_OOP, HOST_OOP_2):
        assert "http://%s:8123/probe/" % host in html
    # The nested document is on the top's own site, handed to the second site's page.
    assert "child=http://%s:8123/probe/nested_127_0_0_1" % HOST_TOP in html
    assert "window.__parity_done" in html and "collect('top')" in html
    assert "length >= %d;" % len(CONTEXTS) in html


@pytest.mark.unit
def test_a_probe_page_reads_the_documents_own_values_and_reports_to_the_top():
    leaf = probe_page("oop_127_0_0_3").decode("utf-8")
    assert "<iframe" not in leaf
    assert "window.top.postMessage" in leaf and 'collect("oop_127_0_0_3")' in leaf
    with_child = probe_page("oop_127_0_0_2", "http://127.0.0.1:1/probe/x").decode("utf-8")
    assert '<iframe id="child" src="http://127.0.0.1:1/probe/x"' in with_child
    for surface in ("toDataURL", "getImageData", "OfflineAudioContext", "WEBGL_debug_renderer_info",
                    "resolvedOptions().timeZone", "navigator.languages", "navigator.userAgent",
                    "screen.availHeight", "devicePixelRatio", "document.fonts.check"):
        assert surface in with_child, surface
    # Secure-context-only APIs are not used: 127.0.0.2 and .3 are not necessarily one.
    assert "crypto.subtle" not in with_child


@pytest.mark.unit
def test_the_strategy_one_prefs_leave_the_process_count_alone():
    assert STRATEGY_1_PREFS == {"fission.autostart": True, "fission.webContentIsolationStrategy": 1}
    assert not any("processCount" in key for key in STRATEGY_1_PREFS)


@pytest.mark.integration
def test_the_server_answers_every_host_with_the_page_of_its_path():
    server, port = _serve()
    try:
        import urllib.request

        for host in (HOST_TOP, HOST_OOP, HOST_OOP_2):
            assert urllib.request.urlopen("http://%s:%d/" % (host, port), timeout=5).read() == top_page(port)
        top = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5).read()
        assert top == top_page(port)
        leaf = urllib.request.urlopen("http://127.0.0.1:%d/probe/oop_127_0_0_3" % port, timeout=5).read()
        assert leaf == probe_page("oop_127_0_0_3")
        nested = urllib.request.urlopen(
            "http://127.0.0.1:%d/probe/oop_127_0_0_2?child=http://127.0.0.1:%d/probe/n" % (port, port),
            timeout=5).read()
        assert nested == probe_page("oop_127_0_0_2", "http://127.0.0.1:%d/probe/n" % port)
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# E2E layer
# ---------------------------------------------------------------------------


class _Servers:
    """The same handler on 127.0.0.1, 127.0.0.2 and 127.0.0.3, all on one port."""

    def __init__(self, servers):
        self.servers = servers
        self.port = servers[0].server_address[1]

    def shutdown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()


def _serve():
    """One page server per host, on one port; ``(servers, port)``.

    Each host gets its own socket (no ``0.0.0.0``: that would offer the pages to the network
    and ask Windows Firewall about it). The kernel picks the port for the first one, which
    stays bound; the other two bind the same number, and a number that is taken on one of
    them is given up for a new one.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                body = top_page(self.server.server_address[1])
            elif url.path.startswith("/probe/"):
                name = url.path[len("/probe/"):]
                child = None
                for part in url.query.split("&"):
                    if part.startswith("child="):
                        child = part[len("child="):]
                body = probe_page(name, child)
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    last = None
    for _ in range(20):
        servers = []
        try:
            first = LocalServer((HOST_TOP, 0), Handler)
            servers.append(first)
            port = first.server_address[1]
            for host in (HOST_OOP, HOST_OOP_2):
                servers.append(LocalServer((host, port), Handler))
        except OSError as error:
            last = error
            for server in servers:
                server.server_close()
            continue
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return _Servers(servers), port
    raise RuntimeError("no port free on all of %s, %s, %s: %s" % (HOST_TOP, HOST_OOP, HOST_OOP_2, last))


#: Run in the top document: ask the iframe on the third site to spin, report the longest gap
#: between two ticks of a 10 ms timer. What a site can measure about its own process, with no
#: help from the driver.
SPIN_PROBE_JS = """(spin) => new Promise(resolve => {
  let last = performance.now(), longest = 0;
  const id = setInterval(() => {
    const now = performance.now();
    longest = Math.max(longest, now - last);
    last = now;
  }, 10);
  document.getElementById('oop2').contentWindow.postMessage({fission_busy: spin}, '*');
  setTimeout(() => { clearInterval(id); resolve(longest); }, 1500);
})"""


def _run(binary: str, seed: int, strategy: int) -> dict:
    """Open the page once; return what the four documents read and the longest timer gap the top saw."""
    from invisible_playwright import InvisiblePlaywright

    prefs = dict(STRATEGY_1_PREFS if strategy == 1 else STRATEGY_0_PREFS)
    server, port = _serve()
    try:
        with InvisiblePlaywright(seed=seed, binary_path=binary, humanize=False, headless=True,
                                 locale="en-US", timezone="America/New_York",
                                 extra_prefs=prefs) as browser:
            page = browser.new_context().new_page()
            page.goto("http://%s:%d/" % (HOST_TOP, port), wait_until="load", timeout=60_000)
            deadline = time.monotonic() + 60.0
            while not page.evaluate("() => window.__parity_done"):
                if time.monotonic() >= deadline:
                    break
                page.wait_for_timeout(200)
            results = page.evaluate("() => window.__parity")
            longest_gap = page.evaluate(SPIN_PROBE_JS, SPIN_MS)
    finally:
        server.shutdown()
    return {"results": results, "longest_gap_ms": longest_gap, "port": port}


@pytest.fixture(scope="module", params=[
    pytest.param(0, id="strategy0"),
    pytest.param(1, id="strategy1"),
])
def strategy(request):
    return request.param


@pytest.fixture(scope="module")
def run(firefox_binary, strategy):
    return _run(firefox_binary, SEED, strategy)


@pytest.fixture(scope="module")
def run_other_seed(firefox_binary, strategy):
    return _run(firefox_binary, OTHER_SEED, strategy)


@pytest.mark.e2e
def test_every_context_reported(run):
    assert sorted(run["results"]) == sorted(CONTEXTS), run["results"].keys()


@pytest.mark.e2e
def test_the_iframes_run_where_the_strategy_puts_them(run, strategy):
    """The precondition of the strategy-1 case, read by the page itself.

    The iframe on the third site spins for 400 ms. In a process shared with the top the top's
    10 ms timer stalls for about that long; in a process of its own it does not.
    """
    gap = run["longest_gap_ms"]
    if strategy == 0:
        assert gap >= SHARED_PROCESS_GAP_MS, (
            "strategy 0 keeps the iframes in the top's process, so a %d ms spin should stall "
            "the top's timer; the longest gap was %.0f ms" % (SPIN_MS, gap))
    else:
        assert gap < OWN_PROCESS_GAP_MS, (
            "strategy 1 gives the third site a process of its own, so a %d ms spin should not "
            "stall the top's timer; the longest gap was %.0f ms" % (SPIN_MS, gap))


@pytest.mark.e2e
@pytest.mark.parametrize("group", sorted(GROUPS))
def test_the_identity_is_the_same_in_every_frame(run, group):
    if group == "webgl" and run["results"].get("top", {}).get("webgl_renderer") is None:
        pytest.skip("this machine gives the top document no WebGL renderer to compare against")
    found = mismatches(run["results"], GROUPS[group])
    assert not found, "identity differs between the top document and its iframes:\n  " + "\n  ".join(found)


@pytest.mark.e2e
def test_another_seed_is_another_machine_in_every_frame(run, run_other_seed):
    """The negative of the parity: equal values are only worth something if the seed moves them.

    Together with the parity inside each launch this says: every frame reads the machine of ITS
    seed, not one machine shared by every seed.
    """
    for results in (run["results"], run_other_seed["results"]):
        assert mismatches(results, PERSONA_FIELDS) == []
    same = same_persona(run["results"], run_other_seed["results"])
    assert not same, "seed %d and seed %d read the same machine in: %s" % (SEED, OTHER_SEED, same)
