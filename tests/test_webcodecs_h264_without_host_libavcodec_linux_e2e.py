"""WebCodecs decodes H.264 on a Linux host that has no libavcodec/libavutil.

A page tells a host whose system ffmpeg decodes the stream from one where the
engine's own ffvpx does by whether ``VideoDecoder`` still produces frames once
``libavcodec``/``libavutil`` are gone from the host: the engine ships its own
copy (``media/ffvpx``) and must decode with it, never by falling back to a
system library that may or may not be there. With a host libavcodec present,
decoding succeeds on every engine (the system library would mask a missing
ffvpx), so this test only means something where there is none: the gate
removes libavcodec/libavutil from its Linux legs (same schema as the Mesa
check, see ``test_webgl2_exists_without_host_mesa_linux_e2e.py``), and a
machine that still has one skips it.

The stream is ``webcodecs/h264.annexb`` of web-platform-tests (10 Annex B
frames, 320x240), vendored at ``tests/data/webcodecs_h264.annexb``; the chunk
offsets are those of ``webcodecs/videoDecoder-codec-specific-setup.js``
(``H264_ANNEXB_DATA``), the same ones the manual check in
``media/ffvpx/test/webcodecs_h264/test.html`` uses.
"""
from __future__ import annotations

import glob
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

LIBDIR = "/usr/lib/x86_64-linux-gnu"
HOST_LIBAVCODEC = (
    "libavcodec.so*",
    "libavutil.so*",
)

_DATA_FILE = Path(__file__).parent / "data" / "webcodecs_h264.annexb"

# Offsets of webcodecs/videoDecoder-codec-specific-setup.js (H264_ANNEXB_DATA),
# the same ones media/ffvpx/test/webcodecs_h264/test.html decodes.
_CHUNKS = [
    (0, 4175), (4175, 602), (4777, 473), (5250, 559), (5809, 585),
    (6394, 517), (6911, 530), (7441, 521), (7962, 452), (8414, 526),
]

_PAGE = """
async (chunks) => {
  const result = {frames: 0, sizes: [], errors: []};
  if (typeof VideoDecoder === 'undefined') {
    result.errors.push('VideoDecoder is not defined');
    return result;
  }
  const config = {
    codec: 'avc1.64000b',
    codedWidth: 320,
    codedHeight: 240,
    displayAspectWidth: 320,
    displayAspectHeight: 240,
  };
  const support = await VideoDecoder.isConfigSupported(config);
  result.supported = support.supported;
  if (!support.supported) {
    result.errors.push('isConfigSupported is false');
    return result;
  }
  const buf = new Uint8Array(await (await fetch('/h264.annexb')).arrayBuffer());
  await new Promise((resolve) => {
    const decoder = new VideoDecoder({
      output: (frame) => {
        result.frames++;
        result.sizes.push(frame.codedWidth + 'x' + frame.codedHeight);
        frame.close();
      },
      error: (e) => result.errors.push(String(e)),
    });
    decoder.configure(config);
    chunks.forEach(([offset, size], i) => {
      decoder.decode(new EncodedVideoChunk({
        type: i === 0 ? 'key' : 'delta',
        timestamp: i * 33333,
        data: buf.subarray(offset, offset + size),
      }));
    });
    decoder.flush().then(() => { decoder.close(); resolve(); });
  });
  return result;
}
"""


def _host_libavcodec_files() -> list:
    out = []
    for pattern in HOST_LIBAVCODEC:
        out += glob.glob(os.path.join(LIBDIR, pattern))
    return sorted(out)


class _AnnexbHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/h264.annexb":
            body = _DATA_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"<!doctype html><title>webcodecs-h264</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_webcodecs_h264_decodes_on_a_host_without_libavcodec(firefox_binary, engine_at_least):
    engine_at_least("firefox-62")
    if not sys.platform.startswith("linux"):
        pytest.skip("the host libavcodec/libavutil that could mask a missing ffvpx are Linux libraries")
    present = _host_libavcodec_files()
    if present:
        pytest.skip("this host has a libavcodec (%s ...): decoding succeeds with one on every "
                    "engine, so the test proves nothing here" % present[0])

    from invisible_playwright import InvisiblePlaywright

    with LocalServer(("127.0.0.1", 0), _AnnexbHandler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with InvisiblePlaywright(
                seed=4260, binary_path=firefox_binary, headless=True,
                extra_prefs={
                    "dom.media.webcodecs.enabled": True,
                    "media.ffvpx.enabled": True,
                    "media.ffmpeg.enabled": False,
                },
            ) as browser:
                page = browser.new_page()
                page.goto("http://127.0.0.1:%d/" % port)
                got = page.evaluate(_PAGE, _CHUNKS)
        finally:
            srv.shutdown()

    assert not got["errors"], "WebCodecs errors without a host libavcodec: %s" % got["errors"]
    assert got.get("supported"), "avc1.64000b is not isConfigSupported: %s" % got
    assert got["frames"] == 10, got
    assert got["sizes"] == ["320x240"] * 10, got
