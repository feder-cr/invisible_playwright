"""A WebGL extension the persona declares can be called without killing the browser.

⛔ KNOWN-BAD, read with symbols on 2026-11-07 (Linux, firefox-55): the page got
`WEBGL_provoking_vertex` from `getExtension` - the persona's list (a Windows
one, 31/19 extensions) declares it because ANGLE exposes it - and one call to
`provokingVertexWEBGL()` took the whole browser down. The host side of WebGL
decides on its own in `WebGLContext::IsExtensionSupported`, and for that
extension it refuses on Linux by policy even when the GL has it; the entry point
then asserts the extension is enabled (`MOZ_RELEASE_ASSERT`) and aborts. On
Linux the WebGL host lives in the main process: exit -11 in two seconds, one
page in 5,000. The same trap sat in `colorMaskiOES`, `blendEquationSeparateiOES`,
`blendFuncSeparateiOES` (OES_draw_buffers_indexed), `polygonOffsetClampEXT`
(EXT_polygon_offset_clamp) and `clipControlEXT` (EXT_clip_control).

From firefox-56 the host serves every extension the persona declares wherever
its GL can, and an entry point whose extension is not enabled returns instead of
asserting. The test calls each of the six entry points on both context kinds,
then asks the page a question: a dead browser answers nothing.
"""
from __future__ import annotations

import pytest

CALLS = """
() => {
  const out = {};
  for (const kind of ["webgl", "webgl2"]) {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext(kind);
    if (!gl) { out[kind] = "no context"; continue; }
    const used = [];
    let ext = gl.getExtension("WEBGL_provoking_vertex");
    if (ext) { ext.provokingVertexWEBGL(ext.FIRST_VERTEX_CONVENTION_WEBGL); used.push("provoking_vertex"); }
    ext = gl.getExtension("OES_draw_buffers_indexed");
    if (ext) {
      ext.colorMaskiOES(0, true, true, true, true);
      ext.blendEquationSeparateiOES(0, gl.FUNC_ADD, gl.FUNC_ADD);
      ext.blendFuncSeparateiOES(0, gl.ONE, gl.ZERO, gl.ONE, gl.ZERO);
      used.push("draw_buffers_indexed");
    }
    ext = gl.getExtension("EXT_polygon_offset_clamp");
    if (ext) { ext.polygonOffsetClampEXT(0, 0, 0); used.push("polygon_offset_clamp"); }
    ext = gl.getExtension("EXT_clip_control");
    if (ext) { ext.clipControlEXT(ext.LOWER_LEFT_EXT, ext.NEGATIVE_ONE_TO_ONE_EXT); used.push("clip_control"); }
    out[kind] = {used, error: gl.getError()};
  }
  return out;
}
"""


def _engine_release() -> int:
    import invisible_core
    return int(invisible_core.__version__.split(".")[0])


@pytest.mark.e2e
def test_every_declared_extension_can_be_called_and_the_page_still_answers(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    if _engine_release() < 56:
        # ⛔ On firefox-55 and before this test does not fail, it kills the
        # browser (Linux). It runs from the engine that carries the hooks.
        pytest.skip("the hooks ship in firefox-56; this engine is firefox-%d" % _engine_release())
    with InvisiblePlaywright(seed=4246, binary_path=firefox_binary,
                             headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        result = page.evaluate(CALLS)
        # The browser is alive: it answers a second question after the calls.
        assert page.evaluate("() => 1 + 1") == 2
        for kind, r in result.items():
            assert r != "no context", "%s: no context at all" % kind
            assert r["error"] == 0, "%s: GL error %d after %s" % (kind, r["error"], r["used"])
        # The persona declares at least the one that crashed, so the test
        # exercised the trap and did not pass on an empty list.
        assert "provoking_vertex" in result["webgl2"]["used"], result
