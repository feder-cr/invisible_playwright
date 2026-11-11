"""A `mediump` WebGL shader computes at 32 bit, as on Windows.

On Windows the engine draws through ANGLE on Direct3D 11, which has no 16-bit
float for a shader's `mediump` and `lowp`: both run at full 32-bit precision, so
a page that reads a value back from a `mediump` shader gets the float32 it would
get from `highp`. On Linux the engine draws on its own Mesa llvmpipe, which ran
`mediump` and `lowp` as fp16 (an 11-bit significand, range 65504): an 8-bit
texel read through a sampler of default precision came back as texel/255 rounded
to fp16, up to about 0.12 of a step off (visible once the page scales it up),
and plain arithmetic lost everything past 2048. The result is a different
answer on the same page, and one a real Windows Firefox does not give.

The rule, the same on both systems: in a shader with `precision mediump float;`
every value is a float32.

Three shaders, each writing the bits of its floats (`floatBitsToUint`) into an
RGBA32UI target so nothing between the shader and the page can round them:

1. a `sampler2D` of default precision (lowp in GLSL ES 3.00) fetches, with
   `texelFetch`, an RGBA8 texture whose channels each hold all 256 byte values;
   every channel must be the float32 of byte/255 (the conversion is allowed one
   unit in the last place, since a driver may multiply by 1/255; fp16 is
   thousands of units off);
2. the same with `precision mediump sampler2D;` declared;
3. arithmetic whose exact float32 result fp16 cannot hold: 2049.0 + u (u = 1.0,
   from a uniform) is 2050 only past 11 bits, 0.1 * u is float32(0.1), 1.0 + u *
   2^-12 and 32.0 + u * 2^-10 need 13 and 16 bits. Each is one correctly
   rounded operation, so it is exact in float32 and the answer is the same on
   every hardware.
"""
from __future__ import annotations

import struct

import pytest

pytestmark = pytest.mark.e2e

# The conversion of an 8-bit texel to float may be one unit in the last place
# off the float32 nearest byte/255 (a driver multiplies by 1/255); fp16 is
# around 2000 units off for most bytes.
TEXEL_ULPS = 1

CHANNELS = "RGBA"


def _texels() -> list:
    """256 RGBA texels: every channel takes every byte value (37 and 91 are odd)."""
    return [(i, 255 - i, (i * 37) % 256, (i * 91 + 13) % 256) for i in range(256)]


# The arithmetic of the third shader: (channel, GLSL, value, why fp16 cannot hold it).
ARITHMETIC = [
    ("r", "2049.0 + u", 2050.0, "fp16 cannot hold 2049 (steps of 2 above 2048)"),
    ("g", "0.1 * u", None, "fp16 holds 0.1 as 0.0999756"),
    ("b", "1.0 + u * 0.000244140625", 1.0 + 2.0 ** -12, "1 + 2^-12 needs 13 bits"),
    ("a", "32.0 + u * 0.0009765625", 32.0 + 2.0 ** -10, "32 + 2^-10 needs 16 bits"),
]

FS_HEAD = "#version 300 es\nprecision mediump float;\n"
FS_TEXEL = """uniform sampler2D t;
out highp uvec4 o;
void main() {
  vec4 v = texelFetch(t, ivec2(gl_FragCoord.xy), 0);
  o = uvec4(floatBitsToUint(v.r), floatBitsToUint(v.g), floatBitsToUint(v.b), floatBitsToUint(v.a));
}"""
FS_ARITH = """uniform float u;
out highp uvec4 o;
void main() {
  float r = 2049.0 + u;
  float g = 0.1 * u;
  float b = 1.0 + u * 0.000244140625;
  float a = 32.0 + u * 0.0009765625;
  o = uvec4(floatBitsToUint(r), floatBitsToUint(g), floatBitsToUint(b), floatBitsToUint(a));
}"""
SHADERS = {
    "texelFetch, sampler2D of default precision": FS_HEAD + FS_TEXEL,
    "texelFetch, precision mediump sampler2D": FS_HEAD + "precision mediump sampler2D;\n" + FS_TEXEL,
    "arithmetic": FS_HEAD + FS_ARITH,
}

PAGE = r"""(plan) => {
  const gl = document.createElement('canvas').getContext('webgl2');
  if (!gl) return { error: 'no webgl2' };
  const vsSrc = '#version 300 es\nvoid main(){vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));gl_Position=vec4(p*2.-1.,0.,1.);}';
  function compile(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  }
  const rb = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
  gl.renderbufferStorage(gl.RENDERBUFFER, gl.RGBA32UI, 256, 1);
  const fb = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
  gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, rb);
  if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) return { error: 'RGBA32UI target incomplete' };
  gl.viewport(0, 0, 256, 1);

  const tex = gl.createTexture(); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(plan.texels.flat()));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

  const vs = compile(gl.VERTEX_SHADER, vsSrc);
  const out = {};
  for (const [name, src] of Object.entries(plan.shaders)) {
    const fs = compile(gl.FRAGMENT_SHADER, src);
    if (!fs) { out[name] = { error: 'fragment shader does not compile' }; continue; }
    const p = gl.createProgram(); gl.attachShader(p, vs); gl.attachShader(p, fs); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) { out[name] = { error: 'link: ' + gl.getProgramInfoLog(p) }; continue; }
    gl.useProgram(p);
    const t = gl.getUniformLocation(p, 't'); if (t) gl.uniform1i(t, 0);
    const u = gl.getUniformLocation(p, 'u'); if (u) gl.uniform1f(u, 1.0);
    gl.clearBufferuiv(gl.COLOR, 0, new Uint32Array([0xdeadbeef, 0xdeadbeef, 0xdeadbeef, 0xdeadbeef]));
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    const px = new Uint32Array(256 * 4);
    gl.readPixels(0, 0, 256, 1, gl.RGBA_INTEGER, gl.UNSIGNED_INT, px);
    const e = gl.getError();
    out[name] = e ? { error: 'GL error 0x' + e.toString(16) } : { bits: Array.from(px) };
  }
  return out;
}"""


def _bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def texel_problems(texels: list, bits: list) -> list:
    """Every channel of every texel that is not the float32 of byte/255 (to TEXEL_ULPS)."""
    problems = []
    for x, texel in enumerate(texels):
        for ch in range(4):
            byte = texel[ch]
            want = _bits(byte / 255)
            got = bits[x * 4 + ch]
            ulps = abs(got - want)
            if ulps > TEXEL_ULPS:
                problems.append(f"texel {x} {CHANNELS[ch]}: byte {byte} read as {_float(got)!r} "
                                f"(0x{got:08x}), float32(byte/255) is {_float(want)!r} (0x{want:08x}), {ulps} ulps off")
    return problems


def arithmetic_problems(bits: list) -> list:
    problems = []
    for ch, (name, glsl, value, why) in enumerate(ARITHMETIC):
        want = _bits(0.1 if value is None else value)  # packing rounds to the float32
        got_values = {bits[x * 4 + ch] for x in range(256)}
        if got_values != {want}:
            got = sorted(got_values)[0]
            problems.append(f"{glsl} ({name}): got {_float(got)!r} (0x{got:08x}), "
                            f"float32 is {_float(want)!r} (0x{want:08x}); {why}")
    return problems


def test_mediump_computes_at_32_bit(firefox_binary, engine_at_least):
    engine_at_least("firefox-64")
    from invisible_playwright import InvisiblePlaywright

    texels = _texels()
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        got = page.evaluate(PAGE, {"texels": [list(t) for t in texels], "shaders": SHADERS})
    assert "error" not in got, got.get("error")
    failed = {}
    for name in SHADERS:
        res = got[name]
        assert "bits" in res, f"{name}: {res}"
        found = (arithmetic_problems(res["bits"]) if name == "arithmetic"
                 else texel_problems(texels, res["bits"]))
        if found:
            failed[name] = found
    assert not failed, "mediump is not at 32 bit:\n" + "\n".join(
        f"- {name}: {len(found)} wrong, first:\n    " + "\n    ".join(found[:4]) for name, found in failed.items())
