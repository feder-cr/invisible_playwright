"""Hunt scaffolding (never merged): the memory of the content processes, with and without ``preload_newtab=False``.

    python scripts/newtab_memory_probe.py --binary PATH --out DIR [--rounds 5] [--seconds 10]

Alternating rounds (default first, then the option; the next round the other way round). Each run starts the engine with the
library, opens one page of a local site, waits, then reads every content process below this script: its remote type from the
engine's own log (``Setting remoteType of process <pid> to <type>``, ``MOZ_LOG=Process:5,timestamp,sync``) and its memory
(working set on Windows, resident set elsewhere; private bytes where the system gives them). Writes one JSON per run and a
summary in markdown on stdout.
"""
import argparse
import http.server
import json
import os
import re
import statistics
import sys
import threading
import time

TYPE = re.compile(r"(?:Setting remoteType of process (\d+) to (\S+)|Changing remoteType of process (\d+) from \S+ to (\S+))")


def serve():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b"<!doctype html><title>plain</title><p>plain"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def types_from_log(base):
    found = {}
    folder, stem = os.path.split(base)
    for name in os.listdir(folder):
        if name.startswith(stem):
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as fh:
                for m in TYPE.finditer(fh.read()):      # the last type a process was given is the one it has
                    pid, kind = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                    found[int(pid)] = kind.split("^")[0]
    return found


def one_run(binary, out, label, option, seconds, url):
    import psutil

    from invisible_playwright import InvisiblePlaywright

    base = os.path.join(out, "moz_" + label)
    os.environ["MOZ_LOG"] = "Process:5,timestamp,sync"
    os.environ["MOZ_LOG_FILE"] = base
    rows = []
    try:
        with InvisiblePlaywright(seed=7, binary_path=binary, headless=True, humanize=False, locale="en-US",
                                 timezone="America/New_York", **option) as browser:
            page = browser.new_context().new_page()
            page.goto(url)
            time.sleep(seconds)
            types = types_from_log(base)
            for proc in psutil.Process(os.getpid()).children(recursive=True):
                try:
                    cmd = proc.cmdline()
                    if "-contentproc" not in cmd or cmd[-1] != "tab":
                        continue
                    mem = proc.memory_full_info() if hasattr(proc, "memory_full_info") else proc.memory_info()
                    private = getattr(mem, "private", None) or getattr(mem, "uss", None)
                    rows.append({"pid": proc.pid, "type": types.get(proc.pid, "?"), "rss_mb": round(mem.rss / 1e6, 1),
                                 "private_mb": round(private / 1e6, 1) if private else None})
                except (psutil.Error, IndexError):
                    continue
    finally:
        os.environ.pop("MOZ_LOG", None)
        os.environ.pop("MOZ_LOG_FILE", None)
    record = {"label": label, "option": option, "seconds": seconds, "processes": sorted(rows, key=lambda r: r["type"]),
              "total_rss_mb": round(sum(r["rss_mb"] for r in rows), 1),
              "total_private_mb": round(sum(r["private_mb"] or 0 for r in rows), 1)}
    with open(os.path.join(out, label + ".json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    return record


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    server = serve()
    url = "http://127.0.0.1:%d/plain" % server.server_address[1]
    arms = [("default", {}), ("preload_off", {"preload_newtab": False})]
    records = []
    try:
        for n in range(1, args.rounds + 1):
            for name, option in (arms if n % 2 else arms[::-1]):
                rec = one_run(args.binary, args.out, "%s_%d" % (name, n), option, args.seconds, url)
                records.append(rec)
                print("%-14s total rss %7.1f MB, private %7.1f MB: %s" % (
                    rec["label"], rec["total_rss_mb"], rec["total_private_mb"],
                    [(p["type"], p["rss_mb"]) for p in rec["processes"]]), flush=True)
    finally:
        server.shutdown()
    print("\n## Content processes, with and without preload_newtab=False (%d rounds, %s s, one page of a site)\n"
          % (args.rounds, args.seconds))
    print("| arm | runs | total RSS MB (min-max, median) | total private MB (median) | processes by type (median MB) |")
    print("|---|---|---|---|---|")
    for name, _ in arms:
        mine = [r for r in records if r["label"].startswith(name + "_")]
        if not mine:
            continue
        rss = [r["total_rss_mb"] for r in mine]
        private = [r["total_private_mb"] for r in mine]
        by_type = {}
        for r in mine:
            for p in r["processes"]:
                by_type.setdefault(p["type"], []).append(p["rss_mb"])
        kinds = ", ".join("%s x%.1f %.0f" % (t, len(v) / len(mine), statistics.median(v)) for t, v in sorted(by_type.items()))
        print("| %s | %d | %.0f-%.0f, %.0f | %.0f | %s |" % (name, len(mine), min(rss), max(rss), statistics.median(rss),
                                                         statistics.median(private), kinds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
