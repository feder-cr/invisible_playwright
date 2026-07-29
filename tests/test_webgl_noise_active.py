"""Regression guard: render-noise stays ACTIVE on high-entropy WebGL readbacks.

The near-uniform skip added 2026-06-18 (so fixed-hash reference checks on a solid
WebGL readback pass) must NOT disable noise on real fingerprint renders. A
high-entropy WebGL readback (>16 distinct colours) must still differ between two
seeds - i.e. the per-seed gamma noise is applied. Pairs with
test_canvas_render_stealth.py (solid readback stays pure).

Kept in its own file: it launches its own short-lived browsers, so it must not run
alongside another module's shared browser (the Playwright sync API cannot nest).

Run: pytest tests/test_webgl_noise_active.py -m e2e -v
"""
from __future__ import annotations

import hashlib

import pytest

from invisible_playwright import InvisiblePlaywright

# A high-entropy render: 64 columns, each a distinct colour (>16 distinct → the
# near-uniform skip does NOT apply → noise must run).
_HIGH_ENTROPY_JS = """() => {
    const c = document.createElement('canvas'); c.width = 64; c.height = 64;
    const gl = c.getContext('webgl', {preserveDrawingBuffer: true});
    if (!gl) return null;
    gl.enable(gl.SCISSOR_TEST);
    for (let k = 0; k < 64; k++) {
        gl.scissor(k, 0, 1, 64);
        gl.clearColor(k / 64, (63 - k) / 64, (k * 7 % 64) / 64, 1);
        gl.clear(gl.COLOR_BUFFER_BIT);
    }
    const buf = new Uint8Array(64 * 64 * 4);
    gl.finish(); gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    return Array.from(buf);
}"""


def _render_hash(firefox_binary, seed: int):
    with InvisiblePlaywright(
        seed=seed, binary_path=firefox_binary, headless=True,
        extra_prefs={"zoom.stealth.fpp.hw_seed": 1000 + seed},
    ) as b:
        p = b.new_context().new_page()
        p.goto("about:blank", timeout=30_000)
        arr = p.evaluate(_HIGH_ENTROPY_JS)
    if arr is None:
        return None
    return hashlib.sha256(bytes(arr)).hexdigest()


@pytest.mark.e2e
def test_high_entropy_webgl_still_noised_per_seed(firefox_binary):
    """Two different seeds → two different per-seed gamma curves → the high-entropy
    readback hashes must differ. Identical hashes would mean the noise was skipped
    on a real (non-uniform) render - a regression of the uniform-skip scope."""
    h1 = _render_hash(firefox_binary, 1)
    h2 = _render_hash(firefox_binary, 2)
    if h1 is None or h2 is None:
        pytest.skip("webgl unavailable")
    assert h1 != h2, \
        "high-entropy WebGL readback identical across seeds → render-noise not applied"
