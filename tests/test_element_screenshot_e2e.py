"""`Locator.screenshot()` / `ElementHandle.screenshot()` photograph the element: its size, its pixels, wherever it is.

The wrapper had no `screenshot` on an element handle ("ElementHandle has no method 'screenshot'"). The picture is the page's own
screenshot of the element's box, so the checks are the ones that can go wrong with a box: an element below the fold (it has to be
scrolled in and the document scroll added to a box that is in viewport coordinates), an element inside a frame that the page has
scrolled, an `<iframe>` element itself, and the size of the picture being the size of the box.
"""
from __future__ import annotations

import struct
import zlib

import pytest

pytestmark = pytest.mark.e2e

GREEN, PINK = (0, 204, 68), (255, 0, 170)

PAGE = (
    "<!doctype html><title>shot</title><body style='margin:0;background:#fff'>"
    "<div id='top' style='position:absolute;left:20px;top:10px;width:30px;height:20px;background:#ff00aa'></div>"
    "<div id='below' style='position:absolute;left:40px;top:2500px;width:80px;height:60px;background:#00cc44'></div>"
    "<iframe id='f' style='position:absolute;left:100px;top:900px;width:200px;height:120px;border:0' srcdoc=\""
    "<body style='margin:0;background:#ff00aa'><div style='height:600px'></div>"
    "<div id='inner' style='margin-left:15px;width:50px;height:40px;background:#00cc44'></div>"
    "<div style='height:900px'></div>\"></iframe>"
    "<div style='position:absolute;top:4000px;width:1px;height:1px'></div>"
)


def decode_png(data: bytes):
    """(width, height, pixel(x, y)) of an 8-bit RGB/RGBA PNG without interlacing."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and color in (2, 6) and interlace == 0, (depth, color, interlace)
            step = 3 if color == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw, stride, rows, prev = zlib.decompress(idat), width * step, [], bytearray(width * step)
    for y in range(height):
        ft, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - step] if i >= step else 0
            b = prev[i]
            c = prev[i - step] if i >= step else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return width, height, lambda x, y: tuple(rows[y][x * step:x * step + 3])


def _near(a, b, tol=6):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def _whole(png, color):
    width, height, pixel = decode_png(png)
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1), (width // 2, height // 2)):
        assert _near(pixel(x, y), color), ((x, y), pixel(x, y), color)
    return width, height


def test_an_element_screenshot_is_the_element_wherever_it_is(firefox_binary, tmp_path):
    from invisible_playwright import InvisiblePlaywright

    (tmp_path / "p.html").write_text(PAGE, encoding="utf-8")
    with InvisiblePlaywright(seed=4262, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_context().new_page()
        page.goto((tmp_path / "p.html").as_uri(), wait_until="load")
        # in view
        assert _whole(page.locator("#top").screenshot(scale="css"), PINK) == (30, 20)
        # far below the fold: scrolled in, then the document scroll is added to its viewport box
        assert page.evaluate("window.scrollY") == 0
        assert _whole(page.locator("#below").screenshot(scale="css"), GREEN) == (80, 60)
        # the same element again, now that the page has scrolled to it
        assert _whole(page.locator("#below").screenshot(scale="css"), GREEN) == (80, 60)
        # an element in a frame, below that frame's fold, in a page that is scrolled: size and pixels
        frame = page.frames[1]
        assert _whole(frame.locator("#inner").screenshot(scale="css"), GREEN) == (50, 40)
        # the <iframe> element is the frame: pink around, the green swatch inside it
        width, height, pixel = decode_png(page.locator("#f").screenshot(scale="css"))
        assert (width, height) == (200, 120)
        assert _near(pixel(3, 3), PINK)


def test_an_element_that_is_not_there_has_no_screenshot(firefox_binary, tmp_path):
    from invisible_playwright import InvisiblePlaywright

    (tmp_path / "p.html").write_text("<!doctype html><div id='gone' style='display:none'>x</div>", encoding="utf-8")
    with InvisiblePlaywright(seed=4263, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_context().new_page()
        page.goto((tmp_path / "p.html").as_uri(), wait_until="load")
        with pytest.raises(Exception, match="not visible"):
            page.locator("#gone").screenshot(timeout=2000)
