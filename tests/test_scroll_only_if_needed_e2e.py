"""scroll_into_view_if_needed - and so every click - leaves an element that is
already in full view where it is, and centres one that is not.

**Why this file exists.** invisible_core's `InjectedScript.scroll_into_view`
centred the element every time. Playwright asks for it before every click (and
EVA before every mouse move), so each click on a visible element moved the
page under it: from scrollY 1000, a button in plain view sent the page to ~745,
a button inside a same-origin iframe to ~867. Chromium's
`scrollIntoViewIfNeeded` and a person do not scroll for something they see.

Known-bad input, run against this file before it was trusted: the core before
the fix -> the two "in full view" tests go red; the "below the fold" one stays
green on both, because that path did not change.
"""
from __future__ import annotations

import http.server
import threading

import pytest

PAGE = b"""<!doctype html><title>s</title><body style="margin:0">
<button id=seen style="position:absolute;top:1100px;left:50px">seen</button>
<iframe id=f srcdoc="<body style='margin:0'><button id=inner style='margin:20px'>in</button></body>"
        style="position:absolute;top:1200px;left:50px;width:300px;height:150px"></iframe>
<div style="height:5000px"></div>
<button id=low style="position:absolute;top:3600px;left:50px">low</button>
</body>"""


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


@pytest.fixture
def page_at_1000(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto(url, timeout=30_000)
            page.evaluate("window.scrollTo(0, 1000)")
            assert page.evaluate("window.scrollY") == 1000
            yield page
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_an_element_in_full_view_is_not_scrolled_and_its_click_does_not_scroll(page_at_1000):
    page = page_at_1000
    page.locator("#seen").scroll_into_view_if_needed()
    assert page.evaluate("window.scrollY") == 1000
    page.locator("#seen").click()
    assert page.evaluate("window.scrollY") == 1000


@pytest.mark.e2e
def test_an_element_in_full_view_inside_a_frame_is_not_scrolled(page_at_1000):
    page = page_at_1000
    page.frame_locator("#f").locator("#inner").scroll_into_view_if_needed()
    assert page.evaluate("window.scrollY") == 1000


@pytest.mark.e2e
def test_an_element_below_the_fold_is_centred(page_at_1000):
    page = page_at_1000
    page.locator("#low").scroll_into_view_if_needed()
    top, height, vh = page.evaluate(
        "(() => { const r = document.getElementById('low').getBoundingClientRect();"
        " return [r.top, r.height, document.documentElement.clientHeight]; })()")
    assert abs((top + height / 2) - vh / 2) <= 2, (top, height, vh)
