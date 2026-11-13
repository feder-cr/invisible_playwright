"""HUNT SCAFFOLDING, NEVER MERGED: the plain round trip, 200 x page.evaluate("1"), ours against stock. Usage: hunt_rtt.py ours|stock [binary]"""
import json
import sys
import time

ENGINE = sys.argv[1]


def run(page):
    page.goto("data:text/html,<p>x</p>")
    t = []
    for _ in range(200):
        t0 = time.perf_counter()
        page.evaluate("1")
        t.append((time.perf_counter() - t0) * 1000)
    t.sort()
    print("HUNT-RTT " + json.dumps({"engine": ENGINE, "p50": round(t[100], 2), "p95": round(t[190], 2), "max": round(t[-1], 2)}), flush=True)


if ENGINE == "stock":
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        run(b.new_page())
        b.close()
else:
    from invisible_playwright import InvisiblePlaywright
    with InvisiblePlaywright(seed=2, binary_path=sys.argv[2], headless=True) as browser:
        run(browser.new_page())
