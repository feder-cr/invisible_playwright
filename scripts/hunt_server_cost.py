"""Hunt only: what a browser costs with our server in the user's process, out of it, and with stock Playwright.

One arm, N browsers at once in this (client) process, REPS rounds. Per browser and round it prints one JSON line:
  start_ms      from the call that starts the browser to the first new_page() returning
  client_mb     RSS of this process (the user's process) with the N browsers open, after a page has loaded
  server_mb     RSS of the processes that serve the browsers and are not Firefox: our launch_server processes
                (oursremote) or Playwright's Node driver (stock); 0 for ours in-process
  firefox_mb    RSS of every Firefox process under this process (the same engine work in every arm, as a check)

    python scripts/hunt_server_cost.py ours|oursremote|stock N REPS [binary]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import psutil

PAGE = "data:text/html,<title>cost</title><p>cost"


def _tree_rss():
    me = psutil.Process()
    client = me.memory_info().rss
    server = firefox = 0
    for child in me.children(recursive=True):
        try:
            name = child.name().lower()
            rss = child.memory_info().rss
        except psutil.Error:
            continue
        if "firefox" in name:
            firefox += rss
        elif "python" in name or "node" in name:
            server += rss
    mb = lambda b: round(b / 2 ** 20, 1)
    return mb(client), mb(server), mb(firefox)


async def _serve_one(binary):
    proc = await asyncio.create_subprocess_exec(sys.executable, __file__, "serve", binary,
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    while True:
        line = (await asyncio.wait_for(proc.stdout.readline(), 120)).decode().strip()
        if not line:
            raise RuntimeError("the server process ended before it served")
        if line.startswith("ENDPOINT "):
            return proc, line.split(" ", 1)[1]


def serve_one_browser(binary):
    from invisible_playwright._juggler.remote import launch_server
    server = launch_server(headless=True, binary_path=binary, seed=42, humanize=False)
    print("ENDPOINT " + server.ws_endpoint, flush=True)
    try:
        sys.stdin.read()
    finally:
        server.close()


async def one_round(arm, n, binary):
    """N browsers at once; returns their start times, then the memory with all of them open."""
    opened, closers, starts = [], [], []

    async def start_one():
        t0 = time.monotonic()
        if arm == "ours":
            from invisible_playwright.async_api import InvisiblePlaywright
            cm = InvisiblePlaywright(seed=42, binary_path=binary, humanize=False, headless=True)
            browser = await cm.__aenter__()
            closers.append(lambda: cm.__aexit__(None, None, None))
        elif arm == "oursremote":
            from playwright.async_api import async_playwright
            proc, endpoint = await _serve_one(binary)
            pw = await async_playwright().start()
            browser = await pw.firefox.connect(endpoint)

            async def close_remote(browser=browser, pw=pw, proc=proc):
                await browser.close()
                await pw.stop()
                proc.stdin.close()
                await asyncio.wait_for(proc.wait(), 30)
            closers.append(close_remote)
        else:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser = await pw.firefox.launch(headless=True)

            async def close_stock(browser=browser, pw=pw):
                await browser.close()
                await pw.stop()
            closers.append(close_stock)
        page = await browser.new_page()
        starts.append(round((time.monotonic() - t0) * 1000))
        await page.goto(PAGE)
        opened.append(page)

    await asyncio.gather(*(start_one() for _ in range(n)))
    await asyncio.sleep(2)   # let the processes settle before the memory is read
    client, server, firefox = _tree_rss()
    for close in closers:
        await close()
    return starts, client, server, firefox


async def main(arm, n, reps, binary):
    for rep in range(reps):
        starts, client, server, firefox = await one_round(arm, n, binary)
        print("COSTROW " + json.dumps({"os": sys.platform, "arm": arm, "n": n, "rep": rep, "start_ms": starts,
                                       "client_mb": client, "server_mb": server, "firefox_mb": firefox}), flush=True)
        await asyncio.sleep(1)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve_one_browser(sys.argv[2])
        sys.exit(0)
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None))
