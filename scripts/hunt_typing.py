"""HUNT SCAFFOLDING, NEVER MERGED: one typed address, three times per key: planned, sent (before the keydown command), seen by the page.

Usage: python scripts/hunt_typing.py ours|stock [binary]. HUNT_ARM names the arm in the output (core from PyPI, core from git, stock). Both type the same plan (persona of seed 2, typing nonce 1, the plan the core's
keyboard draws for a session's first field): ours through press_sequentially (the core keyboard), stock as down / sleep(dwell) / up /
sleep(gap) through stock Playwright's keyboard. One JSON line per run with the three series in wall-clock milliseconds, plus the page's
keyup times and, per run: slip at the last key (page against plan), the smallest gap between two keydowns the page saw, and each key's
dwell (keyup - keydown, seen by the page) against the planned dwell.
"""
import os
import http.server
import json
import sys
import threading
import time

from invisible_core.juggler._behaviour import TypingPersona, plan_typing

ENGINE = sys.argv[1]
TEXT = "someone@example.com"
PLAN = plan_typing(TEXT, TypingPersona.from_seed(2), nonce=1)
SENT = []
PAGE = (b"<!doctype html><html><body><input id=f><script>window.__k=[];window.__u=[];document.addEventListener('keydown',"
        b"e=>__k.push([e.key, performance.timeOrigin + performance.now()]),true);document.addEventListener('keyup',"
        b"e=>__u.push([e.key, performance.timeOrigin + performance.now()]),true);</script></body></html>")


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % srv.server_address[1]


def report(page, t_start):
    keys = page.evaluate("__k")
    ups = page.evaluate("__u")
    planned, t = [], 0.0
    for dwell, gap in PLAN:
        planned.append(round(t))
        t += dwell + gap
    downs = [(k, v) for k, v in keys if k != "Shift"]
    releases = [(k, v) for k, v in ups if k != "Shift"]
    page_ms = [round(v - downs[0][1]) for k, v in downs] if downs else []
    # a key's dwell: its keyup after its keydown, matched by order (the same key never overlaps itself in a plan)
    dwell_page, dwell_err = [], []
    if len(downs) == len(releases) == len(PLAN):
        for (kd, d), (ku, u), (planned_dwell, _gap) in zip(downs, releases, PLAN):
            dwell_page.append(round(u - d, 1))
            dwell_err.append(round((u - d) - planned_dwell, 1))
    plan_gaps = [planned[i + 1] - planned[i] for i in range(len(planned) - 1)]
    page_gaps = [page_ms[i + 1] - page_ms[i] for i in range(len(page_ms) - 1)]
    print("HUNT-TYPING " + json.dumps({
        "engine": ENGINE, "arm": os.environ.get("HUNT_ARM", ENGINE), "planned_ms": planned,
        "sent_ms": [round(x * 1000 - SENT[0] * 1000) for x in SENT if True],
        "page_ms": page_ms,
        "page_keys": [k for k, v in keys], "send_offset_ms": round((SENT[0] - t_start) * 1000) if SENT else None,
        "keyup_ms": [round(v - downs[0][1]) for k, v in releases] if downs else [],
        "planned_dwell_ms": [round(d, 1) for d, g in PLAN],
        "dwell_page_ms": dwell_page, "dwell_err_ms": dwell_err,
        "slip_last_ms": (page_ms[-1] - planned[-1]) if len(page_ms) == len(planned) else None,
        "min_keydown_gap_ms": min(page_gaps) if page_gaps else None,
        "plan_min_keydown_gap_ms": min(plan_gaps) if plan_gaps else None,
        "all_keys_seen": len(downs) == len(PLAN) and len(releases) == len(PLAN),
    }), flush=True)


if ENGINE == "stock":
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_page()
        for _ in range(5):
            SENT.clear()
            page.goto(url)
            page.click("#f")
            t_start = time.time()
            for (dwell, gap), ch in zip(PLAN, TEXT):
                SENT.append(time.time())
                if ch == "@":
                    page.keyboard.down("Shift")
                page.keyboard.down(ch)
                time.sleep(dwell / 1000)
                page.keyboard.up(ch)
                if ch == "@":
                    page.keyboard.up("Shift")
                time.sleep(gap / 1000)
            report(page, t_start)
        b.close()
else:
    from invisible_core.juggler import keyboard as kb
    real_down = kb.Keyboard.down

    def down(self, key, *a, **k):
        if key != "Shift":
            SENT.append(time.time())
        return real_down(self, key, *a, **k)

    kb.Keyboard.down = down
    from invisible_playwright import InvisiblePlaywright
    for _ in range(5):
        # a fresh browser per run: the plan is the one of the session's first field (typing nonce 1)
        with InvisiblePlaywright(seed=2, binary_path=sys.argv[2], headless=True) as browser:
            SENT.clear()
            page = browser.new_page()
            page.goto(url)
            t_start = time.time()
            page.locator("#f").press_sequentially(TEXT)
            report(page, t_start)
srv.shutdown()
