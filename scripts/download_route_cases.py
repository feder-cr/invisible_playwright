"""The download cases of the retro: a fresh page against a page reused after a `route`, with and without `unroute`, in a context
made with no `accept_downloads` (as the X12 probe did); stock client against our served browser, and the stock reference.

    python route_cases.py --binary PATH --out FILE [--arms ours,stock]

For our served browser the server runs in this process, so the engine's `Browser.downloadCreated` / `Browser.downloadFinished`
are recorded as the server receives them.
"""
import argparse
import importlib
import json
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote_download_repro as base_repro  # noqa: E402

EVENTS = []


def record_engine_events():
    from invisible_playwright._juggler import server as srv

    created, finished = srv.BrowserDispatcher._on_download_created, srv.BrowserDispatcher._on_download_finished

    def on_created(self, params):
        EVENTS.append({"t": round(time.time(), 3), "event": "downloadCreated", "uuid": params.get("uuid"), "url": params.get("url")})
        return created(self, params)

    def on_finished(self, params):
        EVENTS.append({"t": round(time.time(), 3), "event": "downloadFinished", **params})
        return finished(self, params)

    srv.BrowserDispatcher._on_download_created = on_created
    srv.BrowserDispatcher._on_download_finished = on_finished


def one(page, base, link, expected):
    out = {"file": link}
    try:
        page.goto(base + "/")
        with page.expect_download(timeout=20000) as info:
            page.click("#" + link)
        download = info.value
        try:
            target = os.path.join(tempfile.mkdtemp(prefix="dl_"), "saved_" + link)
            download.save_as(target)
            with open(target, "rb") as fh:
                out["save_as"] = "ok" if fh.read() == expected else "WRONG BYTES"
        except Exception as exc:
            out["save_as"] = "EXC %s" % str(exc).splitlines()[0][:160]
        try:
            out["failure"] = download.failure()
        except Exception as exc:
            out["failure"] = "EXC %s" % str(exc).splitlines()[0][:160]
    except Exception as exc:
        out["error"] = "EXC %s" % str(exc).splitlines()[0][:160]
    return out


def cases(browser, base):
    rows = []
    for case in ("fresh", "after-route", "after-route-unroute", "after-route-abort-images"):
        context = browser.new_context()          # no accept_downloads, as the X12 probe
        try:
            page = context.new_page()
            if case == "after-route":
                page.route("**/*", lambda route: route.continue_())
                page.goto(base + "/")
            elif case == "after-route-unroute":
                page.route("**/*", lambda route: route.continue_())
                page.goto(base + "/")
                page.unroute("**/*")
            elif case == "after-route-abort-images":
                context.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media")
                              else route.continue_())
                page.goto(base + "/")
            for link, expected in (("t", base_repro.TEXT), ("b", base_repro.BINARY)):
                row = one(page, base, link, expected)
                row["case"] = case
                rows.append(row)
        finally:
            try:
                context.close()
            except Exception:
                pass
    return rows


def arm_ours(binary, base):
    from invisible_playwright._juggler.remote import launch_server

    record_engine_events()
    server = launch_server(headless=True, binary_path=binary, locale="en-US", timezone="Europe/Rome")
    try:
        sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            try:
                return cases(browser, base)
            finally:
                browser.close()
    finally:
        server.close()


def arm_stock(base):
    import subprocess

    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"headless": True}, fh)
    proc = subprocess.Popen([sys.executable, "-m", "playwright", "launch-server", "--browser", "firefox", "--config", fh.name],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        endpoint = None
        end = time.time() + 60
        while time.time() < end:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("ws://"):
                endpoint = line.strip()
                break
        with sync_playwright() as p:
            browser = p.firefox.connect(endpoint)
            try:
                return cases(browser, base)
            finally:
                browser.close()
    finally:
        proc.kill()
        proc.wait(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", default="ours,stock")
    args = ap.parse_args()
    server = base_repro.serve()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    record = {}
    try:
        for arm in args.arms.split(","):
            try:
                rows = arm_ours(args.binary, base) if arm == "ours" else arm_stock(base)
            except Exception as exc:
                rows = [{"case": "-", "error": "EXC %s" % str(exc).splitlines()[0][:200], "trace": traceback.format_exc()[-1200:]}]
            record[arm] = rows
            for r in rows:
                print("%-6s %-26s %-2s save_as=%-50s failure=%s %s" % (arm, r.get("case"), r.get("file", ""), str(r.get("save_as"))[:50],
                                                                     r.get("failure"), r.get("error", "")), flush=True)
    finally:
        server.shutdown()
    record["engine_events"] = EVENTS
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    for e in EVENTS:
        print("engine:", json.dumps(e)[:220])
    return 0


if __name__ == "__main__":
    sys.exit(main())
