"""HUNT SCAFFOLDING, NEVER MERGED: the processes of the retail Firefox and of this browser on the same page, measured the same way.

    python scripts/study_processes.py --retail <firefox executable> --ours <firefox executable> --out <dir> [--seconds 30]

One local page (a PeerConnection, so the socket process starts, and an iframe of another site) opened in a fresh profile by each
browser, one after the other. After `--seconds`, for every process of the browser's tree: its kind (from the -contentproc command
line), resident and private memory, the sandbox (Windows: integrity level and AppContainer of the token; Linux: the Seccomp line of
/proc), and its connections that are not loopback; the names the browser resolved (MOZ_LOG nsHostResolver:5) and the files under
the profile's datareporting/ at the end. The retail is launched bare (its own command line); ours through the package, as a user
launches it. Writes <out>/<name>.json and prints a table.
"""
from __future__ import annotations

import argparse
import ctypes
import glob
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import psutil

PAGE = b"""<!doctype html><title>study</title><body>study
<iframe src="http://localhost:%d/frame"></iframe>
<script>
const pc = new RTCPeerConnection({iceServers: []});
pc.createDataChannel('x');
pc.createOffer().then(o => pc.setLocalDescription(o));
</script></body>"""


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = PAGE % self.server.server_address[1] if self.path == "/" else b"<!doctype html><title>frame</title>frame"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _kind(cmd):
    if "-contentproc" not in cmd:
        return "parent" if cmd else "?"
    for k in ("socket", "gpu", "rdd", "utility", "tab", "gmplugin", "forkserver"):
        if k in cmd:
            return k
    return cmd[-1] if cmd else "?"


def _integrity(pid):
    if sys.platform != "win32":
        try:
            for line in open("/proc/%d/status" % pid):
                if line.startswith("Seccomp:"):
                    return "seccomp=" + line.split()[1]
        except OSError:
            return None
        return None
    k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    vp = ctypes.c_void_p
    k32.OpenProcess.restype, k32.OpenProcess.argtypes = vp, [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.CloseHandle.argtypes = [vp]
    adv.OpenProcessToken.argtypes = [vp, ctypes.c_ulong, ctypes.POINTER(vp)]
    adv.GetTokenInformation.argtypes = [vp, ctypes.c_int, vp, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    adv.GetSidSubAuthorityCount.restype, adv.GetSidSubAuthorityCount.argtypes = ctypes.POINTER(ctypes.c_ubyte), [vp]
    adv.GetSidSubAuthority.restype, adv.GetSidSubAuthority.argtypes = ctypes.POINTER(ctypes.c_ulong), [vp, ctypes.c_ulong]
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    tok = vp()
    try:
        if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            return None
        need = ctypes.c_ulong()
        adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(need))
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(tok, 25, ctypes.cast(buf, vp), need, ctypes.byref(need)):
            return None
        sid = ctypes.cast(buf, ctypes.POINTER(vp))[0]
        count = adv.GetSidSubAuthorityCount(sid)[0]
        rid = adv.GetSidSubAuthority(sid, count - 1)[0]
        level = {0: "untrusted", 0x1000: "low", 0x2000: "medium", 0x2100: "medium+", 0x3000: "high", 0x4000: "system"}.get(rid, hex(rid))
        app = ctypes.c_ulong()
        adv.GetTokenInformation(tok, 29, ctypes.cast(ctypes.byref(app), vp), 4, ctypes.byref(need))
        return level + (" appcontainer" if app.value else "")
    finally:
        if tok:
            k32.CloseHandle(tok)
        k32.CloseHandle(h)


def _snapshot(exe):
    """Every process whose executable sits in the folder of `exe` (the retail's launcher process on Windows starts the browser
    again and exits, so its own pid has no tree)."""
    folder = os.path.normcase(os.path.dirname(os.path.abspath(exe)))
    procs, denied = [], 0
    for p in psutil.process_iter(["exe", "name", "cmdline"]):
        try:
            path = p.info["exe"]
            if path and os.path.normcase(os.path.dirname(path)) == folder:
                procs.append(p)
            elif not path and (p.info["name"] or "").lower() == os.path.basename(exe).lower():
                # a token that may not read the image path (a Basic User token): the name, and the folder in the command line
                cmd = " ".join(p.info["cmdline"] or [])
                if not cmd or os.path.normcase(os.path.dirname(os.path.abspath(exe))) in os.path.normcase(cmd):
                    procs.append(p)
        except psutil.Error:
            denied += 1
            continue
    print("  (processes not readable from this token: %d)" % denied)
    out = []
    for p in procs:
        try:
            cmd = p.cmdline()
            mem = p.memory_full_info()
            conns = [("%s:%s" % c.raddr) for c in p.net_connections(kind="inet") if c.raddr and not c.raddr.ip.startswith(("127.", "::1"))]
            out.append({"pid": p.pid, "name": p.name(), "kind": _kind(cmd), "rss_mb": round(mem.rss / 2**20, 1),
                        "private_mb": round(getattr(mem, "private", getattr(mem, "uss", 0)) / 2**20, 1),
                        "sandbox": _integrity(p.pid), "connections": sorted(set(conns))})
        except psutil.Error:
            continue
    return out


def _resolved(logdir):
    names = set()
    for f in glob.glob(os.path.join(logdir, "*")):
        for line in open(f, encoding="utf-8", errors="replace"):
            m = re.search(r"Resolving host \[([^\]]+)\]|host \[([^\]]+)\]", line)
            if m:
                names.add((m.group(1) or m.group(2)).split(":")[0])
    return sorted(n for n in names if n not in ("localhost", "127.0.0.1"))


def _datareporting(profile):
    root = os.path.join(profile, "datareporting")
    return sorted(os.path.relpath(os.path.join(d, f), profile) for d, _s, fs in os.walk(root) for f in fs) if os.path.isdir(root) else []


def retail(exe, url, seconds, work):
    profile = tempfile.mkdtemp(prefix="retail_", dir=work)
    logdir = tempfile.mkdtemp(prefix="retail_log_", dir=work)
    env = dict(os.environ, MOZ_LOG="timestamp,nsHostResolver:5", MOZ_LOG_FILE=os.path.join(logdir, "moz"))
    subprocess.Popen([exe, "-no-remote", "-profile", profile, url], env=env)
    time.sleep(seconds)
    snap = _snapshot(exe)
    for q in snap:
        try:
            psutil.Process(q["pid"]).kill()
        except psutil.Error:
            pass
    time.sleep(3)
    return {"browser": "retail", "processes": snap, "resolved": _resolved(logdir), "datareporting": _datareporting(profile)}


def ours(exe, url, seconds, work):
    from invisible_playwright import InvisiblePlaywright
    logdir = tempfile.mkdtemp(prefix="ours_log_", dir=work)
    os.environ.update(MOZ_LOG="timestamp,nsHostResolver:5", MOZ_LOG_FILE=os.path.join(logdir, "moz"))
    with InvisiblePlaywright(binary_path=exe, headless=True) as browser:
        page = browser.new_page()
        page.goto(url)
        time.sleep(seconds)
        snap = _snapshot(exe)
        parents = [psutil.Process(q["pid"]) for q in snap if q["kind"] == "parent"]
        cmd = parents[0].cmdline() if parents else []
        profile = cmd[cmd.index("-profile") + 1] if "-profile" in cmd else None
        reporting = _datareporting(profile) if profile else []
    return {"browser": "ours", "processes": snap, "resolved": _resolved(logdir), "datareporting": reporting}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retail", required=True)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="study_")
    srv = _serve()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    for n in range(1, a.repeat + 1):
      for name, fn, exe in (("retail", retail, a.retail), ("ours", ours, a.ours)):
        r = fn(exe, url, a.seconds, work)
        r.update(platform=sys.platform, seconds=a.seconds, executable=exe, round=n)
        json.dump(r, open(os.path.join(a.out, "%s_%d.json" % (name, n)), "w"), indent=1)
        print("== %s round %d: %d processes, %d names resolved, %d datareporting files" % (name, n, len(r["processes"]), len(r["resolved"]), len(r["datareporting"])))
        for p in sorted(r["processes"], key=lambda x: x["kind"]):
            print("  %-10s rss %6.1f MB  private %6.1f MB  sandbox %-22s conns %d" % (p["kind"], p["rss_mb"], p["private_mb"], p["sandbox"], len(p["connections"])))
        print("  resolved:", ", ".join(r["resolved"]))
        print("  datareporting:", ", ".join(r["datareporting"]))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
