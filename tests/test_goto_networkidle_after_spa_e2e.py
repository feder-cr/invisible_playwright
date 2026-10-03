"""`goto(..., wait_until="networkidle")` away from a quiet single-page app.

The known-bad input, measured on 2026-10-03: a production login page (a
single-page app that rewrites its URL with `history.pushState` after load)
went quiet, and the next `goto` to another site with
`wait_until="networkidle"` returned at once with NO Response while the browser
was still on the old page. A caller reads that None as a same-document
navigation. The wait had been satisfied by the OLD page's
silence before the new document's request was even sent; `wait_until="load"`
was unaffected.

Traced on the engine's events: with a `beforeunload` listener on the old page,
Firefox sends `Page.navigationStarted` BEFORE the new document's
`Network.requestWillBeSent` (it has to ask the old page first). In that gap
the request counter is zero and has been for seconds, so a wait that reads
only the counter is satisfied by the page being left. Without the listener the
request comes first and the defect does not show, which is why the listener is
the load-bearing line of this fixture, not the pushState.

This is the same shape without depending on anybody's site: a page that
pushStates after load and listens for `beforeunload`, then a cross-origin
target whose server takes a moment to answer. The unit half is in
`test_juggler_lifecycle.py`.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

SPA = (b"<!doctype html><html><head><title>spa</title></head><body>spa"
       b"<script>addEventListener('load', () => {"
       b" history.pushState({}, '', '/spa/route');"
       b" setTimeout(() => history.pushState({}, '', '/spa/later'), 50);"
       b"});"
       b"addEventListener('beforeunload', () => {});"
       b"addEventListener('unload', () => {});"
       b"</script></body></html>")
SLOW = b"<!doctype html><html><head><title>slow</title></head><body>slow</body></html>"


def _serve(body, delay=0.0):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if delay and not self.path.startswith("/favicon"):
                time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


@pytest.mark.e2e
def test_networkidle_goto_leaves_a_quiet_spa_and_answers_for_the_new_page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    spa, spa_port = _serve(SPA)
    slow, slow_port = _serve(SLOW, delay=1.5)
    # Different host spelling as well as port: a cross-origin navigation.
    spa_url = "http://127.0.0.1:%d/" % spa_port
    slow_url = "http://localhost:%d/landing" % slow_port
    try:
        with InvisiblePlaywright(seed=4244, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            first = page.goto(spa_url, wait_until="networkidle", timeout=20_000)
            assert first is not None and first.status == 200, first
            time.sleep(1.0)  # the SPA has pushed its routes and gone quiet
            assert page.url.endswith("/spa/later"), page.url

            response = page.goto(slow_url, wait_until="networkidle",
                                 timeout=20_000)
            assert page.url == slow_url, (
                "goto(networkidle) returned while still on %s" % page.url)
            assert response is not None, (
                "goto answered no Response for a cross-document navigation")
            assert response.status == 200 and response.url == slow_url, (
                response.status, response.url)
            assert page.title() == "slow"
    finally:
        spa.shutdown()
        slow.shutdown()
