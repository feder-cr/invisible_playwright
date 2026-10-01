"""A page the SITE opens is a Page the caller can see, with its first responses.

Measured 2026-10-01: a `target=_blank` link and a `window.open` each attached a
new target in the browser, and the server built nothing for it. Only
`newPage` built Pages, so `context.pages` never listed the tab,
`expect_popup()` and `expect_page()` timed out, and a tab the site later
closed was never announced. The second half is what made it expensive: a PDF
opened in a new tab is requested and answered before any Page could exist, so
its response - the only copy of the file, since the viewer will not give it
back - never reached `context.on("response")`.

⛔ THE LAST TEST IS THE CONTROL. A page the caller opens itself carries no
opener and must still be built exactly once.
"""
from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

PDF = b"%PDF-1.4\n%%EOF\n"
PAGE = b"""<!DOCTYPE html><html><body>
<a id="tab" href="/doc.pdf" target="_blank">pdf in a new tab</a>
<button id="win" onclick="window.open('/closes')">window.open</button>
</body></html>"""
CLOSES = b"<!DOCTYPE html><script>setTimeout(() => window.close(), 500)</script>bye"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body, kind = {"/doc.pdf": (PDF, "application/pdf"),
                      "/closes": (CLOSES, "text/html")}.get(self.path, (PAGE, "text/html"))
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_page():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d" % srv.server_address[1]
        finally:
            srv.shutdown()


@pytest.mark.e2e
def test_a_new_tab_is_a_popup_and_its_document_response_is_seen(firefox_binary, local_page):
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        context = browser.new_context()
        page = context.new_page()
        page.goto(local_page, wait_until="load")
        seen = []
        context.on("response", lambda r: seen.append(r))
        with page.expect_popup(timeout=15000) as info:
            page.click("#tab")
        popup = info.value
        popup.wait_for_load_state()
        assert popup.opener() == page
        assert popup in context.pages and len(context.pages) == 2
        documents = [r for r in seen if r.url.endswith("/doc.pdf")]
        assert len(documents) == 1, [r.url for r in seen]
        assert documents[0].body() == PDF


@pytest.mark.e2e
def test_a_tab_the_site_closes_is_announced_closed(firefox_binary, local_page):
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        context = browser.new_context()
        page = context.new_page()
        page.goto(local_page, wait_until="load")
        with context.expect_page(timeout=15000) as info:
            page.click("#win")
        opened = info.value
        opened.wait_for_event("close", timeout=15000)
        assert opened.is_closed()
        assert context.pages == [page]


@pytest.mark.e2e
def test_a_page_the_caller_opens_is_built_once(firefox_binary, local_page):
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        context = browser.new_context()
        announced = []
        context.on("page", lambda p: announced.append(p))
        first = context.new_page()
        second = context.new_page()
        first.goto(local_page, wait_until="load")
        assert announced == [first, second]
        assert context.pages == [first, second]
        assert first.opener() is None
