"""A screenshot of a page with text comes back (e2e, Linux).

On the candidate engine of firefox-63 ``page.screenshot()`` of a page with HTML
text never answered: the parent waited for a paint that the text never let
finish, while a page without text, or a canvas, was fine. Every engine before
it draws the same page, so this is not tied to an engine release: it must pass
on all of them, headed and headless.

The page has serif, sans-serif and monospace text. The screenshot must answer
within 15 seconds, and the PNG must hold pixels of more than one colour (text
on a background), not a blank frame.
"""
from __future__ import annotations

import os
import struct
import sys
import time
import zlib

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux text path"),
]

DEADLINE_S = 15

PAGE = ("<!doctype html><meta charset=utf-8><body style='margin:8px;background:#fff;color:#000'>"
        "<h1 style='font-family:serif'>Serif heading</h1>"
        "<p style='font-family:sans-serif;font-size:20px'>The quick brown fox jumps over the lazy dog.</p>"
        "<pre style='font-family:monospace;font-size:16px'>monospace 0123456789</pre></body>")


def _pixel_bytes(png: bytes) -> bytes:
    """The decompressed scanlines of a PNG."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        kind, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    assert width > 0 and height > 0, (width, height)
    return zlib.decompress(idat)


@pytest.mark.wall_clock
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
def test_a_screenshot_of_a_page_with_text_returns(firefox_binary, headless):
    if not headless and not os.environ.get("DISPLAY"):
        pytest.skip("a headed run needs a display (xvfb-run)")
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4246, binary_path=firefox_binary, headless=headless) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        assert page.evaluate("() => document.querySelector('p').getBoundingClientRect().width") > 0
        started = time.monotonic()
        png = page.screenshot(timeout=DEADLINE_S * 1000)
        took = time.monotonic() - started

    assert took < DEADLINE_S, "the screenshot took %.1fs" % took
    assert len(png) > 1000, "the PNG is %d bytes" % len(png)
    pixels = _pixel_bytes(png)
    assert len(set(pixels)) > 4, "the screenshot is a blank frame"
