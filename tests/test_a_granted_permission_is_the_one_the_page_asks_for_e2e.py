"""`grant_permissions(["geolocation"])` grants the permission the page asks for.

⛔ The names a caller uses (`geolocation`, `notifications`) are the ones of the web platform; the engine keeps a
grant under its own (`geo`, `desktop-notification`). The wrapper passed the web names through, the engine stored
a grant under a name it never reads, and the page was asked, or refused, as if nothing had been granted:
`navigator.geolocation.getCurrentPosition` ended in a denial with a context that had been granted the permission
and given a position. The Playwright client of this browser translates the five names it knows and refuses any
other (playwright-core `ffBrowser.js`, `doGrantPermissions`); so does the wrapper now.

A grant with no origin was sent as the empty string, and the engine finds the origin of a page with
`find(key => url.startsWith(key))`, whose answer `''` is false: it fell back to `'*'`, which held nothing. No origin
is `'*'`, as it is for the Playwright client of this browser, and as the grant made with the context already was.

Red before: the page answers PERMISSION_DENIED (code 1) or never answers.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

READ = """() => new Promise(resolve => {
  const timer = setTimeout(() => resolve({timeout: true}), 8000);
  navigator.geolocation.getCurrentPosition(
    p => { clearTimeout(timer); resolve({lat: p.coords.latitude, lon: p.coords.longitude}); },
    e => { clearTimeout(timer); resolve({error: e.code, message: e.message}); });
})"""


@pytest.fixture
def local_page_url():
    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b"<!doctype html><title>geo</title><p>geo"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = LocalServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d/" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.e2e
def test_a_granted_geolocation_gives_the_page_the_position(firefox_binary, local_page_url):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True) as browser:
        context = browser.new_context(geolocation={"latitude": 41.9, "longitude": 12.5})
        context.grant_permissions(["geolocation"])
        page = context.new_page()
        page.goto(local_page_url)
        answer = page.evaluate(READ)
        assert answer == {"lat": 41.9, "lon": 12.5}, answer


@pytest.mark.e2e
def test_a_permission_granted_when_the_context_is_made_is_the_same(firefox_binary, local_page_url):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True) as browser:
        context = browser.new_context(geolocation={"latitude": 45.4, "longitude": 9.2}, permissions=["geolocation"])
        page = context.new_page()
        page.goto(local_page_url)
        answer = page.evaluate(READ)
        assert answer == {"lat": 45.4, "lon": 9.2}, answer


@pytest.mark.e2e
def test_a_name_outside_the_table_is_refused_and_nothing_is_granted(firefox_binary, local_page_url):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True) as browser:
        context = browser.new_context(geolocation={"latitude": 41.9, "longitude": 12.5})
        with pytest.raises(Exception, match="Unknown permission: camera"):
            context.grant_permissions(["geolocation", "camera"])
