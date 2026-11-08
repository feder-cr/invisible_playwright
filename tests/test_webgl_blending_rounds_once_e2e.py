"""WebGL blending is done on the unquantized shader output and rounded once.

On Windows the engine draws through ANGLE on Direct3D 11: the fragment shader's
float output goes into the blender as it is, the blend is computed at float
precision, and the result is rounded once, to the render target's format. On
Linux the engine draws on its own Mesa llvmpipe, and until that was fixed
llvmpipe first quantized the shader output to the target's 8 bits and then
blended in 8-bit arithmetic, so a page read back values up to one step off
(two for SUBTRACT) across most factor pairs: `blendFunc(SRC_ALPHA,
ONE_MINUS_SRC_ALPHA)` over a destination of 100 with a source of 200 and an
alpha of 1/255 read back 101 where D3D11 reads round(100.392) = 100.

The rule, the same on both systems: result = clamp(blend(src, dst)) computed
exactly from the float32 values the shader outputs and the destination as it is
stored, then rounded half up to the target's step, once.

One page, one WebGL2 context. For every render-target format the context gives
a complete framebuffer (RGBA8, RGB10_A2, RGBA4 as renderbuffers; R16 and RGBA16
as textures when EXT_texture_norm16 is there) the page clears a 256x1 strip to
a destination, then draws a constant colour from a uniform onto one pixel of the
strip per source alpha (the scissor picks the pixel, so every pixel is one
independent 1x1 blend), and reads the strip back. It sweeps the factor pairs
SRC_ALPHA / ONE_MINUS_SRC_ALPHA, ONE / ONE_MINUS_SRC_ALPHA (premultiplied
source), ONE_MINUS_DST_ALPHA / DST_ALPHA, DST_COLOR / ZERO and
CONSTANT_COLOR / ONE_MINUS_CONSTANT_COLOR with FUNC_ADD, FUNC_SUBTRACT and
FUNC_REVERSE_SUBTRACT, and MIN and MAX, over straight, premultiplied and
non-8-bit-exact (0.31...) sources with a different value in each of R, G, B, A.
The page returns the raw values; the reference is computed here in double.
A result that is a hair from a rounding boundary (within 2e-4 of a half step, in
target steps; scaled up for 16 bits, where float32 itself is that coarse) cannot
be told from float noise in the hardware and is skipped, and the test insists
that nearly all cases were compared.
"""
from __future__ import annotations

import math
import struct

import pytest

pytestmark = pytest.mark.e2e

# Render targets: name, steps per channel (R, G, B, A), step between the source
# alphas swept (RGBA8 takes every one), how the page reads it back.
FORMATS = [
    {"name": "RGBA8", "max": (255, 255, 255, 255), "step": 1, "kind": "rbo", "ifmt": "RGBA8", "read": "u8"},
    {"name": "RGB10_A2", "max": (1023, 1023, 1023, 3), "step": 3, "kind": "rbo", "ifmt": "RGB10_A2", "read": "u1010102"},
    {"name": "RGBA4", "max": (15, 15, 15, 15), "step": 3, "kind": "rbo", "ifmt": "RGBA4", "read": "u4"},
    {"name": "RGBA16", "max": (65535, 65535, 65535, 65535), "step": 3, "kind": "norm16", "ifmt": "RGBA16_EXT", "read": "u16"},
    # no alpha in R16: a missing alpha reads as 1 for DST_ALPHA, only R is stored
    {"name": "R16", "max": (65535, 0, 0, 0), "step": 3, "kind": "norm16", "ifmt": "R16_EXT", "read": "u16"},
]

# Blend states: (name, source factor, destination factor, equation). MIN and MAX
# ignore the factors.
STATES = [
    ("SRC_ALPHA/ONE_MINUS_SRC_ALPHA ADD", "SRC_ALPHA", "ONE_MINUS_SRC_ALPHA", "FUNC_ADD"),
    ("ONE/ONE_MINUS_SRC_ALPHA ADD", "ONE", "ONE_MINUS_SRC_ALPHA", "FUNC_ADD"),
    ("ONE_MINUS_DST_ALPHA/DST_ALPHA ADD", "ONE_MINUS_DST_ALPHA", "DST_ALPHA", "FUNC_ADD"),
    ("DST_COLOR/ZERO ADD", "DST_COLOR", "ZERO", "FUNC_ADD"),
    ("CONSTANT_COLOR/ONE_MINUS_CONSTANT_COLOR ADD", "CONSTANT_COLOR", "ONE_MINUS_CONSTANT_COLOR", "FUNC_ADD"),
    ("SRC_ALPHA/ONE_MINUS_SRC_ALPHA SUBTRACT", "SRC_ALPHA", "ONE_MINUS_SRC_ALPHA", "FUNC_SUBTRACT"),
    ("ONE/ONE SUBTRACT", "ONE", "ONE", "FUNC_SUBTRACT"),
    ("SRC_ALPHA/ONE_MINUS_SRC_ALPHA REVERSE_SUBTRACT", "SRC_ALPHA", "ONE_MINUS_SRC_ALPHA", "FUNC_REVERSE_SUBTRACT"),
    ("ONE/ONE REVERSE_SUBTRACT", "ONE", "ONE", "FUNC_REVERSE_SUBTRACT"),
    ("MIN", "ONE", "ONE", "MIN"),
    ("MAX", "ONE", "ONE", "MAX"),
]

# The constant blend colour (float32 values as the GL stores them).
BLEND_COLOR = (0.25, 0.6, 0.9, 0.35)

# (source rgb in bytes, fraction rgb for the non-8-bit-exact source, destination
# rgba in bytes). Different in R, G, B and A, with dst alpha both partial and full.
COLOUR_SETS = [
    ((200, 50, 137), (0.31, 0.71, 0.123), (100, 210, 88, 77)),
    ((50, 255, 0), (0.9, 0.05, 0.456), (210, 0, 255, 255)),
    ((10, 128, 250), (0.77, 0.333, 0.01), (255, 37, 129, 200)),
]
MODES = ("straight", "premul", "frac")

# The least share of the (case, channel) results that may be skipped as
# near-ties before the test would prove nothing.
MAX_SKIPPED = 0.02

PAGE = r"""(plan) => {
  const gl = document.createElement('canvas').getContext('webgl2');
  if (!gl) return { error: 'no webgl2' };
  gl.disable(gl.DITHER);
  const vs = '#version 300 es\nvoid main(){vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));gl_Position=vec4(p*2.-1.,0.,1.);}';
  const fs = '#version 300 es\nprecision highp float;uniform vec4 c;out vec4 o;void main(){o=c;}';
  const prog = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) return { error: 'shader: ' + gl.getShaderInfoLog(s) };
    gl.attachShader(prog, s);
  }
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return { error: 'link: ' + gl.getProgramInfoLog(prog) };
  gl.useProgram(prog);
  const uc = gl.getUniformLocation(prog, 'c');
  gl.viewport(0, 0, 256, 1);
  gl.blendColor(plan.blendColor[0], plan.blendColor[1], plan.blendColor[2], plan.blendColor[3]);
  const norm16 = gl.getExtension('EXT_texture_norm16');

  function target(f) {
    const fb = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    if (f.kind === 'rbo') {
      const rb = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl[f.ifmt], 256, 1);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, rb);
    } else {
      if (!norm16) return 'no EXT_texture_norm16';
      const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texStorage2D(gl.TEXTURE_2D, 1, norm16[f.ifmt], 256, 1);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    }
    if (gl.getError() !== 0) return 'storage refused';
    const st = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    return st === gl.FRAMEBUFFER_COMPLETE ? '' : 'framebuffer status 0x' + st.toString(16);
  }

  // The strip as target steps, 4 numbers per pixel (a channel the target does not store reads 0).
  function read(f, n) {
    const out = new Array(n * 4).fill(0);
    if (f.read === 'u1010102') {
      const b = new Uint32Array(256);
      gl.readPixels(0, 0, 256, 1, gl.RGBA, gl.UNSIGNED_INT_2_10_10_10_REV, b);
      for (let i = 0; i < n; i++) {
        out[i * 4] = b[i] & 1023; out[i * 4 + 1] = (b[i] >>> 10) & 1023;
        out[i * 4 + 2] = (b[i] >>> 20) & 1023; out[i * 4 + 3] = b[i] >>> 30;
      }
    } else if (f.read === 'u16') {
      let formats = [gl.RGBA, gl.RED], done = false;
      for (const fmt of formats) {
        const ch = fmt === gl.RGBA ? 4 : 1;
        const b = new Uint16Array(256 * ch);
        while (gl.getError() !== 0) {}
        gl.readPixels(0, 0, 256, 1, fmt, gl.UNSIGNED_SHORT, b);
        if (gl.getError() !== 0) continue;
        for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) out[i * 4 + c] = b[i * ch + c];
        done = true; break;
      }
      if (!done) return null;
    } else {
      const b = new Uint8Array(256 * 4);
      gl.readPixels(0, 0, 256, 1, gl.RGBA, gl.UNSIGNED_BYTE, b);
      const div = f.read === 'u4' ? 17 : 1;   // 4 bits come back as v * 17
      for (let i = 0; i < n * 4; i++) out[i] = Math.round(b[i] / div);
    }
    return gl.getError() === 0 ? out : null;
  }

  const result = {};
  for (const f of plan.formats) {
    const problem = target(f);
    if (problem) { result[f.name] = { skipped: problem }; continue; }
    const groups = [];
    for (const g of f.groups) {
      gl.disable(gl.BLEND); gl.disable(gl.SCISSOR_TEST);
      gl.clearColor(g.dst[0], g.dst[1], g.dst[2], g.dst[3]);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.SCISSOR_TEST); gl.enable(gl.BLEND);
      gl.blendFunc(gl[g.sf], gl[g.df]); gl.blendEquation(gl[g.eq]);
      for (let x = 0; x < g.src.length; x++) {
        gl.scissor(x, 0, 1, 1);
        gl.uniform4f(uc, g.src[x][0], g.src[x][1], g.src[x][2], g.src[x][3]);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }
      const px = read(f, g.src.length);
      if (!px) { result[f.name] = { skipped: 'read back refused, error 0x' + gl.getError().toString(16) }; break; }
      groups.push(px);
    }
    if (groups.length === f.groups.length) result[f.name] = { groups };
  }
  return result;
}"""


def _f32(x: float) -> float:
    """x as the float32 the shader and the GL hold."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _short(x: float) -> float:
    """The shortest decimal that is still the same float32, so the plan stays small
    on the wire (the page rounds it to float32 again, to the same value)."""
    for digits in range(1, 10):
        y = float(f"{x:.{digits}g}")
        if _f32(y) == x:
            return y
    return x


def _alphas(step: int) -> list:
    return list(range(0, 256, step))


def build_plan() -> dict:
    """What the page draws: per format, per (blend state, colour set, source mode)
    one group, the destination in target steps and one source colour per alpha."""
    formats = []
    for fmt in FORMATS:
        groups = []
        for name, sf, df, eq in STATES:
            for set_i, (rgb, frac, dst_bytes) in enumerate(COLOUR_SETS):
                dst = [round(b / 255 * m) for b, m in zip(dst_bytes, fmt["max"])]
                for mode in MODES:
                    src = []
                    for a in _alphas(fmt["step"]):
                        alpha = a / 255
                        if mode == "frac":
                            alpha = min(1.0, (a * 0.9961 + 0.37) / 255)
                        alpha = _f32(alpha)
                        if mode == "frac":
                            col = [_f32(v) for v in frac]
                        elif mode == "premul":
                            col = [_f32(_f32(b / 255) * alpha) for b in rgb]
                        else:
                            col = [_f32(b / 255) for b in rgb]
                        src.append(col + [alpha])
                    groups.append({"state": name, "sf": sf, "df": df, "eq": eq, "set": set_i, "mode": mode,
                                   "dst": [_short(_f32(d / m)) if m else 0.0 for d, m in zip(dst, fmt["max"])],
                                   "dst_steps": dst, "src": src})
        formats.append({"name": fmt["name"], "kind": fmt["kind"], "ifmt": fmt["ifmt"], "read": fmt["read"],
                        "groups": groups})
    return {"blendColor": [_f32(v) for v in BLEND_COLOR], "formats": formats}


def wire(plan: dict) -> dict:
    """The plan as the page gets it: the sources as short decimals."""
    return {**plan, "formats": [{**f, "groups": [{**g, "src": [[_short(v) for v in px] for px in g["src"]]}
                                                  for g in f["groups"]]} for f in plan["formats"]]}


def _factor(name: str, s, d, k, ch: int) -> float:
    return {
        "ZERO": lambda: 0.0,
        "ONE": lambda: 1.0,
        "SRC_COLOR": lambda: s[ch],
        "ONE_MINUS_SRC_COLOR": lambda: 1 - s[ch],
        "DST_COLOR": lambda: d[ch],
        "ONE_MINUS_DST_COLOR": lambda: 1 - d[ch],
        "SRC_ALPHA": lambda: s[3],
        "ONE_MINUS_SRC_ALPHA": lambda: 1 - s[3],
        "DST_ALPHA": lambda: d[3],
        "ONE_MINUS_DST_ALPHA": lambda: 1 - d[3],
        "CONSTANT_COLOR": lambda: k[ch],
        "ONE_MINUS_CONSTANT_COLOR": lambda: 1 - k[ch],
    }[name]()


def exact_steps(group: dict, src, k, fmt: dict, ch: int) -> float:
    """The blend of one channel in double, from the float32 source and the
    destination as stored, in target steps, clamped to the target's range."""
    mx = fmt["max"]
    d = [dst / m if m else 0.0 for dst, m in zip(group["dst_steps"], mx)]
    if mx[3] == 0:
        d[3] = 1.0  # a target with no alpha reads as 1
    s = src
    if group["eq"] == "MIN":
        r = min(s[ch], d[ch])
    elif group["eq"] == "MAX":
        r = max(s[ch], d[ch])
    else:
        a = s[ch] * _factor(group["sf"], s, d, k, ch)
        b = d[ch] * _factor(group["df"], s, d, k, ch)
        r = {"FUNC_ADD": a + b, "FUNC_SUBTRACT": a - b, "FUNC_REVERSE_SUBTRACT": b - a}[group["eq"]]
    return min(1.0, max(0.0, r)) * mx[ch]


def check(plan: dict, got: dict):
    """-> (failures, compared, skipped_as_near_ties, formats_checked, formats_skipped)."""
    k = plan["blendColor"]
    failures, compared, near_ties = [], 0, 0
    checked, skipped = [], {}
    for pf in plan["formats"]:
        fmt = next(f for f in FORMATS if f["name"] == pf["name"])
        res = got[pf["name"]]
        if "skipped" in res:
            skipped[pf["name"]] = res["skipped"]
            continue
        checked.append(pf["name"])
        # float32 itself is this coarse at the target's scale; 2e-4 at 8 bits and below
        tol = max(2e-4, 7.8e-7 * max(fmt["max"]))
        for g, px in zip(pf["groups"], res["groups"]):
            for x, src in enumerate(g["src"]):
                for ch in range(4):
                    if fmt["max"][ch] == 0:
                        continue
                    exact = exact_steps(g, src, k, fmt, ch)
                    if abs((exact - math.floor(exact)) - 0.5) < tol:
                        near_ties += 1
                        continue
                    want = math.floor(exact + 0.5)
                    have = px[x * 4 + ch]
                    compared += 1
                    if have != want:
                        failures.append({"format": pf["name"], "state": g["state"], "set": g["set"], "mode": g["mode"],
                                         "channel": "RGBA"[ch], "alpha": round(src[3] * 255, 3),
                                         "dst": g["dst_steps"][ch], "src": src[ch],
                                         "got": have, "exact": round(exact, 4)})
    return failures, compared, near_ties, checked, skipped


def _describe(failures: list, worst: int = 8) -> str:
    lines = [f"  {f['format']} {f['state']} set {f['set']} {f['mode']} channel {f['channel']} "
             f"alpha {f['alpha']} src {f['src']:.6f} dst {f['dst']}: got {f['got']}, exact {f['exact']}"
             for f in failures[:worst]]
    return "\n".join(lines)


def test_blending_is_done_unquantized_and_rounded_once(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    plan = build_plan()
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        got = page.evaluate(PAGE, wire(plan))
    assert "error" not in got, got.get("error")
    assert "groups" in got["RGBA8"], f"RGBA8 target unusable: {got['RGBA8']}"

    failures, compared, near_ties, checked, skipped = check(plan, got)
    total = compared + near_ties
    assert compared > 50_000, (compared, checked, skipped)
    assert near_ties / total < MAX_SKIPPED, (near_ties, total)
    max_err = max((abs(f["got"] - math.floor(f["exact"] + 0.5)) for f in failures), default=0)
    assert not failures, (
        f"{len(failures)} of {compared} results differ from the blend computed unquantized and rounded once "
        f"(max error {max_err}; formats checked {checked}, not available {skipped}); first:\n"
        + _describe(failures))
