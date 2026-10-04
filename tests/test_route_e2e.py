"""`route()` holds a page's requests, or refuses to be set.

The known-bad input, measured on 2026-10-02: a context route set as a guard
("abort every POST") never ran. The server received the page's fetch POST and
its beacon while the handler saw nothing, and `context.route` returned without
an error. Two defects stacked:

- Firefox offers a request to Juggler's interception hook only while
  `dom.serviceWorkers.enabled` is true. With it false the engine accepted
  `setRequestInterception` and held nothing.
- With the pref on, the first held request was answered on the browser session
  instead of the page's, the engine refused the command, and the page hung.

And `page.route` was refused outright as an unimplemented gap.

`new_context(service_workers="block")` was accepted and ignored. Playwright
honours it with a page script that replaces `navigator.serviceWorker.register`
with a function whose source names Playwright, which every page can read, so
here it refuses instead, and the route tests run with service workers allowed.
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

PAGE = (b"<!doctype html><html><body><script>"
        b"fetch('/api', {method: 'POST', body: '{}'})"
        b".then(r => document.title = 'post:' + r.status)"
        b".catch(() => document.title = 'post:blocked');"
        b"navigator.sendBeacon('/beacon', 'x');"
        b"</script></body></html>")


def _serve():
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(("GET", self.path))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.append(("POST", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port, hits


@pytest.mark.e2e
@pytest.mark.parametrize("where", ["context", "page"])
def test_a_route_guard_sees_and_stops_every_post(firefox_binary, where):
    from invisible_playwright import InvisiblePlaywright

    srv, url, hits = _serve()
    routed, blocked = [], []

    def guard(route):
        routed.append(route.request.method)
        if route.request.method in ("GET", "HEAD", "OPTIONS"):
            route.continue_()
        else:
            blocked.append(route.request.url)
            route.abort()

    try:
        with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                                 headless=True) as browser:
            context = browser.new_context()
            page = context.new_page()
            (context if where == "context" else page).route("**/*", guard)
            page.goto(url, wait_until="load", timeout=20_000)
            page.wait_for_function("document.title.startsWith('post:')",
                                   timeout=10_000)
            assert page.title() == "post:blocked", page.title()
            # routing leaves nothing in the page: the container is the
            # browser's own, with no property planted on it
            assert page.evaluate(
                "Object.getOwnPropertyNames(navigator.serviceWorker).length"
                " === 0 && navigator.serviceWorker.register.toString()"
                ".includes('[native code]')")
    finally:
        srv.shutdown()
    assert "GET" in routed and routed.count("POST") == 2, routed
    assert [m for m, _ in hits if m == "POST"] == [], hits
    assert sorted(u.rsplit("/", 1)[1] for u in blocked) == ["api", "beacon"]


@pytest.mark.e2e
@pytest.mark.parametrize("where", ["context", "page"])
def test_a_route_without_service_workers_refuses_instead_of_ignoring(
        firefox_binary, where):
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.sync_api import Error

    with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                             headless=True,
                             extra_prefs={"dom.serviceWorkers.enabled": False}
                             ) as browser:
        context = browser.new_context()
        page = context.new_page()
        with pytest.raises(Error, match="dom.serviceWorkers.enabled=false"):
            (context if where == "context" else page).route(
                "**/*", lambda route: route.continue_())


@pytest.mark.e2e
def test_blocking_service_workers_refuses_instead_of_planting_a_page_script(
        firefox_binary):
    """Playwright's way to block them is a page-visible override that names
    Playwright; ignoring the option is a promise nobody keeps. Refusing is
    the only answer that is true and leaves the page untouched."""
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.sync_api import Error

    with InvisiblePlaywright(seed=4243, binary_path=firefox_binary,
                             headless=True) as browser:
        with pytest.raises(Error, match='service_workers="block" is not '
                                        'supported'):
            browser.new_context(service_workers="block")
        # the browser is still usable after the refusal
        page = browser.new_context().new_page()
        assert page.evaluate("1 + 1") == 2
