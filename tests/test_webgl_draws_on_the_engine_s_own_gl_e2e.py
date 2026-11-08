"""On Linux WebGL draws on the engine's own GL, never the host's.

Until firefox-59 the engine drew WebGL on Linux with whatever GL the host had,
GLX or EGL from the system's libraries: the host's Mesa version, or its GPU
driver, decided which declared extensions could be served and every pixel a
page read back. Measured on 2026-11-08 with firefox-57 under Xvfb: the process
that draws WebGL mapped the host's libEGL, libGLX and libgallium 26.0.8 from
/usr/lib. From firefox-59 the GL ships with the engine in `llvmpipe/` next to
the executable (Mesa's llvmpipe behind EGL, surfaceless, built from the same
pinned tree and patches as WebGPU's lavapipe), and with the engine on Firefox
loads it by path and never falls back to the host's.

The first test also stands for the X11 case: under Xvfb, as every session on
Linux runs, candidate 42484125583 had no WebGL at all, because the surfaceless
display was asked for with the X11 display, which Mesa refuses.

The first test reads the maps of the process that draws: the engine's libEGL
and libgallium are there, and no libEGL of the host's. (GTK still opens the
host's GLX at startup to choose its X11 visuals; nothing of it reaches WebGL
or a page, and it is not what this test is about.) The second uses every
extension the persona declares, for real, and compares each answer with the
one firefox-57 gave on Windows (ANGLE on D3D11), measured the same day.
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the engine's own GL is Linux's")]

DRAW = """() => {
  const gl = document.createElement('canvas').getContext('webgl2');
  gl.clearColor(1, 0, 0, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  const px = new Uint8Array(4);
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return Array.from(px);
}"""

# Every declared extension used for real: a call, an enum, a shader, a
# framebuffer, and the error or state it leaves.
USE = r"""() => {
  const R = {};
  function ctx(kind) { return document.createElement('canvas').getContext(kind); }
  function err(gl) { const e = gl.getError(); return e === 0 ? 'ok' : '0x' + e.toString(16); }
  function clean(gl) { while (gl.getError()) {} }
  function fbo(gl, ifmt, fmt, type) {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, 4, 4, 0, fmt, type, null);
    const e1 = gl.getError();
    const f = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    const s = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    return (e1 ? 'tex 0x' + e1.toString(16) + ' ' : '') + (s === gl.FRAMEBUFFER_COMPLETE ? 'complete' : 'fb 0x' + s.toString(16));
  }
  function shader(gl, src) {
    const s = gl.createShader(gl.FRAGMENT_SHADER); gl.shaderSource(s, src); gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? 'ok' : 'fail: ' + gl.getShaderInfoLog(s).slice(0, 80);
  }
  function comp(gl, ext, fmtName, bytes) {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.compressedTexImage2D(gl.TEXTURE_2D, 0, ext[fmtName], 4, 4, 0, new Uint8Array(bytes));
    return err(gl);
  }
  function program(gl) {
    const p = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, 'void main(){gl_PointSize=1.0;gl_Position=vec4(0.0,0.0,0.0,1.0);}'],
                               [gl.FRAGMENT_SHADER, 'void main(){gl_FragColor=vec4(1.0);}']]) {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); gl.attachShader(p, s);
    }
    gl.linkProgram(p); gl.useProgram(p); return p;
  }
  function tex(gl, ifmt, fmt, type) {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, 2, 1, 0, fmt, type, null);
    return t;
  }
  for (const kind of ['webgl', 'webgl2']) {
    const gl = ctx(kind); const out = {}; R[kind] = out;
    const two = kind === 'webgl2';
    for (const name of gl.getSupportedExtensions()) {
      clean(gl);
      const x = gl.getExtension(name);
      let r;
      try {
        switch (name) {
        case 'ANGLE_instanced_arrays': program(gl); x.vertexAttribDivisorANGLE(0, 1); x.drawArraysInstancedANGLE(gl.POINTS, 0, 1, 1); r = err(gl) + ' divisor=' + gl.getVertexAttrib(0, x.VERTEX_ATTRIB_ARRAY_DIVISOR_ANGLE); x.vertexAttribDivisorANGLE(0, 0); break;
        case 'EXT_blend_minmax': gl.blendEquation(x.MIN_EXT); r = err(gl); gl.blendEquation(gl.FUNC_ADD); break;
        case 'EXT_color_buffer_half_float':
          if (two) r = fbo(gl, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT);
          else { const h = gl.getExtension('OES_texture_half_float'); r = fbo(gl, gl.RGBA, gl.RGBA, h.HALF_FLOAT_OES); }
          break;
        case 'EXT_color_buffer_float': r = fbo(gl, gl.RGBA32F, gl.RGBA, gl.FLOAT); break;
        case 'WEBGL_color_buffer_float': gl.getExtension('OES_texture_float'); r = fbo(gl, gl.RGBA, gl.RGBA, gl.FLOAT); break;
        case 'EXT_float_blend':
          if (two) { gl.getExtension('EXT_color_buffer_float'); r = fbo(gl, gl.RGBA32F, gl.RGBA, gl.FLOAT); }
          else { gl.getExtension('OES_texture_float'); gl.getExtension('WEBGL_color_buffer_float'); r = fbo(gl, gl.RGBA, gl.RGBA, gl.FLOAT); }
          break;
        case 'EXT_frag_depth': r = shader(gl, '#extension GL_EXT_frag_depth : require\nvoid main(){gl_FragDepthEXT=0.5;}'); break;
        case 'EXT_shader_texture_lod': r = shader(gl, '#extension GL_EXT_shader_texture_lod : require\nprecision mediump float;uniform sampler2D s;void main(){gl_FragColor=texture2DLodEXT(s,vec2(0.),0.);}'); break;
        case 'OES_standard_derivatives': r = shader(gl, '#extension GL_OES_standard_derivatives : require\nprecision mediump float;void main(){gl_FragColor=vec4(dFdx(1.));}'); break;
        case 'EXT_sRGB': tex(gl, x.SRGB_ALPHA_EXT, x.SRGB_ALPHA_EXT, gl.UNSIGNED_BYTE); r = err(gl); break;
        case 'EXT_texture_compression_bptc': r = comp(gl, x, 'COMPRESSED_RGBA_BPTC_UNORM_EXT', 16); break;
        case 'EXT_texture_compression_rgtc': r = comp(gl, x, 'COMPRESSED_RED_RGTC1_EXT', 8); break;
        case 'WEBGL_compressed_texture_s3tc': r = comp(gl, x, 'COMPRESSED_RGBA_S3TC_DXT5_EXT', 16); break;
        case 'WEBGL_compressed_texture_s3tc_srgb': r = comp(gl, x, 'COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT', 16); break;
        case 'EXT_texture_filter_anisotropic': {
          tex(gl, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE);
          gl.texParameterf(gl.TEXTURE_2D, x.TEXTURE_MAX_ANISOTROPY_EXT, 2);
          r = err(gl) + ' max=' + gl.getParameter(x.MAX_TEXTURE_MAX_ANISOTROPY_EXT) + ' read=' + gl.getTexParameter(gl.TEXTURE_2D, x.TEXTURE_MAX_ANISOTROPY_EXT);
          break; }
        case 'OES_element_index_uint': {
          const b = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, b);
          gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array([0]), gl.STATIC_DRAW);
          program(gl); gl.drawElements(gl.POINTS, 1, gl.UNSIGNED_INT, 0); r = err(gl); break; }
        case 'OES_fbo_render_mipmap': {
          const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 4, 4, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
          gl.texImage2D(gl.TEXTURE_2D, 1, gl.RGBA, 2, 2, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
          const f = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, f);
          gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 1);
          r = err(gl); gl.bindFramebuffer(gl.FRAMEBUFFER, null); break; }
        case 'OES_texture_float': tex(gl, gl.RGBA, gl.RGBA, gl.FLOAT); r = err(gl); break;
        case 'OES_texture_half_float': tex(gl, gl.RGBA, gl.RGBA, x.HALF_FLOAT_OES); r = err(gl); break;
        case 'OES_vertex_array_object': {
          const v = x.createVertexArrayOES(); x.bindVertexArrayOES(v);
          r = err(gl) + ' isVA=' + x.isVertexArrayOES(v); x.bindVertexArrayOES(null); break; }
        case 'WEBGL_depth_texture': tex(gl, gl.DEPTH_COMPONENT, gl.DEPTH_COMPONENT, gl.UNSIGNED_SHORT); r = err(gl); break;
        case 'WEBGL_draw_buffers': { const t = tex(gl, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE); const f = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, f); gl.framebufferTexture2D(gl.FRAMEBUFFER, x.COLOR_ATTACHMENT0_WEBGL, gl.TEXTURE_2D, t, 0); x.drawBuffersWEBGL([x.COLOR_ATTACHMENT0_WEBGL]); r = err(gl) + ' max=' + gl.getParameter(x.MAX_DRAW_BUFFERS_WEBGL); gl.bindFramebuffer(gl.FRAMEBUFFER, null); break; }
        case 'WEBGL_provoking_vertex': {
          x.provokingVertexWEBGL(x.FIRST_VERTEX_CONVENTION_WEBGL);
          const v = gl.getParameter(x.PROVOKING_VERTEX_WEBGL);
          r = err(gl) + ' read=' + (v === x.FIRST_VERTEX_CONVENTION_WEBGL ? 'first' : v);
          x.provokingVertexWEBGL(x.LAST_VERTEX_CONVENTION_WEBGL); break; }
        case 'OES_draw_buffers_indexed':
          x.colorMaskiOES(0, false, true, true, true);
          r = err(gl) + ' read=' + JSON.stringify(gl.getIndexedParameter(gl.COLOR_WRITEMASK, 0));
          x.colorMaskiOES(0, true, true, true, true); break;
        case 'OVR_multiview2': {
          const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D_ARRAY, t);
          gl.texStorage3D(gl.TEXTURE_2D_ARRAY, 1, gl.RGBA8, 4, 4, 2);
          const f = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, f);
          x.framebufferTextureMultiviewOVR(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, t, 0, 0, 2);
          r = err(gl) + ' ' + (gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE ? 'complete' : 'incomplete') + ' maxViews=' + gl.getParameter(x.MAX_VIEWS_OVR);
          gl.bindFramebuffer(gl.FRAMEBUFFER, null); break; }
        case 'OES_texture_float_linear': case 'OES_texture_half_float_linear': {
          const half = name.includes('half');
          if (two) tex(gl, half ? gl.RGBA16F : gl.RGBA32F, gl.RGBA, half ? gl.HALF_FLOAT : gl.FLOAT);
          else { const h = gl.getExtension(half ? 'OES_texture_half_float' : 'OES_texture_float');
                 tex(gl, gl.RGBA, gl.RGBA, half ? h.HALF_FLOAT_OES : gl.FLOAT); }
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); r = err(gl); break; }
        case 'WEBGL_debug_shaders': {
          const s = gl.createShader(gl.FRAGMENT_SHADER); gl.shaderSource(s, 'void main(){}'); gl.compileShader(s);
          r = (x.getTranslatedShaderSource(s) || '').slice(0, 40).replace(/\n/g, ' '); break; }
        default: r = x ? 'object' : 'null';
        }
      } catch (e) { r = 'throw ' + e.name + ': ' + e.message.slice(0, 60); }
      out[name] = r;
    }
  }
  return R;
}"""

# firefox-57 on Windows (ANGLE, Direct3D 11), seed 42, 2026-11-08.
ON_WINDOWS = {
    "webgl": {
        'ANGLE_instanced_arrays': 'ok divisor=1',
        'EXT_blend_minmax': 'ok',
        'EXT_clip_control': 'object',
        'EXT_color_buffer_half_float': 'complete',
        'EXT_depth_clamp': 'object',
        'EXT_float_blend': 'complete',
        'EXT_frag_depth': 'ok',
        'EXT_polygon_offset_clamp': 'object',
        'EXT_sRGB': 'ok',
        'EXT_shader_texture_lod': 'ok',
        'EXT_texture_compression_bptc': 'ok',
        'EXT_texture_compression_rgtc': 'ok',
        'EXT_texture_filter_anisotropic': 'ok max=16 read=2',
        'EXT_texture_mirror_clamp_to_edge': 'object',
        'OES_element_index_uint': 'ok',
        'OES_fbo_render_mipmap': 'ok',
        'OES_standard_derivatives': 'ok',
        'OES_texture_float': 'ok',
        'OES_texture_float_linear': 'ok',
        'OES_texture_half_float': 'ok',
        'OES_texture_half_float_linear': 'ok',
        'OES_vertex_array_object': 'ok isVA=true',
        'WEBGL_color_buffer_float': 'complete',
        'WEBGL_compressed_texture_s3tc': 'ok',
        'WEBGL_compressed_texture_s3tc_srgb': 'ok',
        'WEBGL_debug_renderer_info': 'object',
        'WEBGL_debug_shaders': 'void main(){ } ',
        'WEBGL_depth_texture': 'ok',
        'WEBGL_draw_buffers': 'ok max=8',
        'WEBGL_lose_context': 'object',
        'WEBGL_provoking_vertex': 'ok read=first',
    },
    "webgl2": {
        'EXT_clip_control': 'object',
        'EXT_color_buffer_float': 'complete',
        'EXT_depth_clamp': 'object',
        'EXT_float_blend': 'complete',
        'EXT_polygon_offset_clamp': 'object',
        'EXT_texture_compression_bptc': 'ok',
        'EXT_texture_compression_rgtc': 'ok',
        'EXT_texture_filter_anisotropic': 'ok max=16 read=2',
        'EXT_texture_mirror_clamp_to_edge': 'object',
        'EXT_texture_norm16': 'object',
        'OES_draw_buffers_indexed': 'ok read=[false,true,true,true]',
        'OES_texture_float_linear': 'ok',
        'OVR_multiview2': 'ok complete maxViews=4',
        'WEBGL_compressed_texture_s3tc': 'ok',
        'WEBGL_compressed_texture_s3tc_srgb': 'ok',
        'WEBGL_debug_renderer_info': 'object',
        'WEBGL_debug_shaders': 'void main(){ } ',
        'WEBGL_lose_context': 'object',
        'WEBGL_provoking_vertex': 'ok read=first',
    },
}


def _ships_its_own_gl(binary_dir: str) -> bool:
    """The engine's own GL is in the package from firefox-59: the test follows
    the package under test, not the core's version (the gate runs a candidate
    under the published core)."""
    return os.path.isfile(os.path.join(binary_dir, "llvmpipe", "libEGL.so.1"))


def _maps_of_the_drawing_process(binary_dir: str) -> list:
    """The files mapped by the engine process that runs WebGL's host (the one
    with the CanvasRenderer thread)."""
    for d in glob.glob("/proc/[0-9]*"):
        try:
            if not os.readlink(d + "/exe").startswith(binary_dir):
                continue
            threads = {open(t + "/comm").read().strip() for t in glob.glob(d + "/task/*")}
            if "CanvasRenderer" not in threads:
                continue
            return sorted({line.split()[-1] for line in open(d + "/maps") if len(line.split()) > 5})
        except OSError:
            continue
    return []


def test_webgl_draws_on_the_engine_s_own_gl(firefox_binary):
    binary_dir = os.path.dirname(os.path.realpath(firefox_binary))
    if not _ships_its_own_gl(binary_dir):
        pytest.skip("this engine has no GL of its own (it ships from firefox-59)")
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        assert page.evaluate(DRAW) == [255, 0, 0, 255]
        maps = _maps_of_the_drawing_process(binary_dir)
    own = os.path.join(binary_dir, "llvmpipe") + os.sep
    assert maps, "no engine process with a CanvasRenderer thread"
    assert own + "libEGL.so.1" in maps, [m for m in maps if "EGL" in m or "gallium" in m]
    assert any(m.startswith(own + "libgallium-") for m in maps), [m for m in maps if "gallium" in m]
    assert not [m for m in maps if "libEGL" in os.path.basename(m) and not m.startswith(own)], maps


def test_every_declared_extension_answers_as_on_windows(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        got = page.evaluate(USE)
    for kind in ("webgl", "webgl2"):
        differ = {name: (got[kind].get(name), want) for name, want in ON_WINDOWS[kind].items()
                  if got[kind].get(name) != want}
        assert not differ, (kind, differ)
        assert sorted(got[kind]) == sorted(ON_WINDOWS[kind]), kind
