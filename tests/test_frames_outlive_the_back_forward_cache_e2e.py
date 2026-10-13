"""A page's frames stay in `page.frames` after a few navigations in one tab.

The engine used to report the iframes of the page being loaded as detached
while it loaded them: with the back-forward cache in the parent each
navigation gives the tab a new top-level browsing context, and when the cache
let an old one go, the engine took it for the current main frame and
detached all its children. Measured on firefox-42 with this history: 0 child
frames where there are 3. Fixed in firefox-43 (70-known-bugs [B292] in the
workbench).
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

PAGES = {
    "/framed": b"<!doctype html><title>fr</title>"
               b"<iframe src=/plain></iframe><iframe src=/other></iframe>",
    "/nested": b"<!doctype html><iframe src=/framed></iframe>",
    "/shadow": b"<!doctype html><div id=h></div><iframe src=/plain></iframe><script>"
               b"document.getElementById('h').attachShadow({mode: 'open'}).innerHTML ="
               b" '<iframe src=/other></iframe>';</script>",
}
PLAIN = b"<!doctype html><title>plain</title><p>plain</p>"


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path, PLAIN)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()


def test_the_frames_of_a_page_outlive_an_old_entry_leaving_the_cache(firefox_binary, site):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4292, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        for path in ("", "framed", "shadow", ""):
            page.goto(site + path)
        page.goto(site + "nested")
        urls = sorted(f.url.rsplit("/", 1)[-1] for f in page.frames if f != page.main_frame)
        assert urls == ["framed", "other", "plain"]
        assert page.frame_locator("iframe").frame_locator("iframe").first.locator("p").inner_text() == "plain"
