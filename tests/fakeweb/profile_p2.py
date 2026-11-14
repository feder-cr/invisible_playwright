"""Profile P2 of ci-release-18: ONE context and ONE page, reused for every address, driven by the stock Playwright client.

The shape of a production client (tf-fetch): a stock Playwright client connected to our served browser, a context route
(plain synchronous function: abort image and media, continue the rest), `goto(wait_until="commit")` and then the client-side
wait for `networkidle`. After every difficult page the `quiet` canary is loaded on the same page: a state that survives a
navigation makes the canary fail. The same driver runs against the stock Firefox, launched by the same client, so the
verdict is "same outcome as stock".
"""
from __future__ import annotations

import time

BLOCKED = ("image", "media")


def install_route(context):
    context.route("**/*", lambda route: route.abort() if route.request.resource_type in BLOCKED else route.continue_())


def navigate(page, url, *, idle_timeout_ms):
    rec = {"idle": False, "err": None, "seconds": None}
    # The requests the client has heard of and not yet heard the end of: a navigation that does not idle says who is still in flight
    # (type, method, url, frame). The process that hosts a request is not readable by a client.
    open_requests = {}

    def started(request):
        try:
            frame = request.frame.url[:100]
        except Exception:
            frame = None
        open_requests[id(request)] = {"type": request.resource_type, "method": request.method, "url": request.url[:160],
                                      "frame": frame, "t": round(time.monotonic() - t0, 2)}

    def ended(request):
        open_requests.pop(id(request), None)

    t0 = time.monotonic()
    page.on("request", started)
    page.on("requestfinished", ended)
    page.on("requestfailed", ended)
    try:
        page.goto(url, wait_until="commit", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=idle_timeout_ms)
        rec["idle"] = True
    except Exception as exc:        # recorded, the run goes on: the verdict compares outcomes
        text = str(exc).strip()
        rec["err"] = (text.splitlines()[0] if text else repr(exc))[:200]
        rec["in_flight"] = list(open_requests.values())[:30]
    finally:
        for event, handler in (("request", started), ("requestfinished", ended), ("requestfailed", ended)):
            try:
                page.remove_listener(event, handler)
            except Exception:
                pass
    rec["seconds"] = round(time.monotonic() - t0, 2)
    return rec


def drive(browser, origin, patterns, repetitions, idle_timeout_ms=10000):
    """Every pattern `repetitions` times, each followed by the canary. Returns a list of cells."""
    cells = []
    context = browser.new_context()
    try:
        install_route(context)
        page = context.new_page()
        for rep in range(1, repetitions + 1):
            for name in patterns:
                for pattern, canary in ((name, False), ("quiet", True)):
                    cell = navigate(page, "%s/p/%s" % (origin, pattern), idle_timeout_ms=idle_timeout_ms)
                    cell.update(round=rep, pattern=pattern, canary=canary, after=name if canary else None)
                    cells.append(cell)
    finally:
        try:
            context.close()
        except Exception:
            pass
    return cells


def run_on_candidate(sync_playwright, binary, origin, patterns, repetitions, **kw):
    """Our engine, served over the Playwright WebSocket and driven by the stock client."""
    from invisible_playwright._juggler.remote import launch_server

    server = launch_server(headless=True, binary_path=binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            try:
                return drive(browser, origin, patterns, repetitions, **kw)
            finally:
                browser.close()
    finally:
        server.close()


def run_on_stock(sync_playwright, origin, patterns, repetitions, **kw):
    with sync_playwright() as p:
        browser = p.firefox.launch()
        try:
            return drive(browser, origin, patterns, repetitions, **kw)
        finally:
            browser.close()


def verdict(candidate, stock, declared):
    """The cells where the candidate fails and stock idles, and the cells where stock is not what pages.json declares."""
    key = lambda c: (c["round"], c["pattern"], c["canary"], c["after"])      # noqa: E731
    stock_by = {key(c): c for c in stock}
    fails, broken = [], []
    for c in candidate:
        s = stock_by.get(key(c))
        if s is None:
            continue
        if s["idle"] and not c["idle"]:
            fails.append((key(c), c["err"]))
    for s in stock:
        if declared.get(s["pattern"]) == "idle" and not s["idle"]:
            broken.append((key(s), s["err"]))
    return fails, broken
