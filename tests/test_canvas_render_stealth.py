"""Canvas / WebGL render-stealth regression test (binary-level, 2026-06-18).

Guards a patched-binary behaviour that must never regress, needed for the
fingerprint to look like a real Windows browser to FOSS detectors (CreepJS,
FingerprintJS, BrowserLeaks) and fixed-hash reference checks:

  Solid WebGL readback purity under render-noise - a fixed solid-colour WebGL
  readback (which reference checks hash against a universal constant) must stay
  byte-exact even with per-seed render-noise enabled, while high-entropy
  renders stay noised. (C++: render-noise skips near-uniform WebGL readbacks.)

(Per-font canvas distinctness is no longer guarded here: the font-collapse +
per-font draw offset were removed on 2026-06-20 in favour of real bundled
Windows fonts, which rasterise to distinct images by nature.)

Runs against about:blank, no network/proxy. Part of the e2e release gate.
Run: pytest tests/test_canvas_render_stealth.py -m e2e -v
"""
from __future__ import annotations

import pytest

from invisible_playwright import InvisiblePlaywright


@pytest.fixture(scope="module")
def noised_page(firefox_binary):
    """Headless session with render-noise explicitly ON (positive hw_seed) so the
    purity / distinctness guards actually exercise the noise path."""
    with InvisiblePlaywright(
        seed=42,
        binary_path=firefox_binary,
        headless=True,
        extra_prefs={"zoom.stealth.fpp.hw_seed": 24680},
    ) as browser:
        p = browser.new_context().new_page()
        p.goto("about:blank", timeout=30_000)
        yield p


@pytest.mark.e2e
def test_solid_webgl_readback_stays_pure_under_noise(noised_page):
    """A solid-colour WebGL readback must remain byte-exact (only {0,255}) with
    render-noise on. Regression: the noise drifted edge pixels 255->254 on some GL
    backends (Linux ANGLE-over-GL), breaking fixed-hash reference checks ('oe')."""
    res = noised_page.evaluate(
        """() => {
            const c = document.createElement('canvas'); c.width = 256; c.height = 24;
            const gl = c.getContext('webgl', {preserveDrawingBuffer: true});
            if (!gl) return {ok: false, reason: 'no-webgl'};
            gl.clearColor(1, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
            const buf = new Uint8Array(256 * 24 * 4);
            gl.finish(); gl.readPixels(0, 0, 256, 24, gl.RGBA, gl.UNSIGNED_BYTE, buf);
            const vals = new Set();
            for (let i = 0; i < buf.length; i++) vals.add(buf[i]);
            return {ok: true, vals: Array.from(vals).sort((a, b) => a - b)};
        }"""
    )
    if not res["ok"]:
        pytest.skip(res.get("reason", "webgl unavailable"))
    assert res["vals"] == [0, 255], \
        f"solid WebGL readback not pure under noise: values {res['vals']} (uniform-skip regressed?)"


# NOTE: "high-entropy WebGL still noised" is covered by test_webgl_noise_active.py
# (kept separate: it launches its own browsers, so it must not run while this
# module's shared `noised_page` browser is open - the sync API cannot nest).
