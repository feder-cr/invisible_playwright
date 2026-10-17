"""`route.abort()` with no code fails the request as Playwright's Firefox does (B325).

Measured 2026-10-17 with Playwright's own `test_should_not_chain_abort`, 10
runs out of 10: `page.goto` of an aborted navigation failed with
`NS_ERROR_ABORT`, where Playwright on Firefox says `NS_ERROR_FAILURE`.
Playwright's server sends `errorCode || "failed"` and the engine maps "failed"
to `NS_ERROR_FAILURE`; this server defaulted to "aborted". An error text is a
value a script reads.
"""
from __future__ import annotations

import http.server
import threading
from types import SimpleNamespace

import pytest


def _route():
    from invisible_playwright._juggler.server import RouteDispatcher

    sent = []
    route = object.__new__(RouteDispatcher)
    route.answered = False
    route.request = SimpleNamespace(
        request_id="R1",
        page=SimpleNamespace(send=lambda command, params: sent.append((command, params))))
    route.dispose = lambda: None
    return route, sent


@pytest.mark.unit
def test_an_abort_with_no_code_is_failed():
    route, sent = _route()
    route.op_abort({})
    assert sent == [("Network.abortInterceptedRequest",
                     {"errorCode": "failed", "requestId": "R1"})]


@pytest.mark.unit
def test_an_abort_with_a_code_keeps_it():
    route, sent = _route()
    route.op_abort({"errorCode": "aborted"})
    assert sent[0][1]["errorCode"] == "aborted"


@pytest.mark.e2e
@pytest.mark.parametrize("code, text", [(None, "NS_ERROR_FAILURE"), ("aborted", "NS_ERROR_ABORT")])
def test_an_aborted_navigation_fails_with_the_engines_word_for_the_code(firefox_binary, code, text):
    from invisible_playwright import InvisiblePlaywright

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><title>e</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with InvisiblePlaywright(binary_path=firefox_binary, headless=True) as browser:
            page = browser.new_page()
            page.route("**/empty.html",
                       lambda route: route.abort() if code is None else route.abort(code))
            with pytest.raises(Exception) as caught:
                page.goto("http://127.0.0.1:%d/empty.html" % srv.server_address[1])
    finally:
        srv.shutdown()
    assert text in str(caught.value), str(caught.value)
