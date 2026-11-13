"""HUNT SCAFFOLDING, NEVER MERGED: goto(networkidle) on pages that replace themselves after load, ours against stock Playwright Firefox.

Usage: python scripts/hunt_netidle.py ours|stock [binary]. Prints one JSON line per round: the URL when goto returned, how long goto took, and
when the server saw the request of /landed relative to goto's return (negative = the replacement was already on the wire when goto said idle).
"""
import http.server
import json
import os
import sys
import threading
import time

ENGINE = sys.argv[1]
if len(sys.argv) > 3:
    os.environ["HUNT_LC_TRACE"] = sys.argv[3]
REQS = []


def _page(ms, cross):
    target = "location.origin.replace('127.0.0.1','localhost')+'/landed'" if cross else "'/landed'"
    return ("<!doctype html><html><body><p id=p>first</p><script>addEventListener('load', () => setTimeout(() => "
            "location.replace(%s), %d))</script></body></html>" % (target, ms)).encode()


PAGES = {"/landed": b"<!doctype html><html><body><p id=p>landed</p></body></html>"}
for ms in (0, 50):
    PAGES["/r%d" % ms] = _page(ms, False)
    PAGES["/x%d" % ms] = _page(ms, True)


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        REQS.append((time.monotonic(), self.path))
        body = PAGES.get(self.path.split("?")[0], PAGES["/landed"])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
site = "http://127.0.0.1:%d" % srv.server_address[1]


def rounds(page, Error):
    for path in ("/r0", "/r50", "/x0", "/x50"):
        for i in range(5):
            t0 = time.monotonic()
            err = None
            try:
                page.goto("%s%s?n=%d" % (site, path, i), wait_until="networkidle", timeout=15000)
            except Error as e:
                err = str(e).splitlines()[0][:100]
            t1 = time.monotonic()
            url = page.url
            landed = [t for t, p in REQS if p.startswith("/landed") and t >= t0]
            print("HUNT-ROW " + json.dumps({
                "engine": ENGINE, "path": path, "round": i, "landed_at_return": url.endswith("/landed"), "goto_ms": round((t1 - t0) * 1000),
                "landed_request_ms_from_return": round((landed[0] - t1) * 1000) if landed else None, "error": err,
                "t0": round(t0, 4), "t1": round(t1, 4), "landed_t": round(landed[0], 4) if landed else None}), flush=True)


if ENGINE == "stock":
    from playwright.sync_api import sync_playwright, Error
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        rounds(b.new_page(), Error)
        b.close()
else:
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.sync_api import Error
    with InvisiblePlaywright(seed=4321, binary_path=sys.argv[2], headless=True) as browser:
        rounds(browser.new_page(), Error)
srv.shutdown()
