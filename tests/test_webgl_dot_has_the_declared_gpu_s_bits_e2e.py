"""A WebGL dot() gives the bits of the GPU family the persona declares.

Which instructions an adapter runs for dot(a, b) decides the last bit of what a
page reads back: NVIDIA hardware evaluates it as one chain of fused
multiply-adds, left to right, the first product rounded; another adapter, or
the software rasteriser Windows falls back to, may sum the products another
way. With `zoom.stealth.webgl.alu_class` 1 (NVIDIA Maxwell and Pascal) or 2
(NVIDIA Volta and later) ANGLE emits every float dot() of two, three or four
components as IEEE-only `precise` helpers that reproduce that chain, the same
text and the same bits on every adapter, whichever of the two classes (the helpers match
the RTX 5080's own dot() on 65536 random vectors on Intel, the RTX 5080 and WARP).

The page below computes dot(a, b) for fixed vectors in a fragment shader and
reads the float back as four bytes (floatBitsToUint, one byte per channel of an
RGBA8 target, so no float render target is needed). The expected bits come from
the same chain computed here in exact rational arithmetic and rounded once to
float32 at every fma.

The class-1 and class-2 tests pass on every adapter. The class-0 test is the control: it
shows the vectors tell the host's own dot() from the chain, and is skipped on an
adapter whose dot() already is the chain (an NVIDIA GPU), where the difference
is not visible. The two browser tests need ANGLE's D3D translation: Windows only.
"""
from __future__ import annotations

import json
import random
import struct
import sys
from fractions import Fraction

import pytest

N = 4096  # vectors per component count: the canvas is N pixels wide


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _round_f32(fr: Fraction) -> float:
    """The float32 nearest to an exact rational, ties to even (normal range)."""
    if fr == 0:
        return 0.0
    sign = -1 if fr < 0 else 1
    fr = abs(fr)
    e = fr.numerator.bit_length() - fr.denominator.bit_length()
    shift = 23 - e
    while True:
        scaled = fr * Fraction(2) ** shift
        m = scaled.numerator // scaled.denominator
        if m >= 1 << 24:
            shift -= 1
        elif m < 1 << 23:
            shift += 1
        else:
            break
    rem = scaled - m
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and m & 1):
        m += 1
    return sign * float(Fraction(m) / Fraction(2) ** shift)


def _fma(a: float, b: float, c: float) -> float:
    return _round_f32(Fraction(a) * Fraction(b) + Fraction(c))


def nv_dot(a: list[float], b: list[float]) -> float:
    """One fma chain, left to right, the first product rounded."""
    t = _f32(a[0] * b[0])
    for x, y in zip(a[1:], b[1:]):
        t = _fma(x, y, t)
    return t


def _vectors(seed: int) -> list[list[float]]:
    """N rows of 8 float32: a.xyzw then b.xyzw, magnitudes spread over 2^-6..2^6."""
    rng = random.Random(seed)
    rows = []
    for _ in range(N):
        rows.append([_f32(rng.choice((-1.0, 1.0)) * rng.uniform(0.25, 4.0) * 2.0 ** rng.randint(-6, 6))
                     for _ in range(8)])
    return rows


# One program per component count; the pixel i reads its two vectors from a
# 2-row RGBA32F texture with texelFetch and writes the 32 bits of dot() as bytes.
PAGE = r"""(args) => {
  const [rows, K] = args;
  const n = rows.length;
  const canvas = document.createElement('canvas');
  canvas.width = n; canvas.height = 1;
  const gl = canvas.getContext('webgl2', {antialias: false, preserveDrawingBuffer: true});
  if (!gl) return {error: 'no webgl2'};
  const sw = ['x', 'xy', 'xyz', 'xyzw'][K - 1];
  const vs = '#version 300 es\n' +
    'void main(){ vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));' +
    ' gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }';
  const fs = '#version 300 es\nprecision highp float; precision highp int; precision highp sampler2D;\n' +
    'uniform sampler2D T; out vec4 o;\n' +
    'void main(){ int i = int(gl_FragCoord.x);\n' +
    '  vec4 a = texelFetch(T, ivec2(i, 0), 0); vec4 b = texelFetch(T, ivec2(i, 1), 0);\n' +
    '  float d = dot(a.' + sw + ', b.' + sw + ');\n' +
    '  uint u = floatBitsToUint(d);\n' +
    '  o = vec4(float(u & 255u), float((u >> 8) & 255u), float((u >> 16) & 255u), float(u >> 24)) / 255.0; }';
  function sh(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const p = gl.createProgram();
  gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) return {error: gl.getProgramInfoLog(p)};
  gl.useProgram(p);
  const data = new Float32Array(n * 8);
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < 4; c++) { data[i * 4 + c] = rows[i][c]; data[(n + i) * 4 + c] = rows[i][4 + c]; }
  }
  const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, n, 2, 0, gl.RGBA, gl.FLOAT, data);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.uniform1i(gl.getUniformLocation(p, 'T'), 0);
  gl.viewport(0, 0, n, 1);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  const px = new Uint8Array(n * 4);
  gl.readPixels(0, 0, n, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const err = gl.getError();
  if (err) return {error: 'gl error 0x' + err.toString(16)};
  return {bytes: Array.from(px)};
}"""


def _mismatches(firefox_binary, alu_class: int, k: int) -> list[tuple[int, int, int]]:
    """(index, page bits, model bits) of every vector where the page and the chain differ."""
    from invisible_playwright import InvisiblePlaywright

    rows = _vectors(2026 + k)
    prefs = {"zoom.stealth.webgl.alu_class": alu_class,
             # a readback with canvas noise would change low bits of the bytes under test
             "zoom.stealth.canvas.substitute_pixels": False}
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True, extra_prefs=prefs) as browser:
        page = browser.new_context().new_page()
        page.goto("about:blank", timeout=30_000)
        res = page.evaluate(PAGE, [rows, k])
    if "error" in res:
        pytest.skip("WebGL2 not usable here: %s" % res["error"])
    px = res["bytes"]
    bad = []
    for i, row in enumerate(rows):
        got = px[4 * i] | px[4 * i + 1] << 8 | px[4 * i + 2] << 16 | px[4 * i + 3] << 24
        want = _bits(nv_dot(row[:k], row[4:4 + k]))
        if got != want and not (got & 0x7FFFFFFF == 0 and want & 0x7FFFFFFF == 0):
            bad.append((i, got, want))
    return bad


def test_the_model_is_one_fma_chain():
    """The reference itself: exact fma, first product rounded, left to right."""
    # 1 + 2^-24 is a tie that the chain rounds once, to even: 1.
    assert nv_dot([1.0, 1.0], [1.0, 2.0 ** -24]) == 1.0
    assert nv_dot([3.0, 5.0], [7.0, 11.0]) == 76.0
    # The first product is rounded: (1+2^-12)^2 = 1 + 2^-11 + 2^-24 is a tie, rounded to
    # 1 + 2^-11, so the chain leaves 2^-11 after adding -1 (the exact value is 2^-11 + 2^-24).
    x = _f32(1.0 + 2.0 ** -12)
    assert nv_dot([x, 1.0], [x, -1.0]) == 2.0 ** -11


@pytest.mark.e2e
@pytest.mark.timeout(300, method="thread")
@pytest.mark.skipif(sys.platform != "win32", reason="the HLSL is ANGLE's D3D translation, Windows only")
@pytest.mark.parametrize("alu_class", (1, 2))
@pytest.mark.parametrize("k", (2, 3, 4))
def test_dot_is_the_nvidia_fma_chain_when_declared(firefox_binary, engine_at_least, k, alu_class):
    engine_at_least("firefox-60")
    bad = _mismatches(firefox_binary, alu_class, k)
    assert not bad, "dot%d, class %d: %d of %d differ from the chain, first %s" % (
        k, alu_class, len(bad), N, json.dumps(bad[:3]))


@pytest.mark.e2e
@pytest.mark.timeout(300, method="thread")
@pytest.mark.skipif(sys.platform != "win32", reason="the HLSL is ANGLE's D3D translation, Windows only")
@pytest.mark.parametrize("k", (2, 3, 4))
def test_dot_without_the_declaration_is_the_hosts_own(firefox_binary, engine_at_least, k):
    """The control: with class 0 the same vectors tell the host's dot() from the chain."""
    engine_at_least("firefox-60")
    bad = _mismatches(firefox_binary, 0, k)
    if not bad:
        pytest.skip("this adapter's dot%d() already is the NVIDIA chain: the difference is not visible here" % k)
    assert bad
