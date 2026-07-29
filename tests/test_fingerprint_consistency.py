"""Fingerprint consistency / lie-detection tests.

Complementary to test_fingerprint_surface.py: those tests ask "do you
look like a real browser?" - these ask "are your fingerprint surfaces
INTERNALLY CONSISTENT?"

Anti-bot systems catch spoofers not by checking each signal in
isolation but by cross-checking related signals. If you spoof UA to
"Windows" but leave navigator.platform as "Linux x86_64", or you spoof
WebGL renderer in the main thread but not in a Web Worker, the
inconsistency proves the spoof is fake.

Sources studied (all FOSS, MIT-licensed):
  - creepjs/src/lies/index.ts   - the canonical lie detector
  - creepjs/src/worker/index.ts - main-vs-worker scope cross-check
  - creepjs/src/math/index.ts   - Math.x(p) deterministic equality
  - creepjs/src/navigator/index.ts - UA/platform/oscpu invariants
  - niespodd/browser-fingerprinting README - worker hwConcurrency,
                                              plugin chain, perf.timeOrigin

Everything runs against `about:blank` with NO network and NO proxy.

Run only this file:
    pytest tests/test_fingerprint_consistency.py -m e2e -v
"""
from __future__ import annotations

import pytest

from invisible_playwright import InvisiblePlaywright


PIN = {
    "screen.width": 1920,
    "screen.height": 1080,
    "screen.avail_width": 1920,
    "screen.avail_height": 1040,
    "screen.dpr": 1.0,
    "hardware.concurrency": 8,
    "audio.sample_rate": 48000,
    "audio.max_channel_count": 2,
}


@pytest.fixture(scope="module")
def page(firefox_binary):
    with InvisiblePlaywright(
        seed=42,
        pin=PIN,
        binary_path=firefox_binary,
        headless=True,
    ) as browser:
        ctx = browser.new_context()
        p = ctx.new_page()
        p.goto("about:blank", timeout=30_000)
        yield p


def _ev(page, expr):
    return page.evaluate(expr)


# ===========================================================================
# 1. Math determinism - same input MUST yield same output
# Source: creepjs/src/math/index.ts
# A wrapper that adds noise to Math.* (canvas-spoofing prefs) exposes
# itself here: two consecutive calls with the same input must be
# byte-identical.
# ===========================================================================


@pytest.mark.e2e
@pytest.mark.parametrize("fn,arg", [
    ("cos", "1e308"),
    ("acos", "0.5"),
    ("asin", "0.5"),
    ("atan", "Math.PI"),
    ("atanh", "0.5"),
    ("cbrt", "Math.PI"),
    ("cosh", "Math.PI"),
    ("exp", "Math.PI"),
    ("expm1", "Math.PI"),
    ("log", "Math.PI"),
    ("log1p", "Math.PI"),
    ("log10", "Math.PI"),
    ("sin", "Math.PI"),
    ("sinh", "Math.PI"),
    ("sqrt", "Math.PI"),
    ("tan", "Math.PI"),
    ("tanh", "Math.PI"),
])
def test_math_determinism(page, fn, arg):
    """Math.<fn>(<arg>) must return the same value across 100 calls."""
    first, last, all_equal = _ev(page, f"""() => {{
        const r = [];
        for (let i = 0; i < 100; i++) r.push(Math.{fn}({arg}));
        return [r[0], r[99], r.every(x => Object.is(x, r[0]))];
    }}""")
    assert all_equal, (
        f"Math.{fn}({arg}) drifts across calls: first={first}, last={last}"
    )


@pytest.mark.e2e
def test_math_pow_two_arg_determinism(page):
    ok = _ev(page, """() => {
        const a = Math.pow(Math.PI, 2);
        for (let i = 0; i < 50; i++) {
            if (!Object.is(Math.pow(Math.PI, 2), a)) return false;
        }
        return true;
    }""")
    assert ok


# ===========================================================================
# 2. Worker scope vs main thread - navigator properties MUST agree
# Source: creepjs/src/worker/index.ts
# ===========================================================================


def _worker_navigator_dict(page, props):
    expr = """async (props) => {
        const code = `
            self.onmessage = (e) => {
                const out = {};
                for (const p of e.data) {
                    try { out[p] = self.navigator[p]; }
                    catch (err) { out[p] = '<error: ' + err.message + '>'; }
                }
                if (out.languages && Array.isArray(out.languages)) {
                    out.languages = [...out.languages];
                }
                self.postMessage(out);
            };
        `;
        const blob = new Blob([code], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);
        const worker = new Worker(url);
        try {
            const result = await new Promise((resolve, reject) => {
                worker.onmessage = (e) => resolve(e.data);
                worker.onerror = (e) => reject(new Error(e.message));
                worker.postMessage(props);
                setTimeout(() => reject(new Error('worker timeout')), 5000);
            });
            return result;
        } finally {
            worker.terminate();
            URL.revokeObjectURL(url);
        }
    }"""
    return page.evaluate(expr, list(props))


@pytest.mark.e2e
def test_worker_userAgent_matches_main(page):
    main = _ev(page, "navigator.userAgent")
    worker = _worker_navigator_dict(page, ("userAgent",))
    assert worker["userAgent"] == main, (
        f"UA drift main vs worker:\n  main:   {main!r}\n  worker: {worker['userAgent']!r}"
    )


@pytest.mark.e2e
def test_worker_hardwareConcurrency_matches_main(page):
    main = _ev(page, "navigator.hardwareConcurrency")
    worker = _worker_navigator_dict(page, ("hardwareConcurrency",))
    assert worker["hardwareConcurrency"] == main


@pytest.mark.e2e
def test_worker_language_matches_main(page):
    main = _ev(page, "navigator.language")
    worker = _worker_navigator_dict(page, ("language",))
    assert worker["language"] == main


@pytest.mark.e2e
def test_worker_languages_matches_main(page):
    main = _ev(page, "[...navigator.languages]")
    worker = _worker_navigator_dict(page, ("languages",))
    assert list(worker["languages"]) == list(main)


@pytest.mark.e2e
def test_worker_platform_matches_main(page):
    main = _ev(page, "navigator.platform")
    worker = _worker_navigator_dict(page, ("platform",))
    assert worker["platform"] == main


# ===========================================================================
# 3. Iframe scope vs window scope
# Source: creepjs/src/lies/index.ts (getBehemothIframe pattern)
# ===========================================================================


def _iframe_navigator_dict(page, props):
    expr = """(props) => {
        const iframe = document.createElement('iframe');
        iframe.style.display = 'none';
        document.body.appendChild(iframe);
        const out = {};
        for (const p of props) {
            try { out[p] = iframe.contentWindow.navigator[p]; }
            catch (e) { out[p] = '<error: ' + e.message + '>'; }
        }
        if (Array.isArray(out.languages)) out.languages = [...out.languages];
        document.body.removeChild(iframe);
        return out;
    }"""
    return page.evaluate(expr, list(props))


@pytest.mark.e2e
def test_iframe_userAgent_matches_window(page):
    main = _ev(page, "navigator.userAgent")
    iframe = _iframe_navigator_dict(page, ("userAgent",))
    assert iframe["userAgent"] == main


@pytest.mark.e2e
def test_iframe_language_matches_window(page):
    main = _ev(page, "navigator.language")
    iframe = _iframe_navigator_dict(page, ("language",))
    assert iframe["language"] == main


@pytest.mark.e2e
def test_iframe_hardwareConcurrency_matches_window(page):
    main = _ev(page, "navigator.hardwareConcurrency")
    iframe = _iframe_navigator_dict(page, ("hardwareConcurrency",))
    assert iframe["hardwareConcurrency"] == main


@pytest.mark.e2e
def test_iframe_screen_matches_window(page):
    main = _ev(page, "[screen.width, screen.height]")
    iframe = _ev(page, """() => {
        const f = document.createElement('iframe');
        f.style.display = 'none';
        document.body.appendChild(f);
        const v = [f.contentWindow.screen.width, f.contentWindow.screen.height];
        document.body.removeChild(f);
        return v;
    }""")
    assert iframe == main


# ===========================================================================
# 4. UA self-consistency (creepjs/src/navigator/index.ts)
# ===========================================================================


@pytest.mark.e2e
def test_navigator_platform_matches_userAgent_OS(page):
    ua = _ev(page, "navigator.userAgent")
    platform = _ev(page, "navigator.platform")
    if "Windows" in ua:
        assert "Win" in platform
    elif "Mac" in ua:
        assert "Mac" in platform
    elif "Linux" in ua or "X11" in ua:
        assert "Linux" in platform or "X11" in platform


@pytest.mark.e2e
def test_navigator_oscpu_matches_userAgent(page):
    """Firefox-only: navigator.oscpu must correlate with UA OS."""
    ua = _ev(page, "navigator.userAgent")
    oscpu = _ev(page, "navigator.oscpu || ''")
    if not oscpu:
        pytest.skip("navigator.oscpu not exposed")
    if "Windows" in ua:
        assert "Windows" in oscpu
    elif "Linux" in ua:
        assert "Linux" in oscpu
    elif "Mac" in ua:
        assert "Mac" in oscpu


# ===========================================================================
# 5. Native function self-toString (creepjs/src/lies/index.ts hasKnownToString)
# ===========================================================================


def _is_native_toString(text, fn_name):
    """Mirror of CreepJS hasKnownToString - accept the engine-specific
    native patterns (single-line on V8, multi-line on SpiderMonkey)."""
    import re as _re
    name = _re.escape(fn_name)
    patterns = [
        rf"^function {name}\(\) \{{ \[native code\] \}}$",
        rf"^function get {name}\(\) \{{ \[native code\] \}}$",
        rf"^function {name}\(\) \{{[\s\S]*\[native code\][\s\S]*\}}$",
        rf"^function get {name}\(\) \{{[\s\S]*\[native code\][\s\S]*\}}$",
    ]
    return any(_re.match(p, text) for p in patterns)


@pytest.mark.e2e
@pytest.mark.parametrize("native_fn,name", [
    ("Function.prototype.toString", "toString"),
    ("Function.prototype.bind", "bind"),
    ("Function.prototype.call", "call"),
    ("Function.prototype.apply", "apply"),
    ("Object.getOwnPropertyDescriptor", "getOwnPropertyDescriptor"),
    ("Object.defineProperty", "defineProperty"),
    ("Array.prototype.slice", "slice"),
    ("JSON.stringify", "stringify"),
])
def test_native_function_self_toString_matches(page, native_fn, name):
    """Each native function's `.toString()` must match its engine's
    native pattern. A Proxy wrapper or function-rewrite leaks here."""
    text = _ev(page, f"{native_fn}.toString()")
    assert _is_native_toString(text, name), (
        f"{native_fn}.toString() not native-shape: {text!r}"
    )


# ===========================================================================
# 6. AudioContext / WebGL determinism
# ===========================================================================


@pytest.mark.e2e
def test_audio_offline_context_deterministic(page):
    """OfflineAudioContext: same graph → byte-identical output."""
    ok = _ev(page, """async () => {
        async function render() {
            const ctx = new (window.OfflineAudioContext ||
                              window.webkitOfflineAudioContext)(1, 5000, 44100);
            const osc = ctx.createOscillator();
            osc.connect(ctx.destination);
            osc.start(0);
            const buf = await ctx.startRendering();
            return Array.from(buf.getChannelData(0).slice(0, 50));
        }
        const a = await render();
        const b = await render();
        return JSON.stringify(a) === JSON.stringify(b);
    }""")
    assert ok


@pytest.mark.e2e
def test_webgl_getParameter_deterministic(page):
    """WebGL parameters must not drift across reads."""
    ok = _ev(page, """() => {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl');
        if (!gl) return false;
        const params = [gl.MAX_TEXTURE_SIZE, gl.MAX_VIEWPORT_DIMS,
                       gl.MAX_RENDERBUFFER_SIZE, gl.MAX_VERTEX_ATTRIBS];
        const ref = JSON.stringify(params.map(p => gl.getParameter(p)));
        for (let i = 0; i < 50; i++) {
            if (JSON.stringify(params.map(p => gl.getParameter(p))) !== ref) {
                return false;
            }
        }
        return true;
    }""")
    assert ok


# ===========================================================================
# 7. Locale ↔ Intl cross-consistency
# ===========================================================================


@pytest.mark.e2e
def test_navigator_language_matches_Intl_locale(page):
    """navigator.language base must agree with Intl.DateTimeFormat locale."""
    nav = _ev(page, "navigator.language").split("-")[0]
    intl = _ev(page,
        "Intl.DateTimeFormat().resolvedOptions().locale").split("-")[0]
    assert nav == intl, (
        f"navigator.language base={nav!r} vs Intl={intl!r}"
    )


@pytest.mark.e2e
def test_navigator_language_matches_Intl_NumberFormat(page):
    nav = _ev(page, "navigator.language").split("-")[0]
    num = _ev(page,
        "Intl.NumberFormat().resolvedOptions().locale").split("-")[0]
    assert nav == num


@pytest.mark.e2e
def test_navigator_language_matches_Intl_Collator(page):
    nav = _ev(page, "navigator.language").split("-")[0]
    col = _ev(page,
        "(new Intl.Collator()).resolvedOptions().locale").split("-")[0]
    assert nav == col


# ===========================================================================
# 8. Property descriptor shape lies
# Spoofers using Object.defineProperty(navigator, prop, {value: ...})
# leave a 'value' field on the descriptor - real native props use a getter.
# ===========================================================================


_DESCRIPTOR_NATIVE_PROPS = [
    "userAgent", "platform", "hardwareConcurrency", "language", "languages",
    "vendor", "appVersion", "appName", "appCodeName", "doNotTrack",
    "cookieEnabled", "onLine", "product", "productSub", "buildID", "oscpu",
]


@pytest.mark.e2e
@pytest.mark.parametrize("prop", _DESCRIPTOR_NATIVE_PROPS)
def test_navigator_property_descriptor_is_getter_not_value(page, prop):
    """Each spoofable navigator.* property must be defined via a native
    getter - NOT Object.defineProperty(..., {value: x}). The value-field
    descriptor is the lazy spoof leak CreepJS catches."""
    has_lie = _ev(page, f"""() => {{
        let proto = navigator;
        let descriptor = null;
        while (proto && !descriptor) {{
            descriptor = Object.getOwnPropertyDescriptor(proto, {prop!r});
            proto = Object.getPrototypeOf(proto);
        }}
        if (!descriptor) return null;
        return 'value' in descriptor;
    }}""")
    if has_lie is None:
        pytest.skip(f"navigator.{prop} not exposed")
    assert has_lie is False, (
        f"navigator.{prop} descriptor exposes 'value' field - lazy spoof"
    )


# ===========================================================================
# 9. performance.timeOrigin + monotonic
# ===========================================================================


@pytest.mark.e2e
def test_performance_timeOrigin_stable(page):
    assert _ev(page,
        "performance.timeOrigin === performance.timeOrigin")


@pytest.mark.e2e
def test_performance_now_monotonic(page):
    ok = _ev(page, """() => {
        let prev = performance.now();
        for (let i = 0; i < 100; i++) {
            const cur = performance.now();
            if (cur < prev) return false;
            prev = cur;
        }
        return true;
    }""")
    assert ok


# ===========================================================================
# 10. Window dimension invariants
# ===========================================================================


@pytest.mark.e2e
def test_window_inner_not_larger_than_outer(page):
    inner, outer = _ev(page, "[window.innerWidth, window.outerWidth]")
    assert inner <= outer


@pytest.mark.e2e
def test_screen_avail_not_larger_than_screen(page):
    aw, w = _ev(page, "[screen.availWidth, screen.width]")
    ah, h = _ev(page, "[screen.availHeight, screen.height]")
    assert aw <= w and ah <= h


# ===========================================================================
# 11. Firefox UA invariants
# ===========================================================================


@pytest.mark.e2e
def test_firefox_UA_implies_empty_vendor(page):
    """Firefox: navigator.vendor === ''"""
    if "Firefox" not in _ev(page, "navigator.userAgent"):
        pytest.skip("Firefox-only invariant")
    if "Chrome" in _ev(page, "navigator.userAgent"):
        pytest.skip("Chrome+Firefox UA - likely synthetic")
    assert _ev(page, "navigator.vendor") == ""


@pytest.mark.e2e
def test_firefox_appVersion_short_form(page):
    """Real Firefox's appVersion is '5.0 (Windows)' form, not the full UA."""
    if "Firefox" not in _ev(page, "navigator.userAgent"):
        pytest.skip("Firefox-only invariant")
    av = _ev(page, "navigator.appVersion")
    ua = _ev(page, "navigator.userAgent")
    assert av.startswith("5.0 (")
    assert len(av) < len(ua)


@pytest.mark.e2e
def test_firefox_UA_implies_appName_Netscape(page):
    """navigator.appName === 'Netscape' (historical invariant)."""
    if "Firefox" not in _ev(page, "navigator.userAgent"):
        pytest.skip("Firefox-only invariant")
    assert _ev(page, "navigator.appName") == "Netscape"
