"""VideoFrame.timestamp on a real H.264 B-frame stream is in retail's own order.

ff_h264_decoder (h264dec.c) does not set FF_CODEC_CAP_SETS_FRAME_PROPS: without
the engine's own reorder buffer (dom/media/platforms/ffmpeg/
FFmpegVideoDecoder.cpp, mPendingFrameTimes), ffmpeg's generic layer stamps
every frame leaving avcodec_receive_frame() with the pts of the most recently
SENT packet, not the one that produced that image, so VideoFrame.timestamp (a
page reading WebCodecs output sees it directly) comes out reordered on a
B-frame stream even though the decoded image content is already correct. REA
on media/ffvpx/libavcodec/decode.c, confirmed with a real build
(MOZ_LOG="FFmpegVideo:5") and confirmed as a Mozilla-wrapper-level gap (the
same stream decoded with Firefox 159.0 retail Linux's own system libavcodec
reproduces the identical broken sequence).

The stream is the same 96-frame 1080p clip measured in invisible_WebCodecs
(captures/h264-1080p-bframes/h264_8bit_1920x1080.h264, has_b_frames 2, sha256
04da8f4dcbb8bc0f72749c0170d0684591aa10b9f8d04486a199dff84e0d51be), vendored
here at tests/data/webcodecs_h264_1080p_bframes.annexb. The access units are
split client-side with the exact same algorithm as
invisible_WebCodecs/scripts/probe.html (splitAccessUnits), which is how a
real page would do it, and each chunk is tagged timestamp = index * 41667 µs
in bitstream order - exactly like the browser-side probe that produced
captures/h264-1080p-bframes/retail-win/result.json. That capture (Firefox
159.0 retail Windows) is the ground truth this test compares against: 96
frames, duration 41667 each, strictly the dense sequence 0, 41667, 83334, ...,
95*41667 in output order.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

_DATA_FILE = Path(__file__).parent / "data" / "webcodecs_h264_1080p_bframes.annexb"
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

  const aus = splitAccessUnits(bytes);
  result.auCount = aus.length;
  const config = {codec: 'avc3.640028', codedWidth: 1920, codedHeight: 1080};
  const support = await VideoDecoder.isConfigSupported(config);
  result.supported = support.supported;
  if (!support.supported) {
    result.errors.push('isConfigSupported is false');
    return result;
  }
  await new Promise((resolve) => {
    const decoder = new VideoDecoder({
      output: (frame) => {
        result.frames.push({timestamp: frame.timestamp, duration: frame.duration});
        frame.close();
      },
      // A decoder error also rejects the flush below with the same
      // exception, so this only needs to record it for the assertions;
      // flush()'s own second .then() handler is what unblocks the await.
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
    decoder.flush().then(
        () => { decoder.close(); resolve(); },
        (e) => { result.errors.push(String(e)); resolve(); });
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
            body = b"<!doctype html><title>webcodecs-h264-bframe-pts</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_webcodecs_h264_bframe_pts_matches_retail_order(firefox_binary, engine_at_least):
    engine_at_least("firefox-60")

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
    for k, frame in enumerate(got["frames"]):
        assert frame["timestamp"] == k * _FRAME_US, (
            "output frame %d: timestamp %r, expected %r (retail's own order)"
            % (k, frame["timestamp"], k * _FRAME_US))
        assert frame["duration"] == _FRAME_US, (k, frame)
