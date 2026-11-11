"""Where an element of an out-of-process frame is, in the pixels of the main page
(Fission, `fission.webContentIsolationStrategy` = 1).

`bounding_box()` is read in the coordinates of the top document, the viewport of
the top as origin, scroll applied: what `getBoundingClientRect()` of the top
would say. The engine measures a node in the layout of its own process and the
parent carries the quads across each process boundary through the content box
of the <iframe>, measured in the embedder's process. The expected number here is
computed in the page, from the rectangles each document reports for itself: the
origin and size of every <iframe> on the way, and the element in its own frame.

Needs the patched binary of the Fission build. The arithmetic of the carrying
and the order of the commands are node tests in automation/juggler/tests
(oop_frame_geometry, content_mux_frame_routes).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

STRATEGY_1 = {"fission.webContentIsolationStrategy": 1}
TOLERANCE = 0.5

_LEVEL = """sel => {
  const e = document.querySelector(sel), r = e.getBoundingClientRect();
  const sx = e.offsetWidth ? r.width / e.offsetWidth : 1, sy = e.offsetHeight ? r.height / e.offsetHeight : 1;
  return {x: r.x + e.clientLeft * sx, y: r.y + e.clientTop * sy, sx, sy};
}"""
_RECT = """sel => { const r = document.querySelector(sel).getBoundingClientRect();
                 return {x: r.x, y: r.y, width: r.width, height: r.height}; }"""


@pytest.fixture
def geometry_origins():
    servers, routes = [], {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = routes.get(self.path.split("?")[0], "").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        # a (127.0.0.1), a2 (the same site as a, another port), b (localhost), c (127.0.0.2)
        for address in ("127.0.0.1", "127.0.0.1", "127.0.0.1", "127.0.0.2"):
            server = ThreadingHTTPServer((address, 0), Handler)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        a, a2, b, c = [
            f"http://{host}:{server.server_port}"
            for host, server in zip(("127.0.0.1", "127.0.0.1", "localhost", "127.0.0.2"), servers)
        ]
        spacer = '<div style="height:1500px"></div>'
        frame = ('<iframe id="payment" src="{src}" style="position:absolute;left:220px;top:150px;'
                 'border:5px solid;width:600px;height:500px"></iframe>')
        routes["/top"] = "<!doctype html><body style='margin:0'>" + spacer + frame.format(src=b + "/child") + "</body>"
        routes["/scaled"] = ("<!doctype html><body style='margin:0'>" + spacer
                             + "<div style='transform:scale(0.5);transform-origin:0 0'>"
                             + frame.format(src=b + "/child") + "</div></body>")
        routes["/hidden"] = ("<!doctype html><body style='margin:0'>"
                             + frame.format(src=b + "/child").replace("position:absolute", "display:none;position:absolute")
                             + "</body>")
        # a -> a2 (same process as a) -> b (another process): the local stretch is one layout call
        routes["/local-top"] = "<!doctype html><body style='margin:0'>" + frame.format(src=a2 + "/local-middle") + "</body>"
        routes["/local-middle"] = ("<!doctype html><body style='margin:0'>"
                                   + '<iframe id="inner" src="' + b + '/child" style="position:absolute;left:30px;top:40px;'
                                   'border:2px solid;width:500px;height:400px"></iframe></body>')
        # a -> b -> a (the third level is the site of the top again)
        routes["/aba-top"] = "<!doctype html><body style='margin:0'>" + frame.format(src=b + "/aba-middle") + "</body>"
        routes["/aba-middle"] = ("<!doctype html><body style='margin:0'>"
                                 + '<iframe id="inner" src="' + a + '/child" style="position:absolute;left:30px;top:40px;'
                                 'border:2px solid;width:500px;height:400px"></iframe></body>')
        routes["/child"] = ("<!doctype html><body style='margin:0'>"
                            "<div id='checkbox' style='position:absolute;left:40px;top:60px;width:120px;height:40px'>Verify</div>"
                            "<div id='below' style='position:absolute;left:40px;top:1200px;width:120px;height:40px'>Below</div>"
                            "<div style='height:2000px'></div></body>")
        yield {name: base + "/" + name for name, base in (
            ("top", a), ("scaled", a), ("hidden", a), ("local-top", a), ("aba-top", a))}
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def open_page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    opened = []

    def open_(url):
        browser = InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False,
                                      headless=True, extra_prefs=STRATEGY_1)
        opened.append(browser)
        page = browser.__enter__().new_context().new_page()
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(20000)
        page.goto(url)
        return page

    try:
        yield open_
    finally:
        for browser in opened:
            browser.__exit__(None, None, None)


def _frame_in(frame, selector):
    owner = frame.query_selector(selector)
    try:
        return owner.content_frame()
    finally:
        owner.dispose()


def expected_box(chain, selector):
    """The box of `selector` in the last frame of `chain`, in the pixels of the first.

    `chain`: [(frame, selector of the <iframe> in it), ...] from the outermost,
    then the frame that holds the element. Each level adds the content origin of
    its <iframe>, as that document reports it, scaled by what is outside it."""
    ox = oy = 0.0
    sx = sy = 1.0
    for frame, iframe_selector in chain[:-1]:
        level = frame.evaluate(_LEVEL, iframe_selector)
        ox += sx * level["x"]
        oy += sy * level["y"]
        sx *= level["sx"]
        sy *= level["sy"]
    rect = chain[-1][0].evaluate(_RECT, selector)
    return {"x": ox + sx * rect["x"], "y": oy + sy * rect["y"],
            "width": sx * rect["width"], "height": sy * rect["height"]}


def assert_box(actual, expected):
    assert actual is not None
    for key in ("x", "y", "width", "height"):
        assert abs(actual[key] - expected[key]) <= TOLERANCE, (key, actual, expected)


@pytest.mark.e2e
def test_the_box_of_an_element_in_an_out_of_process_frame(open_page, geometry_origins):
    page = open_page(geometry_origins["top"])
    child = _frame_in(page, "#payment")
    box = child.locator("#checkbox").bounding_box()
    assert_box(box, expected_box([(page.main_frame, "#payment"), (child, None)], "#checkbox"))
    assert abs(box["x"] - (220 + 5 + 40)) <= TOLERANCE and abs(box["width"] - 120) <= TOLERANCE


@pytest.mark.e2e
@pytest.mark.parametrize("scroll_top,scroll_child", [(300, 0), (0, 200), (300, 200)])
def test_the_box_with_the_top_or_the_child_scrolled(open_page, geometry_origins, scroll_top, scroll_child):
    page = open_page(geometry_origins["top"])
    child = _frame_in(page, "#payment")
    page.evaluate(f"window.scrollTo(0, {scroll_top})")
    child.evaluate(f"window.scrollTo(0, {scroll_child})")
    assert_box(child.locator("#checkbox").bounding_box(),
               expected_box([(page.main_frame, "#payment"), (child, None)], "#checkbox"))


@pytest.mark.e2e
def test_the_box_through_a_scaled_wrapper(open_page, geometry_origins):
    page = open_page(geometry_origins["scaled"])
    child = _frame_in(page, "#payment")
    box = child.locator("#checkbox").bounding_box()
    assert_box(box, expected_box([(page.main_frame, "#payment"), (child, None)], "#checkbox"))
    assert abs(box["width"] - 60) <= TOLERANCE, "half of 120: the scale of the wrapper is in the box"


@pytest.mark.e2e
def test_a_hidden_iframe_has_no_box(open_page, geometry_origins):
    page = open_page(geometry_origins["hidden"])
    owner = page.query_selector("#payment")
    try:
        assert owner.bounding_box() is None
    finally:
        owner.dispose()


@pytest.mark.e2e
def test_an_out_of_process_frame_inside_a_local_frame(open_page, geometry_origins):
    page = open_page(geometry_origins["local-top"])
    middle = _frame_in(page, "#payment")
    inner = _frame_in(middle, "#inner")
    assert_box(inner.locator("#checkbox").bounding_box(),
               expected_box([(page.main_frame, "#payment"), (middle, "#inner"), (inner, None)], "#checkbox"))


@pytest.mark.e2e
def test_a_b_a_crosses_two_boundaries(open_page, geometry_origins):
    page = open_page(geometry_origins["aba-top"])
    middle = _frame_in(page, "#payment")
    inner = _frame_in(middle, "#inner")
    assert_box(inner.locator("#checkbox").bounding_box(),
               expected_box([(page.main_frame, "#payment"), (middle, "#inner"), (inner, None)], "#checkbox"))


@pytest.mark.e2e
def test_scrolling_into_view_is_seen_by_the_first_reading(open_page, geometry_origins):
    """The scroll of the child's <iframe> reaches the embedder as a message of its
    own; the answer of the command waits for it, so the box read right after is
    inside the viewport, every time. 50 times, the first reading."""
    page = open_page(geometry_origins["top"])
    child = _frame_in(page, "#payment")
    width, height = page.evaluate("[innerWidth, innerHeight]")
    for _ in range(50):
        page.evaluate("window.scrollTo(0, 0)")
        child.evaluate("window.scrollTo(0, 0)")
        locator = child.locator("#below")
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        assert 0 <= cx <= width and 0 <= cy <= height, (box, width, height)
