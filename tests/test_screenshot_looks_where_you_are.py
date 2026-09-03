"""A screenshot photographs where the page IS, and `full_page` means the page.

⛔ NEITHER WAS TRUE UNTIL 2026-09-03, and both failed the same way: silently,
with a valid image of the wrong place.

`op_screenshot` built its clip as `{x: 0, y: 0, width: innerWidth,
height: innerHeight}`. The engine reads a clip in DOCUMENT coordinates, so that
is not the viewport - it is the document's top-left corner, at viewport size.
Two promises broke on that one line:

  * scrolling did nothing. `scrollTo(0, 3000)` then `screenshot()` returned the
    top of the page. Measured on a 4000px document with a red band at y=1000:
    after scrolling to 1000 the image was still all white. For an agent that
    scrolls and looks - which is what browser_take_screenshot is for - every
    look answered the same picture, and nothing in the image says so.
  * `full_page=True` was never read by the driver at all. On the same document
    it returned 800x600 where 800x4000 was asked for.

⛔ AND THE VENDORED UPSTREAM SUITE COULD NOT HAVE CAUGHT IT. It is excluded from
collection by `norecursedirs`, but that is the smaller half: its
`test_screenshot.py` carries three tests - mask, inferring the type from the
path, and the type argument - and neither `full_page` nor scrolling is among
them. A subset of somebody else's suite is not coverage of the part they left
out.

The assertions here read PIXELS, not sizes. A size proves the crop was the right
shape; only a pixel proves it was taken from the right place, and the defect was
a right-shaped crop of the wrong place.
"""
from __future__ import annotations

import http.server
import socket
import struct
import threading
import zlib

import pytest

# White down to 1000, one RED band 1000..1200, black to 4000, and a GREEN
# column at x=1400 so the same trick works sideways. Reading the colour at a
# known place in the image says which part of the document it came from.
ROSSO = (204, 0, 0)
VERDE = (0, 170, 0)
PAGE = b"""<!doctype html><html><body style="margin:0">
<div style="height:1000px;background:#fff"></div>
<div id="band" style="height:200px;background:#c00"></div>
<div style="height:2800px;background:#000"></div>
<div id="colonna" style="position:absolute;top:0;left:1400px;width:200px;height:600px;background:#0a0"></div>
</body></html>"""


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port


# ── reading a PNG without adding a dependency ───────────────────────────────
#
# Pillow would do this in one line and is not in this package's dev extras.
# Adding it so a single test can read one pixel is a heavier change than the
# thirty lines below, which decode exactly as much as the assertions need: the
# header, and the first few rows.

def _size(path) -> tuple:
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG: %r" % head[:8]
    return struct.unpack(">II", head[16:24])


def _first_rows(path, rows: int = 4) -> list:
    """The first `rows` scanlines as lists of (r, g, b).

    Un-filtering needs every row up to the one you want, so this stops as early
    as it can: the assertions only look near the top.
    """
    raw = open(path, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colour = struct.unpack(">IIBB", raw[16:26])
    assert depth == 8, "expected 8 bits per channel, got %d" % depth
    canali = {2: 3, 6: 4, 0: 1}.get(colour)
    assert canali, "unsupported PNG colour type %d" % colour

    dati, i = b"", 8
    while i < len(raw):
        lung, tipo = struct.unpack(">I4s", raw[i:i + 8])
        if tipo == b"IDAT":
            dati += raw[i + 8:i + 8 + lung]
        elif tipo == b"IEND":
            break
        i += 12 + lung
    grezzo = zlib.decompress(dati)

    passo = width * canali
    sopra = bytearray(passo)
    fuori = []
    for r in range(min(rows, height)):
        base = r * (passo + 1)
        filtro = grezzo[base]
        riga = bytearray(grezzo[base + 1:base + 1 + passo])
        for x in range(passo):
            a = riga[x - canali] if x >= canali else 0
            b = sopra[x]
            c = sopra[x - canali] if x >= canali else 0
            if filtro == 1:
                riga[x] = (riga[x] + a) & 0xFF
            elif filtro == 2:
                riga[x] = (riga[x] + b) & 0xFF
            elif filtro == 3:
                riga[x] = (riga[x] + (a + b) // 2) & 0xFF
            elif filtro == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                riga[x] = (riga[x] + pr) & 0xFF
        sopra = riga
        fuori.append([tuple(riga[x:x + 3]) for x in range(0, passo, canali)])
    return fuori


def test_the_png_reader_reads(tmp_path):
    """Everything below is worthless if the decoder is. Known-bad on purpose:
    a PNG this package produced, whose colour at a known place is known."""
    import base64

    # 2x1, left pixel red, right pixel black, written by hand
    dati = zlib.compress(bytes([0, 204, 0, 0, 0, 0, 0]))
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", dati) + _chunk(b"IEND", b""))
    f = tmp_path / "due.png"
    f.write_bytes(png)
    assert _size(f) == (2, 1)
    assert _first_rows(f, 1)[0] == [ROSSO, (0, 0, 0)]


def _chunk(tipo: bytes, corpo: bytes) -> bytes:
    return (struct.pack(">I", len(corpo)) + tipo + corpo
            + struct.pack(">I", zlib.crc32(tipo + corpo) & 0xFFFFFFFF))


@pytest.fixture(scope="module")
def scatti(tmp_path_factory, firefox_binary):
    """One browser, four photographs, so the assertions cost nothing after."""
    from invisible_playwright import InvisiblePlaywright

    out = tmp_path_factory.mktemp("scatti")
    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context(
                viewport={"width": 800, "height": 600}).new_page()
            page.goto(url, wait_until="load", timeout=30_000)
            alto = page.evaluate("() => document.documentElement.scrollHeight")

            page.screenshot(path=str(out / "cima.png"))
            page.screenshot(path=str(out / "intera.png"), full_page=True)
            page.evaluate("() => window.scrollTo(0, 1000)")
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / "scorsa.png"))
            page.screenshot(path=str(out / "ritagliata.png"),
                            clip={"x": 0, "y": 1000, "width": 400, "height": 200})
            # ⛔ scrollTo is CLAMPED to the maximum: asking for 1400 on a
            # 1600px document in an 800px viewport lands on 800. The test
            # reads back where it actually went rather than assuming, which
            # is the same mistake in miniature as the defect it covers.
            page.evaluate("() => window.scrollTo(1400, 0)")
            page.wait_for_timeout(300)
            dove = page.evaluate("() => Math.round(window.scrollX)")
            page.screenshot(path=str(out / "di-lato.png"))
            largo = page.evaluate(
                "() => document.documentElement.scrollWidth")
            yield out, alto, largo, dove
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_the_page_is_the_shape_the_test_believes(scatti):
    """Otherwise every assertion below is about a document of another shape."""
    _, alto, largo, _ = scatti
    assert (largo, alto) == (1600, 4000), (
        "the fixture page is %dx%d, not 1600x4000" % (largo, alto))


@pytest.mark.e2e
def test_an_unscrolled_screenshot_is_the_top_of_the_page(scatti):
    out = scatti[0]
    assert _size(out / "cima.png") == (800, 600)
    assert _first_rows(out / "cima.png", 1)[0][400] == (255, 255, 255)


@pytest.mark.e2e
def test_after_scrolling_it_photographs_where_you_scrolled_to(scatti):
    """The one that was broken, and the only test here that a size cannot pass.

    At scrollY 1000 the viewport starts exactly on the red band, so the top row
    is red. Before the fix this image was white: a correctly sized picture of
    the wrong place.
    """
    out = scatti[0]
    assert _size(out / "scorsa.png") == (800, 600)
    riga = _first_rows(out / "scorsa.png", 1)[0]
    assert riga[400] == ROSSO, (
        "the top of the image is %s, not the red band the viewport is sitting "
        "on - the screenshot ignored the scroll" % (riga[400],))


@pytest.mark.e2e
def test_full_page_means_the_whole_page(scatti):
    """It was never read by the driver, so it returned the viewport."""
    out = scatti[0]
    # 1600 and not 800: the document is wider than the viewport, and taking
    # the viewport width here would be the same defect on the other axis.
    assert _size(out / "intera.png") == (1600, 4000), (
        "full_page returned %s; the document is 1600x4000" % (
            _size(out / "intera.png"),))
    assert _first_rows(out / "intera.png", 1)[0][400] == (255, 255, 255)


@pytest.mark.e2e
def test_a_clip_the_caller_gave_is_left_alone(scatti):
    """Playwright documents `clip` in page coordinates, which is what the engine
    wants, so there is nothing to translate - and the fix must not start
    translating it. Known-bad: adding the scroll offset to a caller's clip,
    which would move this crop 1000px down onto the black."""
    out = scatti[0]
    assert _size(out / "ritagliata.png") == (400, 200)
    assert _first_rows(out / "ritagliata.png", 1)[0][200] == ROSSO


@pytest.mark.e2e
def test_it_follows_a_sideways_scroll_too(scatti):
    """The fix reads `window.scrollX` as well, and nothing was checking it.

    Found by mutation: replacing `x: box["x"]` with `x: 0` left every other
    test in this file green, because none of them scrolled sideways. A green
    suite that cannot see half of a two-line fix is measuring one line.
    """
    out, _, _, dove = scatti
    assert dove > 0, "the page did not scroll sideways at all"
    assert _size(out / "di-lato.png") == (800, 600)
    # The green column lives at document x 1400..1600, so in an image taken
    # at scrollX `dove` it starts at 1400 - dove. Computed, not guessed.
    x = 1400 - dove + 100
    assert 0 <= x < 800, "the column is not in this viewport (scrollX=%d)" % dove
    riga = _first_rows(out / "di-lato.png", 1)[0]
    assert riga[x] == VERDE, (
        "at image x=%d the colour is %s, not the green column that sits there "
        "when the viewport is at scrollX=%d - the screenshot ignored the "
        "horizontal scroll" % (x, riga[x], dove))
