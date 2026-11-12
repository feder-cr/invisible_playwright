"""Hunt scaffolding (never merged): which content processes a Firefox starts for ONE page of one site, over time.

    python scripts/second_process_probe.py --firefox PATH --label NAME --out FILE [--seconds 20] [--pool N] [--blank]

Starts the given Firefox by itself (no driver, no library: `firefox --headless --profile DIR URL`) on a new profile, with a
local page of the site `a.test` (or, with --blank, `about:blank`), and samples the names of its content processes every
100 ms. The profile sets `fission.processSiteNames`, with which a content process that hosts a site is named by that site
(ContentChild::SetProcessName), so the table says for which site each process is, and when it got it. Linux only: no other
system names a process.

The question it answers: the same-site control of the wrapper's suite saw a second "Isolated Web Content" process appear
a few seconds after the first, in every run, with nothing asked by the page. Does a retail Firefox do the same?
"""
import argparse
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

PREFS = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
    "fission.processSiteNames": True,
    "network.dns.localDomains": "a.test,b.test,c.test",
    # a new profile opens nothing of its own
    "browser.shell.checkDefaultBrowser": False,
    "browser.startup.homepage": "about:blank",
    "browser.startup.page": 0,
    "browser.newtabpage.enabled": False,
    "browser.aboutwelcome.enabled": False,
    "startup.homepage_welcome_url": "",
    "startup.homepage_welcome_url.additional": "",
    "startup.homepage_override_url": "",
    "browser.startup.homepage_override.mstone": "ignore",
    "datareporting.policy.dataSubmissionEnabled": False,
    "datareporting.policy.dataSubmissionPolicyBypassNotification": True,
    "toolkit.telemetry.reportingpolicy.firstRun": False,
    "termsofuse.bypassNotification": True,
    "browser.preonboarding.enabled": False,
    "app.update.enabled": False,
    "browser.sessionstore.resume_from_crash": False,
}


def pref_line(name, value):
    return "user_pref(%s, %s);" % (json.dumps(name), json.dumps(value))


def serve():
    class Handler(http.server.BaseHTTPRequestHandler):
        hits = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            Handler.hits.append({"t": round(time.time(), 3), "host": self.headers.get("Host"), "path": self.path})
            body = b"<!doctype html><title>plain</title><p>plain" if self.path == "/plain" else b""
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Handler.hits


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_into(names, seen, children, started):
    import psutil

    for child in children:
        try:
            cmd = child.cmdline()
            if "-contentproc" not in cmd:
                continue
            key = (child.name(), cmd[-1])
            if seen.get(child.pid) != key:
                seen[child.pid] = key
                names.append({"at": round(time.time() - started, 2), "pid": child.pid, "name": key[0], "kind": key[1],
                              "born": round(child.create_time() - started, 2),
                              "rss_mb": round(child.memory_info().rss / 1e6, 1)})
        except (psutil.Error, IndexError):
            continue


def final_table(seen, started):
    import psutil

    final = []
    for pid, (name, kind) in sorted(seen.items()):
        try:
            child = psutil.Process(pid)
            final.append({"pid": pid, "name": child.name(), "kind": kind, "born": round(child.create_time() - started, 2),
                          "rss_mb": round(child.memory_info().rss / 1e6, 1)})
        except psutil.Error:
            final.append({"pid": pid, "name": name, "kind": kind, "gone": True})
    return final


def report(args, url, names, final, hits, started, extra=None):
    record = {"label": args.label, "firefox": args.firefox, "firefox_sha256": sha256(args.firefox), "url": url, "pool": args.pool,
              "prefs": args.pref, "via_wrapper": args.via_wrapper, "seconds": args.seconds, "names": names, "final": final,
              "hits": [dict(h, t=round(h["t"] - started, 2)) for h in hits]}
    record.update(extra or {})
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    tabs = [f for f in final if f["kind"] == "tab" and not f.get("gone")]
    print("%-26s url=%-12s pool=%-2s %s page asked at %s; tab processes at the end: %s" % (
        args.label, url.split("/")[-1][:12], args.pool, " ".join(args.pref), [h["t"] for h in hits if h["path"] == "/plain"][:1],
        [(f["name"], "born %+.2f" % f["born"], "%s MB" % f["rss_mb"]) for f in tabs]))
    print("   names over time: %s" % [(n["at"], n["pid"], n["name"]) for n in names if n["kind"] == "tab"])
    return 0


def via_wrapper(args, prefs, url, server, hits):
    """The engine started by the library, as the suite does it; the page opened with page.goto."""
    import psutil

    from invisible_playwright import InvisiblePlaywright

    keep = ("fission.", "dom.ipc.", "network.dns.localDomains")
    extra_prefs = {k: v for k, v in prefs.items() if k.startswith(keep)}
    names, seen, marks = [], {}, {}
    started = time.time()
    try:
        with InvisiblePlaywright(seed=42, binary_path=args.firefox, humanize=False, headless=True,
                                 extra_prefs=extra_prefs) as browser:
            marks["launched"] = round(time.time() - started, 2)
            page = browser.new_context().new_page()
            marks["page_open"] = round(time.time() - started, 2)
            if not args.blank:
                page.goto(url)
                marks["goto_done"] = round(time.time() - started, 2)
            while time.time() - started < args.seconds:
                sample_into(names, seen, psutil.Process(os.getpid()).children(recursive=True), started)
                page.wait_for_timeout(100)
            final = final_table(seen, started)
    finally:
        server.shutdown()
    print("   instants: %s" % marks)
    return report(args, url, names, final, hits, started, {"instants": marks})


def main(argv=None):
    import psutil

    ap = argparse.ArgumentParser()
    ap.add_argument("--firefox", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--pool", type=int, default=0, help="dom.ipc.processPrelaunch.fission.number; -1 leaves the default")
    ap.add_argument("--blank", action="store_true", help="open about:blank and no page of a site")
    ap.add_argument("--pref", action="append", default=[], help="name=json value, added to the profile (repeatable)")
    ap.add_argument("--via-wrapper", action="store_true",
                    help="start the engine with the library (InvisiblePlaywright) and open the page with page.goto")
    args = ap.parse_args(argv)

    server, hits = serve()
    url = "about:blank" if args.blank else "http://a.test:%d/plain" % server.server_address[1]
    profile = tempfile.mkdtemp(prefix="probe_profile_")
    prefs = dict(PREFS)
    if args.pool >= 0:
        prefs["dom.ipc.processPrelaunch.fission.number"] = args.pool
    for item in args.pref:
        name, _, value = item.partition("=")
        prefs[name] = json.loads(value)
    if args.via_wrapper:
        return via_wrapper(args, prefs, url, server, hits)
    with open(os.path.join(profile, "user.js"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(pref_line(k, v) for k, v in prefs.items()) + "\n")
    log_base = os.path.splitext(args.out)[0] + ".moz"
    env = dict(os.environ, MOZ_LOG="Process:5,ProcessIsolation:5,timestamp,sync", MOZ_LOG_FILE=log_base,
               MOZ_CRASHREPORTER_DISABLE="1")
    started = time.time()
    proc = subprocess.Popen([args.firefox, "--headless", "--no-remote", "--profile", profile, url], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    names, seen = [], {}
    try:
        while time.time() - started < args.seconds:
            try:
                children = psutil.Process(proc.pid).children(recursive=True)
            except psutil.Error:
                children = []
            sample_into(names, seen, children, started)
            time.sleep(0.1)
        final = final_table(seen, started)
    finally:
        try:
            for child in psutil.Process(proc.pid).children(recursive=True):
                child.kill()
        except psutil.Error:
            pass
        proc.kill()
        proc.wait(10)
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
    return report(args, url, names, final, hits, started)


if __name__ == "__main__":
    sys.exit(main())
