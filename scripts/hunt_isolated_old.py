"""HUNT SCAFFOLDING, NEVER MERGED: the isolated-page rounds of tests/test_an_isolated_page_and_the_next_e2e.py, run with whatever
invisible_playwright is installed (the job installs 0.42.0, the release before the fix, with its engine firefox-52).

Usage: python scripts/hunt_isolated_old.py signal|timer. `signal` is the new variant (the test makes the page move right after the read),
`timer` the old one (the page moves 500 ms after load). Prints one line per round: ok, or the error, and a last line RESULT red/green.
"""
import http.server
import sys
import threading

VARIANT = sys.argv[1]
MOVE = (b"window.moveOn = () => setTimeout(() => location.replace('/plain' + location.search), 0)" if VARIANT == "signal" else
        b"addEventListener('load', () => setTimeout(() => location.replace('/plain' + location.search), 500))")
PAGES = {"/isolated": b"<!doctype html><html><body><p id=p>isolated</p><script>" + MOVE + b"</script></body></html>",
         "/plain": b"<!doctype html><html><body><p id=p>plain</p></body></html>"}


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


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
site = "http://127.0.0.1:%d" % srv.server_address[1]

import invisible_playwright
from invisible_playwright import InvisiblePlaywright
print("wrapper", getattr(invisible_playwright, "__version__", "?"), flush=True)
red = False
with InvisiblePlaywright(seed=4321, headless=True) as browser:
    page = browser.new_page()
    for i in range(3):
        try:
            page.goto("%s/isolated?i=%d" % (site, i), wait_until="domcontentloaded")
            assert page.evaluate("self.crossOriginIsolated") is True, "not isolated"
            assert page.locator("#p").inner_text(timeout=10_000) in ("isolated", "plain")
            if VARIANT == "signal":
                page.evaluate("moveOn()")
            page.wait_for_url("**/plain?i=%d" % i, timeout=10_000)
            assert page.evaluate("self.crossOriginIsolated") is False
            assert page.locator("#p").inner_text(timeout=10_000) == "plain"
            assert page.inner_text("#p", timeout=10_000) == "plain"
            assert page.query_selector("#p") is not None
            assert page.locator("#p").all_inner_texts() == ["plain"]
            print("round %d ok" % i, flush=True)
        except Exception as e:
            red = True
            print("round %d ERROR %s" % (i, str(e).splitlines()[0][:200]), flush=True)
print("RESULT", "red" if red else "green", flush=True)
srv.shutdown()
