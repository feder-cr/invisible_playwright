"""`position=` must reach the event, and the argument must reach the handle.

⛔ THESE TWO GUARD ONE DEFECT WITH TWO HALVES, and the halves hid each other.
The humanised cursor deliberately lands OFF-CENTRE and hands the point back to
the action through `position=`. On the Python transport neither half worked:

1. `ElementHandle.evaluateExpression` called `fn(element)` instead of
   `fn(element, arg)`, so the cursor's own hit test - which passes `{x, y}` as
   that argument - threw inside the page, was caught, and answered "no". The
   landing override was therefore never even computed.
2. And once it was computed and sent, the server ignored `position` for every
   pointer action, so the event landed on the geometric centre anyway.

⛔ THE CONSEQUENCE IS A TELL, NOT A MISSING FEATURE. Every element-targeted
action ended on `box.x + width/2, box.y + height/2`: one exact number, identical
in every install, readable from a single event. It was inert on the whole
transport and nothing failed - no exception, no red test, no warning.

Found by diffing the protocol against the Node driver: the driver's `click`
carried a `position` and ours did not.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

from _servers import LocalServer

#: A big target, so a centre click and an offset click are far apart in pixels
#: and the assertion cannot pass by rounding.
PAGE = b"""<!doctype html><html><head><title>position</title></head><body>
<div id="target" style="position:absolute;left:100px;top:100px;
     width:400px;height:300px;background:#eee"></div>
<div id="host" style="position:absolute;left:600px;top:100px;
     width:200px;height:200px;background:#ddd"></div>
<script>
window.__last = null;
document.getElementById('target').addEventListener('click', function (e) {
  window.__last = {x: e.clientX, y: e.clientY};
});
window.__dbl = 0;
document.getElementById('host').addEventListener('dblclick', function () {
  window.__dbl++;
});
</script>
</body></html>"""


@pytest.fixture
def served():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    with LocalServer(("127.0.0.1", 0), H) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d/" % srv.server_address[1]
        finally:
            srv.shutdown()


@pytest.mark.e2e
def test_an_explicit_position_reaches_the_event(firefox_binary, served):
    """The caller pinned a point. The event must land on it, not on the centre.

    ⛔ Asserted from the PAGE's own listener, not from what we sent. Reading
    back our own dispatch would pass on a server that discards the option -
    which is exactly the server this test exists to fail.
    """
    with InvisiblePlaywright(seed=42, headless=True,
                             binary_path=str(firefox_binary)) as browser:
        page = browser.new_page()
        page.goto(served, wait_until="load")
        page.click("#target", position={"x": 10, "y": 12})
        got = page.evaluate("() => window.__last")

    assert got is not None, "the page saw no click at all"
    # The element sits at (100, 100) and is 400x300: its centre is (300, 250).
    assert abs(got["x"] - 110) <= 2 and abs(got["y"] - 112) <= 2, (
        "position={x:10,y:12} on an element at (100,100) must land near "
        "(110,112); the event says %r. Landing on (300,250) means the option "
        "was discarded and every click goes to the geometric centre." % (got,))


@pytest.mark.e2e
def test_the_humanised_cursor_lands_off_centre(firefox_binary, served):
    """With humanize on, the landing must NOT be the exact centre.

    ⛔ The assertion is "not the centre", not "at some specific point": the
    landing is drawn from the session seed on purpose, so pinning the pixel
    would be pinning the generator instead of the property that matters.
    """
    with InvisiblePlaywright(seed=42, headless=True, humanize=True,
                             binary_path=str(firefox_binary)) as browser:
        page = browser.new_page()
        page.goto(served, wait_until="load")
        page.click("#target")
        got = page.evaluate("() => window.__last")

    assert got is not None, "the page saw no click at all"
    assert (got["x"], got["y"]) != (300, 250), (
        "the click landed on the exact geometric centre. That is one number, "
        "identical in every install and readable from a single event - the "
        "tell the landing code exists to avoid.")


@pytest.mark.e2e
def test_a_handle_evaluate_receives_its_argument(firefox_binary, served):
    """`handle.evaluate(fn, arg)` - the SECOND argument.

    Dropping it does not raise here: it makes the expression throw INSIDE the
    page, which a caller writing a probe reads as "no". That silence is what
    made the landing feature inert.
    """
    with InvisiblePlaywright(seed=42, headless=True,
                             binary_path=str(firefox_binary)) as browser:
        page = browser.new_page()
        page.goto(served, wait_until="load")
        handle = page.query_selector("#target")
        got = handle.evaluate("(el, p) => el.id + ':' + p.x + ',' + p.y",
                              {"x": 7, "y": 9})

    assert got == "target:7,9", (
        "the handle's evaluate answered %r: the argument did not arrive." % got)


@pytest.mark.e2e
def test_dblclick_reaches_an_element_inside_a_frame(firefox_binary, served):
    """`dblclick` accepted `frame_id` and threw it away.

    It is the same family as the `position` defect and was found by the same
    scan: a parameter a method takes and never uses. Here the visible effect is
    a double click resolved in the WRONG document.
    """
    with InvisiblePlaywright(seed=42, headless=True,
                             binary_path=str(firefox_binary)) as browser:
        page = browser.new_page()
        page.goto(served, wait_until="load")
        page.dblclick("#host")
        assert page.evaluate("() => window.__dbl") == 1, (
            "the page saw no dblclick: two clicks with clickCount 1 produce "
            "two `click` events and no `dblclick` at all")
