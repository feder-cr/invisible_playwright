"""Hunt only: a goto to another site, stopped by a timer of the page it leaves, in our browser and in stock Firefox.

One batch of reps of one arm, four browsers at once, one fresh browser per rep. Each rep prints one JSON line:
  late_ms     how late the page's setTimeout(1000) fired, measured INSIDE the page (performance.now)
  fired       when it fired (Date.now of the page, ms)
  req, resp   when the server got the request of /slow-frame and when it sent the response (time.time, ms)
  error, url  what the goto raised and where the page ended
The page and the server share the machine's wall clock, so "fired" and "resp" compare directly.

    python scripts/hunt_goto_stopped.py ours|stock|stockfission REPS FIRST_REP [binary]
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, "tests")
from _servers import LocalServer  # noqa: E402

PREFS = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
    "network.dns.localDomains": "a.test,b.test,c.test",
}


def serve():
    log = {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def log_message(self, *args):
            pass

        def do_POST(self):
            parts = urlsplit(self.path)
            q = parse_qs(parts.query)
            if parts.path == "/stopped":
                log.setdefault(q["rep"][0], {}).update(late_ms=float(q["late"][0]), fired=float(q["at"][0]))
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            parts = urlsplit(self.path)
            q = parse_qs(parts.query)
            try:
                if parts.path == "/slow-frame":
                    log.setdefault(q["rep"][0], {})["req"] = time.time() * 1000
                    time.sleep(3)
                body = ("<!doctype html><title>%s</title><p>%s" % (parts.path, parts.path)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if parts.path == "/slow-frame":
                    log[q["rep"][0]]["resp"] = time.time() * 1000
            except OSError:
                pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, log


async def scenario(page, port, rep):
    await page.goto("http://a.test:%d/plain" % port)
    await page.evaluate("""rep => { const t0 = performance.now(); setTimeout(() => {
        navigator.sendBeacon('/stopped?rep=' + rep + '&late=' + (performance.now() - t0 - 1000) + '&at=' + Date.now());
        window.stop(); }, 1000); }""", str(rep))
    error = None
    began = time.time() * 1000
    try:
        await page.goto("http://b.test:%d/slow-frame?rep=%d" % (port, rep), wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        error = str(e).splitlines()[0][:80]
    ended = time.time() * 1000
    await asyncio.sleep(4)   # the beacon may reach the server after the goto has ended
    return error, page.url, began, ended


async def one(arm, rep, port, binary, gate):
    async with gate:
        if arm == "ours":
            from invisible_playwright.async_api import InvisiblePlaywright
            async with InvisiblePlaywright(seed=42, binary_path=binary, humanize=False, headless=True, extra_prefs=PREFS) as browser:
                page = await browser.new_page()
                return await scenario(page, port, rep)
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            # stock: Playwright's Firefox as shipped (its playwright.cfg sets webContentIsolationStrategy 0 by autoconfig, which
            # wins over the prefs given at launch); stockfission: a copy of the same Firefox whose playwright.cfg says 1
            exe = {"executable_path": binary} if arm == "stockfission" else {}
            browser = await p.firefox.launch(headless=True, firefox_user_prefs=PREFS, **exe)
            try:
                page = await browser.new_page()
                return await scenario(page, port, rep)
            finally:
                await browser.close()


async def main(arm, reps, first, binary):
    server, log = serve()
    port = server.server_address[1]
    gate = asyncio.Semaphore(4)
    numbers = list(range(first, first + reps))
    results = await asyncio.gather(*(one(arm, r, port, binary, gate) for r in numbers), return_exceptions=True)
    await asyncio.sleep(1)
    for rep, res in zip(numbers, results):
        row = {"arm": arm, "rep": rep}
        row.update(log.get(str(rep), {}))
        if isinstance(res, Exception):
            row["crash"] = repr(res)[:120]
        else:
            row["error"], row["url"], row["goto_began"], row["goto_ended"] = res
        print("HUNTROW " + json.dumps(row), flush=True)
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None))
