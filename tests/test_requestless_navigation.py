"""Navigation events may have a new document but no network request."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def redirect_origins():
    servers = []
    threads = []

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", destination)
                self.end_headers()
                return
            body = b"<!doctype html><title>landed</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        for _ in range(2):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        start = f"http://127.0.0.1:{servers[0].server_port}"
        destination = f"http://localhost:{servers[1].server_port}/landed"
        yield start, destination
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


@pytest.mark.e2e
@pytest.mark.parametrize("wait", ["wait_for_url", "expect_navigation"])
@pytest.mark.parametrize("requestless", [False, True])
def test_navigation_wait(firefox_binary, redirect_origins, wait, requestless):
    from invisible_playwright import InvisiblePlaywright

    start, destination = redirect_origins
    if requestless:
        destination = "about:blank"
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_context().new_page()
        page.goto(start + "/start")
        url = destination if requestless else start + "/redirect"
        if wait == "expect_navigation":
            with page.expect_navigation(url=destination, timeout=5000) as navigation:
                page.evaluate("url => { location.href = url; }", url)
            response = navigation.value
            if requestless:
                assert response is None
            else:
                assert response.status == 200
                assert response.url == destination
        else:
            page.evaluate("url => { setTimeout(() => location.href = url, 100); }", url)
            page.wait_for_url(destination, timeout=5000)
        assert page.url == page.evaluate("location.href") == destination
