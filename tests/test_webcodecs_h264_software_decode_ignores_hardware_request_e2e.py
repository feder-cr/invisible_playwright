"""With the engine on, WebCodecs decodes in software even when the page asks
for hardware - VideoFrame.copyTo(RGBA) matches the software reference bit for
bit.

REA (C:/ff/invisible_WebCodecs/notes/nv12-rgb-conversion.md) found that
WebCodecs' D3D11/DXVA hardware decode path does its NV12->RGB conversion in
the GPU (``ID3D11VideoContext::VideoProcessorBlt``), which depends on the
driver: a host dependency no two machines are guaranteed to agree on, and
exactly the kind of value the engine's own rule 3 kills at the call that would
read the host. dom/media/webcodecs/DecoderTemplate.cpp's
``ShouldPreferSoftwareDecoder`` makes the decoder ignore the page's
``hardwareAcceleration`` entirely while the engine is on
(``gfx::StealthEngineActive()``): always software (libyuv, fixed Firefox
code), never the driver's own blit.

This is firefox-61 checklist goal 6b: frames a page reads through WebCodecs
must never be converted by the host's GPU VideoProcessor; with the engine on
it stays on the libyuv path, verified by an e2e on the copyTo hash matching
retail's software path.

Same stream as test_webcodecs_h264_bframe_pts_order_e2e.py (the 96-frame 1080p
clip, has_b_frames 2, vendored at
tests/data/webcodecs_h264_1080p_bframes.annexb, sha256
04da8f4dcbb8bc0f72749c0170d0684591aa10b9f8d04486a199dff84e0d51be), decoded the
same way (access units split client-side like
invisible_WebCodecs/scripts/probe.html's splitAccessUnits), but the page's
config explicitly asks for ``hardwareAcceleration: "prefer-hardware"`` - the
opposite of what the engine must actually do. For every output frame the page
computes SHA-256 of ``frame.copyTo(buf, {format: "RGBA"})``, exactly like
probe.html did to produce the reference capture.

The reference is captures/h264-1080p-bframes/retail-win/result.json (Firefox
159.0 retail Windows, headless - which turns DXVA off on retail too, so that
capture is itself the SOFTWARE path, per its own meta.json
``"hardware_decode": false``): 96 RGBA hashes, one per frame index, vendored
here at tests/data/webcodecs_h264_1080p_bframes_rgba_sha256.json so this test
does not depend on the private study repo being checked out beside this one.

On a VM with no GPU this test runs and asserts the same thing it would with a
real GPU present: there is no hardware path to force into software here, so a
pass only says libyuv's output has not drifted from the reference. What it
actually guards against is a regression that gives the engine's WebCodecs a
real hardware path again while leaving the page's request wired straight
through - the day that happens on a machine with a GPU, this hash stops
matching.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

_DATA_FILE = Path(__file__).parent / "data" / "webcodecs_h264_1080p_bframes.annexb"
_REFERENCE_FILE = (
    Path(__file__).parent / "data" / "webcodecs_h264_1080p_bframes_rgba_sha256.json"
)
_FRAME_US = 41667
_EXPECTED_FRAMES = 96

_PAGE = """
async (frameUs) => {
  const result = {frames: [], errors: []};
  if (typeof VideoDecoder === 'undefined') {
    result.errors.push('VideoDecoder is not defined');
    return result;
  }
  const bytes = new Uint8Array(await (await fetch('/h264.annexb')).arrayBuffer());

  // Same access-unit split as invisible_WebCodecs/scripts/probe.html
  // (splitAccessUnits): a new AU starts at an AUD, or at a VCL NAL with
  // first_mb_in_slice == 0 when the current AU already holds a VCL. Non-VCL
  // NALs (SEI, SPS, PPS) right before that VCL go to the new AU.
  function startCodes(b) {
    const pos = [];
    for (let i = 0; i + 3 < b.length; i++) {
      if (b[i] === 0 && b[i + 1] === 0 && b[i + 2] === 1) pos.push(i);
    }
    return pos;
  }
  const nalType = (b) => b & 0x1f;
  const isAud = (t) => t === 9;
  const isVcl = (t) => t >= 1 && t <= 5;
  const isKey = (t) => t === 5;
  const firstSlice = (b, h) => (b[h + 1] & 0x80) !== 0;
  function splitAccessUnits(b) {
    const sc = startCodes(b);
    const starts = [0];
    let curHasVcl = false, prevVcl = false, runStart = null;
    for (const i of sc) {
      const h = i + 3;
      const t = nalType(b[h]);
      if (isAud(t)) {
        if (curHasVcl) starts.push(i);
        curHasVcl = false; prevVcl = false; runStart = null;
      } else if (isVcl(t)) {
        if (firstSlice(b, h) && curHasVcl) {
          starts.push(runStart ?? i);
          curHasVcl = false;
        }
        curHasVcl = true; prevVcl = true; runStart = null;
      } else if (prevVcl && runStart === null) {
        runStart = i;
      }
    }
    const aus = [];
    for (let k = 0; k < starts.length; k++) {
      const s = starts[k];
      let e = k + 1 < starts.length ? starts[k + 1] : b.length;
      while (e > s && b[e - 1] === 0) e--;
      aus.push(b.subarray(s, e));
    }
    return aus;
  }
  function auIsKey(au) {
    for (const i of startCodes(au)) {
      if (i + 3 < au.length && isKey(nalType(au[i + 3]))) return true;
    }
    return false;
  }

  async function sha256Hex(buf) {
    const d = await crypto.subtle.digest('SHA-256', buf);
    return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, '0')).join('');
  }

  const aus = splitAccessUnits(bytes);
  result.auCount = aus.length;
  const config = {
    codec: 'avc3.640028',
    codedWidth: 1920,
    codedHeight: 1080,
    // The opposite of what the engine must actually do: this must make no
    // difference while the engine is on.
    hardwareAcceleration: 'prefer-hardware',
  };
  const support = await VideoDecoder.isConfigSupported(config);
  result.supported = support.supported;
  if (!support.supported) {
    result.errors.push('isConfigSupported is false');
    return result;
  }

  let chain = Promise.resolve();
  async function handleFrame(frame) {
    const row = {timestamp: frame.timestamp, duration: frame.duration, rgbaSha256: null};
    try {
      const size = frame.allocationSize({format: 'RGBA'});
      const buf = new Uint8Array(size);
      await frame.copyTo(buf, {format: 'RGBA'});
      row.rgbaSha256 = await sha256Hex(buf.buffer);
    } catch (e) {
      result.errors.push('copyTo: ' + String(e));
    }
    result.frames.push(row);
    frame.close();
  }

  await new Promise((resolve) => {
    const decoder = new VideoDecoder({
      output: (frame) => { chain = chain.then(() => handleFrame(frame)); },
      error: (e) => result.errors.push(String(e)),
    });
    decoder.configure(config);
    aus.forEach((au, i) => {
      decoder.decode(new EncodedVideoChunk({
        type: auIsKey(au) ? 'key' : 'delta',
        timestamp: i * frameUs,
        duration: frameUs,
        data: au,
      }));
    });
    decoder.flush().then(() => chain).then(() => { decoder.close(); resolve(); });
  });
  return result;
}
"""


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
            body = (
                b"<!doctype html>"
                b"<title>webcodecs-h264-software-decode-ignores-hardware-request</title>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_webcodecs_h264_software_decode_matches_retail_even_with_hardware_request(
    firefox_binary, engine_at_least
):
    engine_at_least("firefox-61")

    from invisible_playwright import InvisiblePlaywright

    reference = json.loads(_REFERENCE_FILE.read_text(encoding="utf-8"))
    expected_hashes = reference["rgbaSha256ByIndex"]
    assert len(expected_hashes) == _EXPECTED_FRAMES, reference

    with LocalServer(("127.0.0.1", 0), _AnnexbHandler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with InvisiblePlaywright(
                seed=4260, binary_path=firefox_binary, headless=True,
                extra_prefs={
                    "dom.media.webcodecs.enabled": True,
                    "media.ffvpx.enabled": True,
                },
            ) as browser:
                page = browser.new_page()
                page.goto("http://127.0.0.1:%d/" % port)
                got = page.evaluate(_PAGE, _FRAME_US)
        finally:
            srv.shutdown()

    assert not got["errors"], "WebCodecs errors decoding the B-frame clip: %s" % got["errors"]
    assert got.get("supported"), "avc3.640028 is not isConfigSupported: %s" % got
    assert got["auCount"] == _EXPECTED_FRAMES, got
    assert len(got["frames"]) == _EXPECTED_FRAMES, got
    for k, (frame, expected) in enumerate(zip(got["frames"], expected_hashes)):
        assert frame["timestamp"] == k * _FRAME_US, (
            "output frame %d: timestamp %r, expected %r"
            % (k, frame["timestamp"], k * _FRAME_US))
        assert frame["rgbaSha256"] == expected, (
            "frame %d: copyTo(RGBA) sha256 %r does not match the software "
            "reference %r - with the engine on this must hold regardless of "
            "hardwareAcceleration: 'prefer-hardware'"
            % (k, frame["rgbaSha256"], expected))
