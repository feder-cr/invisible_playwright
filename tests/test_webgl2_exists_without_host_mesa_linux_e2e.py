"""WebGL2 exists on a Linux host that has no Mesa.

A page tells a host with no GL from one with a Mesa by `getContext("webgl2")`
returning null. The engine's WebGL draws on the GL it ships (llvmpipe behind
EGL), so what the host has or lacks must not decide whether WebGL exists; on
firefox-59 it did (the host's glxtest blocked WEBGL_OPENGL, and the context
failed with FEATURE_FAILURE_WEBGL_EXHAUSTED_DRIVERS). With a host Mesa present,
WebGL2 exists on every engine, so this test only means something where there is
none: the gate removes every Mesa library from its Linux legs, and a machine that
has one skips it.
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

pytestmark = pytest.mark.e2e

LIBDIR = "/usr/lib/x86_64-linux-gnu"
HOST_MESA = (
    "libgallium-*.so",
    "dri/*_dri.so",
    "libGLX_mesa.so*",
    "libEGL_mesa.so*",
)

PAGE = """
() => {
  const errors = [];
  const make = (kind) => {
    const c = document.createElement('canvas');
    c.width = c.height = 4;
    c.addEventListener('webglcontextcreationerror', e => errors.push(kind + ': ' + e.statusMessage));
    return [c, c.getContext(kind)];
  };
  const [c2, gl2] = make('webgl2');
  const [c1, gl1] = make('webgl');
  let px = null;
  if (gl2) {
    gl2.clearColor(1, 0, 0, 1);
    gl2.clear(gl2.COLOR_BUFFER_BIT);
    const buf = new Uint8Array(4);
    gl2.readPixels(0, 0, 1, 1, gl2.RGBA, gl2.UNSIGNED_BYTE, buf);
    px = Array.from(buf);
  }
  return {webgl2: !!gl2, webgl: !!gl1, pixel: px, errors};
}
"""


def _host_mesa_files() -> list:
    out = []
    for pattern in HOST_MESA:
        out += glob.glob(os.path.join(LIBDIR, pattern))
    return sorted(out)


def test_webgl2_exists_on_a_host_without_mesa(firefox_binary, engine_at_least):
    engine_at_least("firefox-64")
    if not sys.platform.startswith("linux"):
        pytest.skip("the host's GL probe that blocked WebGL is a Linux one")
    present = _host_mesa_files()
    if present:
        pytest.skip("this host has a Mesa (%s ...): WebGL2 exists with one on every "
                    "engine, so the test proves nothing here" % present[0])
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        got = page.evaluate(PAGE)
    assert got["webgl2"], "no WebGL2 on a host without Mesa: %s" % got["errors"]
    assert got["webgl"], "no WebGL1 on a host without Mesa: %s" % got["errors"]
    assert got["pixel"] == [255, 0, 0, 255], got
