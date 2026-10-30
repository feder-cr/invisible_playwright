"""A download reaches Playwright: downloadCreated, then downloadFinished, and the
file is where the context's download options say.

**Why this file exists.** The engine accepted Browser.setDownloadOptions and
registered Juggler's interceptor (nsExternalHelperAppService::SetDownloadInterceptor),
but nothing ever called it: no Browser.downloadCreated, no downloadFinished, and
a navigation that ends in a download got no event telling it so. Playwright
Firefox calls the interceptor from nsExternalAppHandler::OnStartRequest, and
reports completion from OnSaveComplete and Cancel. Reported 2026-10-30 on the
151 and 159 engines.

Known-bad input: the engine before the fix -> every test here goes red
(expect_download times out).
"""
from __future__ import annotations

import http.server
import threading

import pytest

BODY = b"invisible download body\n" * 64


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/file"):
                self.send_response(200)
                self.send_header("content-type", "application/octet-stream")
                self.send_header("content-disposition", 'attachment; filename="report.bin"')
                self.send_header("content-length", str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY)
                return
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b'<title>d</title><a id=dl href="/file">get</a>')

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


@pytest.fixture
def served():
    srv, url = _serve()
    try:
        yield url
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_a_clicked_download_is_reported_and_saved(firefox_binary, served, tmp_path):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_context(accept_downloads=True).new_page()
        page.goto(served, timeout=30_000)
        with page.expect_download(timeout=20_000) as info:
            page.click("#dl")
        download = info.value
        assert download.suggested_filename == "report.bin"
        target = tmp_path / "report.bin"
        download.save_as(target)
        assert target.read_bytes() == BODY
        assert download.failure() is None


@pytest.mark.e2e
def test_a_navigation_that_ends_in_a_download_is_reported(firefox_binary, served):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_context(accept_downloads=True).new_page()
        with page.expect_download(timeout=20_000) as info:
            with pytest.raises(Exception) as nav:
                page.goto(served + "file", timeout=20_000)
        # Playwright reports such a goto as "Download is starting"; it must END,
        # not hang until the timeout.
        assert "download" in str(nav.value).lower(), str(nav.value)
        assert info.value.suggested_filename == "report.bin"
