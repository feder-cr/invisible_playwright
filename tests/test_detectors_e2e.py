"""E2E: run the REAL open-source detectors against the patched binary, on CI.

Instead of our own hand-rolled signal checks, this loads the actual detection
libraries and uses their FULL API surface:

  * BotD (@fingerprintjs/botd, MIT) - the client-side bot detector that
    FingerprintJS Pro itself uses. We assert the aggregate verdict
    (``detect().bot == False``) AND every one of its ~18 individual detectors
    (``getDetections()``) returns ``bot == False``.
  * FingerprintJS open-source (MIT) - ``get()`` must return a ``visitorId``
    that is STABLE across two fresh launches with the same seed, and a RICH
    component set (the fingerprint surface is real, not a stub).
  * fpscanner (antoinevastel/fpscanner 1.0.6, MIT) - ``collectFingerprint()``
    runs ~21 bot-detection rules in the browser. We assert the **engine-agnostic**
    subset (webdriver / selenium / bot-UA / platform / timezone / language) is
    clean. We deliberately do NOT assert the Chrome/GPU-only rules (hasCDP,
    hasPlaywright, hasSwiftshaderRenderer, hasMissingChromeObject, …): they're
    trivially clean on Firefox, and the GPU ones can legitimately fire on a
    software-WebGL CI host (Xvfb/llvmpipe) - asserting them would false-red.
  * CreepJS (abrahamjuliot/creepjs, MIT, pinned) - the gold-standard Firefox-aware
    headless/stealth/lie detector. It exposes its result on ``window.Fingerprint``.
    We assert ``headlessRating == 0`` (webdriver + headless-UA tells) and the
    JS-proxy stealth tells are absent. ``stealthRating`` / ``totalLies`` /
    ``likeHeadlessRating`` are LOGGED, not hard-asserted, because some of their
    sub-signals (hasBadWebGL, prefers-light-color) are GPU/theme-sensitive and
    differ on a GPU-less CI host.

Everything is hermetic: the libraries are vendored (tests/vendor/) and served
from a localhost HTTP server - no external CDN call. For CreepJS, every non-local
request is aborted, so its optional crowd-comparison POST never runs and the
verdict is computed purely locally. Runs identically on a dev box and a GH runner.

NOT covered: FingerprintJS *Pro* (commercial, server-side) - stays the local
realness gate.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from invisible_playwright import InvisiblePlaywright

_VENDOR = Path(__file__).parent / "vendor"
_BOTD = "botd-2.0.0.esm.js"
_FPJS = "fingerprintjs-5.2.0.umd.min.js"
_FPSCANNER = "fpscanner-1.0.6.es.js"
_CREEPJS = "creepjs-10aa672.js"  # pinned abrahamjuliot/creepjs@10aa6724

# fpscanner rules that are MEANINGFUL on Firefox and GPU-independent - these must
# stay clean. The omitted rules are Chrome-only (hasCDP/hasPlaywright/
# hasMissingChromeObject/hasHighCPUCount/hasImpossibleDeviceMemory/
# headlessChromeScreenResolution) or GPU-sensitive on a software-WebGL CI host
# (hasSwiftshaderRenderer/hasGPUMismatch/hasMismatchWebGLInWorker).
_FPSCANNER_AGNOSTIC = [
    "hasWebdriver", "hasWebdriverIframe", "hasWebdriverWorker", "hasWebdriverWritable",
    "hasSeleniumProperty", "hasBotUserAgent", "hasPlatformMismatch",
    "hasMismatchLanguages", "hasUTCTimezone", "hasMismatchPlatformIframe",
    "hasMismatchPlatformWorker", "hasInconsistentEtsl",
]

_PAGE = f"""<!doctype html><html><head><meta charset="utf-8">
<title>detectors</title>
<script src="/{_FPJS}"></script>
</head><body><h1 id="state">loading</h1>
<script type="module">
window.__botd = null; window.__fp = null; window.__fps = null; window.__err = "";
(async () => {{
  try {{
    const Botd = await import("/{_BOTD}");
    const botd = await Botd.load();
    const verdict = botd.detect();
    const raw = botd.getDetections() || {{}};
    const detections = {{}};
    for (const k in raw) detections[k] = {{ bot: raw[k].bot, botKind: raw[k].botKind || null }};
    window.__botd = {{ bot: verdict.bot, botKind: verdict.botKind || null, detections }};
  }} catch (e) {{ window.__err += " botd:" + e; }}
  try {{
    const fp = await FingerprintJS.load();
    const r = await fp.get();
    const keys = Object.keys(r.components || {{}});
    const errored = keys.filter(k => r.components[k] && "error" in r.components[k]);
    window.__fp = {{ visitorId: r.visitorId, componentKeys: keys, erroredComponents: errored }};
  }} catch (e) {{ window.__err += " fp:" + e; }}
  try {{
    const M = await import("/{_FPSCANNER}");
    const scanner = new M.default();
    const fp = await scanner.collectFingerprint({{ encrypt: false }});
    window.__fps = {{ fastBotDetection: fp.fastBotDetection, details: fp.fastBotDetectionDetails }};
  }} catch (e) {{ window.__err += " fps:" + e; }}
  document.getElementById("state").textContent = "done";
}})();
</script></body></html>"""

# CreepJS gets its own page: creep.js is a plain `defer` script that runs on load
# and populates window.Fingerprint. A minimal DOM is enough (the rich report DOM
# is only for the visual page, not the computation).
_CREEP_PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>creep</title></head>
<body><div id="fingerprint-data"></div><script src="/{_CREEPJS}" defer></script></body></html>"""


class _DetectorSite:
    """Localhost server: `/` → BotD+FPJS+fpscanner page, `/creepjs` → CreepJS page,
    `/<file>` → the vendored bundle."""

    def __init__(self):
        page = _PAGE.encode()
        creep_page = _CREEP_PAGE.encode()
        vendor = _VENDOR

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                p = self.path.split("?")[0]
                if p == "/":
                    body, ctype = page, "text/html; charset=utf-8"
                elif p == "/creepjs":
                    body, ctype = creep_page, "text/html; charset=utf-8"
                else:
                    f = vendor / Path(p.lstrip("/")).name
                    if not f.is_file():
                        self.send_error(404); return
                    body = f.read_bytes()
                    ctype = "text/javascript; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    @property
    def creep_url(self):
        return f"http://127.0.0.1:{self.port}/creepjs"

    def close(self):
        self._srv.shutdown()


@pytest.fixture(scope="module")
def detector_site():
    s = _DetectorSite()
    yield s
    s.close()


def _run_detectors(firefox_binary, url):
    """Launch the binary, load the page, return (botd, fp, fps, err)."""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=45000)
        page.wait_for_function(
            "() => document.getElementById('state').textContent === 'done'",
            timeout=45000,
        )
        botd = page.evaluate("() => window.__botd")
        fp = page.evaluate("() => window.__fp")
        fps = page.evaluate("() => window.__fps")
        err = page.evaluate("() => window.__err")
    return botd, fp, fps, err


def _run_creepjs(firefox_binary, creep_url):
    """Launch the binary, run CreepJS fully offline, return its headless result."""
    _EV = """() => {
      const f = window.Fingerprint;
      if (!f || !f.headless) return { ready: false };
      const h = f.headless;
      return {
        ready: true,
        headlessRating: h.headlessRating,
        stealthRating: h.stealthRating,
        likeHeadlessRating: h.likeHeadlessRating,
        headless: h.headless || {},
        stealth: h.stealth || {},
        totalLies: (f.lies && f.lies.totalLies) || 0,
      };
    }"""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        # truly offline: abort every non-loopback request (CreepJS's optional
        # crowd-comparison POST to arh.antoinevastel.com never runs).
        page.route(
            "**/*",
            lambda r: r.abort() if "127.0.0.1" not in r.request.url else r.continue_(),
        )
        page.goto(creep_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_function(
            "() => !!(window.Fingerprint && window.Fingerprint.headless)",
            timeout=60000,
        )
        return page.evaluate(_EV)


@pytest.mark.e2e
def test_botd_no_detector_flags_automation(firefox_binary, detector_site):
    """The real BotD must not flag the build - aggregate AND every one of its
    individual detectors (webDriver/userAgent/appVersion/plugins/process/...)."""
    botd, _fp, _fps, err = _run_detectors(firefox_binary, detector_site.url)
    assert botd is not None, f"BotD produced no result (err:{err!r})"
    assert botd.get("bot") is False, (
        f"BotD aggregate flagged a bot: botKind={botd.get('botKind')!r}"
    )
    detections = botd.get("detections") or {}
    assert detections, f"BotD getDetections() returned nothing (err:{err!r})"
    flagged = {k: v.get("botKind") for k, v in detections.items() if v.get("bot")}
    assert not flagged, f"BotD individual detectors flagged automation: {flagged}"


@pytest.mark.e2e
def test_fingerprintjs_visitorid_stable_across_launches(firefox_binary, detector_site):
    """FingerprintJS visitorId must be present and identical across two fresh
    launches with the same seed - a real browser is stable; an over-randomized
    spoof drifts (and a drifting fingerprint is itself a bot tell)."""
    _b1, fp1, _f1, err1 = _run_detectors(firefox_binary, detector_site.url)
    _b2, fp2, _f2, err2 = _run_detectors(firefox_binary, detector_site.url)
    assert fp1 and fp1.get("visitorId"), f"no visitorId on run 1 (err:{err1!r})"
    assert fp2 and fp2.get("visitorId"), f"no visitorId on run 2 (err:{err2!r})"
    assert fp1["visitorId"] == fp2["visitorId"], (
        f"FingerprintJS visitorId drifted across launches: "
        f"{fp1['visitorId']!r} != {fp2['visitorId']!r} (per-session entropy = bot tell)"
    )


@pytest.mark.e2e
def test_fingerprintjs_collects_rich_fingerprint(firefox_binary, detector_site):
    """FingerprintJS must collect a RICH component surface (a real browser
    exposes many signals; a stripped/blocked surface is itself suspicious)."""
    _b, fp, _f, err = _run_detectors(firefox_binary, detector_site.url)
    assert fp and fp.get("visitorId"), f"FingerprintJS produced no id (err:{err!r})"
    keys = fp.get("componentKeys") or []
    assert len(keys) >= 15, (
        f"FingerprintJS collected only {len(keys)} components - surface too thin "
        f"(suppressed signals are themselves a tell): {keys}"
    )


@pytest.mark.e2e
def test_fpscanner_no_automation_rules(firefox_binary, detector_site):
    """fpscanner's engine-agnostic bot rules (webdriver/selenium/bot-UA/platform/
    timezone/language) must all be clean. The Chrome/GPU-only rules are ignored
    on purpose (see module docstring) - they false-red on a software-WebGL host."""
    _b, _fp, fps, err = _run_detectors(firefox_binary, detector_site.url)
    assert fps is not None, f"fpscanner produced no result (err:{err!r})"
    details = fps.get("details") or {}
    assert details, f"fpscanner returned no detection details (err:{err!r})"
    flagged = [
        k for k in _FPSCANNER_AGNOSTIC
        if details.get(k) and details[k].get("detected")
    ]
    assert not flagged, (
        f"fpscanner flagged automation on engine-agnostic rules: {flagged} "
        f"(full details: { {k: v for k, v in details.items() if v.get('detected')} })"
    )


@pytest.mark.e2e
def test_creepjs_headless_and_proxy_clean(firefox_binary, detector_site):
    """CreepJS (Firefox-aware) must see no headless tell and no JS-proxy stealth
    tell. ``headlessRating`` aggregates webDriverIsOn + headless-UA checks (all
    GPU-independent). The proxy/runtime stealth sub-signals (hasIframeProxy,
    hasToStringProxy, hasBadChromeRuntime) must be false - a spoof implemented
    with a JS Proxy is exactly what CreepJS catches. stealthRating/totalLies/
    likeHeadlessRating are GPU/theme-sensitive, so we log them, not assert."""
    r = _run_creepjs(firefox_binary, detector_site.creep_url)
    assert r and r.get("ready"), f"CreepJS never populated window.Fingerprint: {r!r}"
    print(
        f"[creepjs] headlessRating={r['headlessRating']} stealthRating={r['stealthRating']} "
        f"likeHeadlessRating={r['likeHeadlessRating']} totalLies={r['totalLies']} "
        f"headless={r['headless']} stealth={r['stealth']}"
    )
    assert r["headlessRating"] == 0, (
        f"CreepJS headless tells fired: headless={r['headless']} "
        f"(headlessRating={r['headlessRating']})"
    )
    stealth = r.get("stealth") or {}
    proxy_tells = {
        k: stealth.get(k)
        for k in ("hasIframeProxy", "hasToStringProxy", "hasBadChromeRuntime")
        if stealth.get(k)
    }
    assert not proxy_tells, f"CreepJS JS-proxy stealth tells fired: {proxy_tells}"
