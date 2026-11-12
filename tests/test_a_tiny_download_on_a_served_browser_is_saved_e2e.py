"""Tiny downloads through a served browser are saved by a stock client, every one.

A download of a few bytes ends in the engine before the server has handled its announcement, so the server meets
`Browser.downloadFinished` first. A download that succeeded has no error, and the old code read "finished with no
error" as "nothing recorded": the artifact was never marked finished and `save_as` waited forever.

One served browser, a stock Playwright client, a page that links to a 300-byte attachment and to a 1-byte one:
ten downloads of each, each saved and compared byte for byte.

Known-bad input: the server before the fix -> a `save_as` of an early-finished download never returns.
"""
from __future__ import annotations

import http.server
import importlib
import threading

import pytest

pytestmark = pytest.mark.e2e

BODIES = {"big": bytes(range(256)) + b"\x01" * 44, "one": b"Z"}
COUNT = 10


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            kind = self.path.strip("/").split("/")[0].split("?")[0]
            if kind in BODIES:
                body = BODIES[kind]
                self.send_response(200)
                self.send_header("content-type", "application/octet-stream")
                self.send_header("content-disposition", 'attachment; filename="%s.bin"' % kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            links = "".join('<a id="%s%d" href="/%s?n=%d">%s</a> ' % (k, i, k, i, k)
                            for k in BODIES for i in range(COUNT))
            page = ("<!doctype html><title>d</title>" + links).encode()
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

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
        srv.server_close()


def test_ten_tiny_downloads_of_each_size_are_saved_through_a_served_browser(firefox_binary, served, tmp_path):
    try:     # the stock client (a pinned dev dependency), imported by name: the fork's guard forbids a static import
        sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    except ImportError as exc:
        pytest.fail("the stock Playwright client is missing (%s): install the wrapper with its dev extra" % exc,
                    pytrace=False)
    from invisible_playwright._juggler.remote import launch_server

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            try:
                page = browser.new_context(accept_downloads=True).new_page()
                page.goto(served, timeout=30_000)
                for kind, body in BODIES.items():
                    for i in range(COUNT):
                        with page.expect_download(timeout=20_000) as info:
                            page.click("#%s%d" % (kind, i))
                        target = tmp_path / ("%s%d.bin" % (kind, i))
                        info.value.save_as(target)
                        assert target.read_bytes() == body, (kind, i)
                        assert info.value.failure() is None
            finally:
                browser.close()
    finally:
        server.close()
