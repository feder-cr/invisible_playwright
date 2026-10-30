"""A click that submits a form or follows a link returns once that navigation
has committed, as Playwright's does; `no_wait_after=True` does not wait.

**Why this file exists.** From Firefox 157 a form submission is a PLANNED
navigation (`dom.forms.submit_async_navigation`, the retail's default): it
starts after the click that submitted it has returned. Measured 2026-10-27 on
the first 157 engines: `page.click("input[type=submit]")` came back 7 ms before
the POST was announced, so a script that clicked and then read its requests, or
the URL, saw the page it had left. On 151 the submission started inside the
click, and Microsoft's client waits for planned navigations
(`waitForSignalsCreatedBy`), so it never showed there. Playwright's own suite
caught it: test_should_parse_the_data_if_content_type_is_form_urlencoded failed
3/3 on 157 and passed 3/3 on 151.

The engine now reports the plan (Page.linkClicked "before" / "after", from
nsDocShell) and `FrameDispatcher._waiting_after` waits for it and for the
navigation it starts to commit (`Lifecycle.wait_for_action_signals`).

Known-bad inputs, each run against this file before it was trusted:

* click and press without `_waiting_after` -> the form and the Enter tests go
  red on a 157 engine (no request, the old URL);
* `_waiting_after` that ignores `noWaitAfter` -> the last test goes red: the
  click waits for the slow page.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

FORM = (b"<form method='POST' action='/post'>"
        b"<input name='foo' value='bar'><input type='submit' id='go'></form>"
        b"<a id='next' href='/next'>next</a>"
        b"<a id='slow' href='/slow'>slow</a>")


def _serve():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _page(self, body):
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/slow":
                time.sleep(3)
            self._page(FORM if self.path == "/" else b"<title>%s</title>" % self.path.encode())

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self._page(b"<title>posted</title>")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port


@pytest.mark.e2e
def test_a_click_that_submits_a_form_returns_on_the_posted_page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto(url, wait_until="load", timeout=30_000)
            requests = []
            page.on("request", lambda r: requests.append((r.method, r.post_data)))

            page.click("#go", timeout=30_000)
            assert ("POST", "foo=bar") in requests, (
                "the click returned before its form's request: %r" % requests)
            assert page.url == url + "post", page.url

            page.goto(url, wait_until="load", timeout=30_000)
            page.press("input[name=foo]", "Enter", timeout=30_000)
            assert page.url == url + "post", (
                "Enter submitted the form after press() returned: %s" % page.url)

            page.goto(url, wait_until="load", timeout=30_000)
            page.click("#next", timeout=30_000)
            assert page.url == url + "next", page.url
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_no_wait_after_returns_without_waiting_for_the_navigation(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto(url, wait_until="load", timeout=30_000)
            started = time.monotonic()
            page.click("#slow", no_wait_after=True, timeout=30_000)
            assert time.monotonic() - started < 2.5, (
                "no_wait_after=True waited for the navigation")
            page.wait_for_url(url + "slow", timeout=30_000)
    finally:
        srv.shutdown()
