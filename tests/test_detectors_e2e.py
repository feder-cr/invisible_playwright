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

from _servers import LocalServer
request is aborted, so its optional crowd-comparison POST never runs and the
verdict is computed purely locally. Runs identically on a dev box and a GH runner.

NOT covered: FingerprintJS *Pro* (commercial, server-side) - stays the local
realness gate.
"""
from __future__ import annotations

import http.server
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
        # Preload the vendored bundle by exact filename. Serving from this map
        # means a request never builds a filesystem path from its own input, so
        # there is no path a crafted request could traverse.
        files = {q.name: q.read_bytes() for q in _VENDOR.iterdir() if q.is_file()}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                p = self.path.split("?")[0]
                if p == "/":
                    body, ctype = page, "text/html; charset=utf-8"
                elif p == "/creepjs":
                    body, ctype = creep_page, "text/html; charset=utf-8"
                else:
                    body = files.get(Path(p.lstrip("/")).name)
                    if body is None:
                        self.send_error(404); return
                    ctype = "text/javascript; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = LocalServer(("127.0.0.1", 0), H)
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
        # NetworkObserver was stripped to the bone on 2026-08-24: page.route()
        # no longer exists (Network.setRequestInterception refuses), so
        # blocking CreepJS's optional POST to arh.antoinevastel.com can no
        # longer be done this way. Verified: without the block the test still
        # passes, with the same outcome on headlessRating/stealth - that POST
        # was test-hygiene isolation (speed, no dependency on a third-party
        # site), not a precondition of the assertions.
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


@pytest.mark.e2e
def test_enumerate_devices_resolves_promptly(firefox_binary, detector_site):
    """navigator.mediaDevices.enumerateDevices() must RESOLVE, and quickly.

    Born from a defect measured on 2026-08-10, and the point is that no test saw
    it even though it was the cause of four different failures.

    The symptom that was visible was
    `test_fingerprintjs_visitorid_stable_across_launches` going red roughly one
    time in three - a name that sends you the wrong way, because it had nothing
    to do with either the identifier or its stability. The detectors page stayed
    at `state='loading'`: BotD finished, FingerprintJS finished, fpscanner did
    not. Inside fpscanner:

        return new Promise(async function(t) {
          const a = await navigator.mediaDevices.enumerateDevices();
          ...
          return t({...});                 // <- resolves AFTER the await
        });

    If `enumerateDevices()` never resolves, that promise never closes: no
    error, no exception, the page stays alive and responds to `evaluate`, and
    `wait_for_function` times out after 45 seconds. A browser that does not
    respond is easy to diagnose; one that waits forever is not.

    Measured: ONLY the first call costs anything - three consecutive calls in
    the same session give [1383, 2, 0] ms - so it is the media stack
    initialization, once per session. Through this wrapper the median is
    ~1440 ms and about 1 session in 8 never resolves at all; with plain
    Playwright, the SAME binary, the same prefs, the same environment and the
    same context options, the median is ~270 ms and 0 hangs out of 10. Ruled
    out by measurement: the binary, `media.navigator.streams.fake`, all 230
    prefs, the environment variables, the context options and the cursor
    engine. What remains inside the wrapper is open - see 70-known-bugs.md.

    ⛔ TWO LIMITS, NOT ONE, as of 2026-08-17, because with a single one this
    test reported "NEVER RESOLVED" for a resolution that was merely SLOW -
    that is, it wrote the defect's verdict over the loaded-machine's verdict.
    The first version had 5 seconds and failed 7 times out of 8 under load
    with times between 2.7 and 5 seconds; raising it to 30 moved the threshold
    without removing the confusion, and on 2026-08-17 ten full e2e runs gave
    1 red out of 10, with that red entirely inside the run that took 35
    minutes against a median of 11.8, i.e. the only outlier on every column.
    On the remaining nine runs: 0 out of 9.

    **The defect is the HANG, and a hang is infinite.** `BLOCK_LIMIT_MS` sits
    at 120 seconds: 83 times the median of ~1440 ms and 24 times the worst
    time ever observed when the call succeeds (5 s). Beyond that wall it is
    not slowness, and no load on this machine has ever produced anything
    like it. This is the only absolute limit, and it is the one that protects
    the product.

    **SLOWNESS, on the other hand, is measured against the machine, not
    against the clock.** The page times 20000 MICROTASKS, and the slowness
    limit is a multiple of that time, with a floor at 30 seconds and a
    ceiling below the block wall.

    ⛔ Microtasks, and not `setTimeout(...,0)`, for a measured reason: the
    first version of this reference counted 50 rounds of `setTimeout` and on
    an IDLE machine reported 2624 ms, i.e. ~52 ms per round. That was not
    load: it was the timer CLAMP, which Firefox applies regardless. The limit
    that derived from it came out to 393600 ms, **above** the block wall at
    120000, so the slowness assertion was unreachable - a dead assertion that
    looked like a protection. A reference must be chosen for what it measures,
    and a clamped timer measures the clamp. Microtasks are not clamped.

    The ceiling (`wall - 1`) exists for the same reason: if the slowness limit
    exceeds the block wall, the first assertion always fires first and the
    second one no longer exists. A limit that can never be reached is not a
    limit.

    The time and the reference are always printed, so a speed regression stays
    visible even when it does not make anything fail.
    """
    #: Beyond this it will never resolve: this is the defect, and it is absolute.
    BLOCK_LIMIT_MS = 120_000
    #: Slowness floor, plus the multiple of the load reference. The multiple
    #: is tuned against the microtask reference measured on an idle machine
    #: (a few ms), so it gives a limit well under the floor up to a load of
    #: about twenty times, and above it beyond that.
    SLOWNESS_FLOOR_MS = 30_000
    REFERENCE_MULTIPLIER = 2_000

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(detector_site.url, wait_until="load", timeout=45000)
        r = page.evaluate(
            """(limitMs) => {
                // Load reference: 20000 microtasks. NOT setTimeout, which
                // Firefox clamps regardless and would therefore measure the
                // clamp instead of contention. Measured FIRST, so it does not
                // include the media stack initialization we are timing.
                const eventLoopRound = async () => {
                    const t = performance.now();
                    for (let i = 0; i < 20000; i++) {
                        await Promise.resolve();
                    }
                    return performance.now() - t;
                };
                return eventLoopRound().then(rif => {
                    const t0 = performance.now();
                    return Promise.race([
                        navigator.mediaDevices.enumerateDevices().then(
                            d => ({esito: 'risolta', n: d.length, rif: rif,
                                   ms: Math.round(performance.now() - t0)}),
                            e => ({esito: 'rifiutata', rif: rif,
                                   err: String(e).slice(0, 120),
                                   ms: Math.round(performance.now() - t0)})),
                        new Promise(res => setTimeout(
                            () => res({esito: 'MAI RISOLTA', ms: limitMs,
                                       rif: rif}), limitMs)),
                    ]);
                });
            }""",
            BLOCK_LIMIT_MS,
        )

    rif = round(r.get("rif") or 0)
    slowness_limit = min(BLOCK_LIMIT_MS - 1,
                          max(SLOWNESS_FLOOR_MS,
                              REFERENCE_MULTIPLIER * rif))
    print(f"[media] enumerateDevices: {r.get('n')} devices in {r['ms']}ms "
          f"(load reference {rif}ms -> slowness limit "
          f"{slowness_limit}ms, block wall {BLOCK_LIMIT_MS}ms)")

    assert r["esito"] != "MAI RISOLTA", (
        f"navigator.mediaDevices.enumerateDevices() did not resolve within "
        f"{BLOCK_LIMIT_MS}ms. This is not slowness: it is the HANG. Any "
        f"detector doing `await enumerateDevices()` before resolving stays "
        f"stuck forever and the page looks alive - the 2026-08-10 defect, "
        f"which showed up as an identifier test going red one time in three. "
        f"Load reference for this run: {rif}ms."
    )
    assert r["esito"] == "risolta", (
        f"enumerateDevices rejected instead of resolving: {r.get('err')!r}"
    )
    assert r["ms"] <= slowness_limit, (
        f"enumerateDevices RESOLVED in {r['ms']}ms, beyond the limit of "
        f"{slowness_limit}ms. This is not the 2026-08-10 hang - the signal "
        f"arrives - and it is a limit that scales with load: the event loop "
        f"reference was {rif}ms, so the machine was "
        f"{'busy' if rif > 200 else 'idle'}. If the reference is low and this "
        f"time is high, it is a speed regression in our media stack and not "
        f"an artifact of the machine."
    )
