"""Hunt scaffolding (never merged): a download on a served browser, read by a client that connects to it.

    python scripts/remote_download_repro.py --binary PATH --out FILE [--arms ours-stock,ours-library,ours-inprocess,stock]

For each arm, against one local server: a text file and a binary file, each served as an attachment and reached by a click on
a link. The script waits for the download (`expect_download`), then calls `save_as`, `path`, `failure`, and compares the bytes
saved with the bytes served. Arms:

- ours-stock      our engine served by `launch_server`, Playwright's own client connected to it;
- ours-library    the same server, the library's own client connected to it;
- ours-inprocess  the library launching the engine in this process (the path every in-process test covers);
- stock           Playwright's own Firefox served by Playwright's own `launch-server`, Playwright's own client: the reference.

Prints one line per arm and file, and writes the whole record as JSON.
"""
import argparse
import hashlib
import http.server
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback

TEXT = ("one line of text\n" * 2000).encode("utf-8")
BINARY = bytes(range(256)) * 4096          # 1 MiB, every byte value


def serve():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/text.txt"):
                body, ctype, name = TEXT, "text/plain; charset=utf-8", "feed.txt"
            elif self.path.startswith("/data.bin"):
                body, ctype, name = BINARY, "application/octet-stream", "data.bin"
            else:
                body = (b"<!doctype html><title>d</title><a id=t href='/text.txt'>text</a> "
                        b"<a id=b href='/data.bin'>binary</a>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def digest(data):
    return hashlib.sha256(data).hexdigest()[:16]


def one_download(browser, base, link, expected):
    """One file: every call answered or its error, in order."""
    out = {"file": link}
    context = browser.new_context(accept_downloads=True)
    try:
        page = context.new_page()
        page.goto(base + "/")
        with page.expect_download(timeout=20000) as info:
            page.click("#" + link)
        download = info.value
        out["suggested_filename"] = download.suggested_filename
        for name, call in (("failure", lambda: download.failure()),
                           ("path", lambda: download.path()),
                           ("save_as", None)):
            try:
                if name == "save_as":
                    target = os.path.join(tempfile.mkdtemp(prefix="dl_"), "saved_" + link)
                    download.save_as(target)
                    with open(target, "rb") as fh:
                        data = fh.read()
                    out["save_as"] = "ok" if data == expected else "WRONG BYTES (%d, %s)" % (len(data), digest(data))
                else:
                    value = call()
                    if name == "path":
                        with open(value, "rb") as fh:
                            data = fh.read()
                        out["path"] = "ok" if data == expected else "WRONG BYTES (%d)" % len(data)
                    else:
                        out["failure"] = value
            except Exception as exc:       # every call reported, none stops the others
                out[name] = "EXC %s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:200])
    except Exception as exc:
        out["error"] = "EXC %s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:200])
        out["trace"] = traceback.format_exc()[-1500:]
    finally:
        try:
            context.close()
        except Exception:
            pass
    return out


def drive(browser, base):
    return [one_download(browser, base, "t", TEXT), one_download(browser, base, "b", BINARY)]


def arm_ours_served(binary, base, client):
    from invisible_playwright._juggler.remote import launch_server

    server = launch_server(headless=True, binary_path=binary, locale="en-US", timezone="Europe/Rome")
    try:
        if client == "stock":
            sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
        else:
            sync_playwright = importlib.import_module("invisible_playwright._pw.sync_api").sync_playwright
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            try:
                return drive(browser, base)
            finally:
                browser.close()
    finally:
        server.close()


def arm_ours_inprocess(binary, base):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(binary_path=binary, headless=True) as browser:
        return drive(browser, base)


def arm_stock(base):
    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"headless": True}, fh)
        config = fh.name
    proc = subprocess.Popen([sys.executable, "-m", "playwright", "launch-server", "--browser", "firefox", "--config", config],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        endpoint, end = None, time.time() + 60
        while time.time() < end:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("ws://"):
                endpoint = line.strip()
                break
        if not endpoint:
            raise RuntimeError("stock launch-server printed no endpoint")
        with sync_playwright() as p:
            browser = p.firefox.connect(endpoint)
            try:
                return drive(browser, base)
            finally:
                browser.close()
    finally:
        proc.kill()
        proc.wait(10)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", default="stock,ours-stock,ours-library,ours-inprocess")
    args = ap.parse_args(argv)
    server = serve()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    record = {}
    try:
        for arm in args.arms.split(","):
            try:
                if arm == "stock":
                    rows = arm_stock(base)
                elif arm == "ours-stock":
                    rows = arm_ours_served(args.binary, base, "stock")
                elif arm == "ours-library":
                    rows = arm_ours_served(args.binary, base, "library")
                elif arm == "ours-inprocess":
                    rows = arm_ours_inprocess(args.binary, base)
                else:
                    raise ValueError(arm)
            except Exception as exc:
                rows = [{"file": "-", "error": "EXC %s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:200]),
                         "trace": traceback.format_exc()[-1500:]}]
            record[arm] = rows
            for row in rows:
                print("%-15s %-2s save_as=%-40s path=%-14s failure=%-10s %s" % (
                    arm, row.get("file"), str(row.get("save_as"))[:40], str(row.get("path"))[:14], row.get("failure"),
                    row.get("error", "")), flush=True)
    finally:
        server.shutdown()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
