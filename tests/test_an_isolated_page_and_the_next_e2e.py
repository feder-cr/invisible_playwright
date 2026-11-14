"""A page in a process of its own, then the page after it, on a real engine.

⛔ THE KNOWN-BAD INPUT, measured on 2026-10-24 against firefox-52 and 0.42.0:
a page served with Cross-Origin-Opener-Policy and Cross-Origin-Embedder-Policy
(the shape of a challenge page) runs in a process of its own, and the page it
sends itself to does not. One read on the first page, and every locator,
`inner_text` and `query_selector` on the second answered

    Cannot find object with id = id-10

for good, while `evaluate`, `content` and `screenshot` kept working. Juggler
counted its context ids per content process, so the second process named its
utility world as the first had, and the cached InjectedScript of the first was
sent to it. From firefox-53 the ids are unique for the session (juggler
Runtime.js) and the core drops every world on `executionContextsCleared`
(`test_a_page_that_changes_process.py` in invisible_core).

Each round checks `crossOriginIsolated` on the first page: a round where the
engine did not isolate it would not have changed process, and would pass for
the wrong reason.

The page moves to the second one when the test says so, right after the read
on the first: it used to go on its own 500 ms after `load`, and on a loaded
Windows runner more than 500 ms can pass between `domcontentloaded` and the
first read, so that read met the second page or the move itself (2 rounds in 20
under four workers, runs 43435932449 and 43439508625: `crossOriginIsolated`
false, and "Execution context was destroyed").
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

ROUNDS = 3

PAGES = {
    "/isolated": (b"<!doctype html><html><body><p id=p>isolated</p><script>"
                  b"window.moveOn = () => setTimeout(() => "
                  b"location.replace('/plain' + location.search), 0)"
                  b"</script></body></html>"),
    "/plain": b"<!doctype html><html><body><p id=p>plain</p></body></html>",
}


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            body = PAGES.get(path, PAGES["/plain"])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if path == "/isolated":
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    # Threading: Firefox's speculative connections starve a single-threaded
    # server (test_navigation_response.py).
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture(scope="module")
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4321, binary_path=firefox_binary,
                             headless=True) as browser:
        yield browser.new_page()


def test_reads_after_the_isolated_page_answer(page, site):
    for i in range(ROUNDS):
        page.goto("%s/isolated?i=%d" % (site, i), wait_until="domcontentloaded")
        assert page.evaluate("self.crossOriginIsolated") is True, (
            "the engine did not isolate the page: this round tests nothing")
        # The read on the isolated page is what builds the script that went
        # stale; without it the bug never showed.
        assert page.locator("#p").inner_text(timeout=10_000) == "isolated"
        # The page sends itself on, as a challenge page does: the move is its own location.replace, not a goto of ours.
        page.evaluate("moveOn()")
        page.wait_for_url("**/plain?i=%d" % i, timeout=10_000)
        assert page.evaluate("self.crossOriginIsolated") is False
        assert page.locator("#p").inner_text(timeout=10_000) == "plain"
        assert page.inner_text("#p", timeout=10_000) == "plain"
        assert page.query_selector("#p") is not None
        assert page.locator("#p").all_inner_texts() == ["plain"]
