"""HUNT SCAFFOLDING, NEVER MERGED: one typed address, three times per key: planned, sent (before the keydown command), seen by the page.

Usage: python scripts/hunt_typing.py ours|stock [binary]. HUNT_ARM names the arm in the output (core from PyPI, core from git, both, stock).
The page stamps every keydown and keyup twice: "h" = performance.timeOrigin + performance.now() read in the handler, "e" = performance.timeOrigin
+ event.timeStamp (the moment the event was made). Dwell error and the smallest keydown gap are reported on both clocks. Both type the same plan (persona of seed 2, typing nonce 1, the plan the core's
keyboard draws for a session's first field): ours through press_sequentially (the core keyboard), stock as down / sleep(dwell) / up /
sleep(gap) through stock Playwright's keyboard. One JSON line per run with the three series in wall-clock milliseconds, plus the page's
keyup times and, per run: slip at the last key (page against plan), the smallest gap between two keydowns the page saw, and each key's
dwell (keyup - keydown, seen by the page) against the planned dwell.

ENGINE HUNT additions: HUNT_STAMPS=1 means the engine was patched by scripts/hunt_patch_omni.py. The browser keeps its stamps in memory
while keys are typed and writes them once, when it ends (`browser.dom.window.dump.enabled` is passed for it): ours collects the lines from
the core's ProcessOutput reader AFTER the browser is closed (the "hk" field, so a run's line is printed once its browser has ended), stock's
stay in this process's log through DEBUG=pw:browser. The content process's lines are also asked of the page at the end of the run, with a
'hunt-flush' event on the window and a read of window.__hs ("hs"). Every arm launches with -purgecaches. HUNT_RUNS (default 5) = runs
per invocation. SEND-SPLIT hunt: ours also stamps S1 (os.write returned in the core's Connection._write for each Page.dispatchKeyEvent message,
kept raw in memory, field "s1": [wall ms, the JSON of the message]); S0 is the existing "sent_wall_ms" (Keyboard.down before the send). Stock has no S1:
its write is made by Playwright's node driver. The JSON also carries what the summary needs to put the stamps next to the page's events: the wall clock
(calibrated, sub-millisecond) of each send ("sent_wall_ms"), the page's ordered events ("ev": type, key, handler clock, event clock, all
absolute ms) and the window of the typing ("win").

PROFILE hunt additions: HUNT_PROFILE=1 starts the Gecko profiler in the browser from its first instruction and has it write its profile at exit
(scripts/hunt_profiler_env.py): ours one launch per run (a browser per run), stock one per invocation (its browser is kept for the runs of an
invocation). The profile path is a TEMPLATE with "%p" (the browser puts the writer's pid there): every process that writes a profile writes its own file,
so the pingsender background task the parent starts at shutdown cannot overwrite the parent's profile (run 43551469616). The record carries the template
("profile"). Ours: after the core's close() (which only waits for the Windows launcher stub) the run waits for the PARENT pid, found in the stamps, to be
gone, then checks that file (scripts/hunt_profile_check.py: complete JSON, the parent's pid, covers the typing, the anchor agrees with meta.startTime, the
buffer did not wrap) and prints a "PROFILE-CHECK {json}" line, kept in the record as "profile_check". Stock: the same check on the invocation's profile
(the largest file of the template) once Playwright's close() returned. Ours (stamped) also times our side of the send (scripts/hunt_ourside.py: lock proxies,
holders, thread CPU against wall, a Python stack sampler): "wr" = one record per Page.dispatchKeyEvent write, "wl" = the cases over 30 ms.
HUNT_PROFILE unset = the same arm without the profiler (the twin).
"""
import os
import http.server
import json
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt_ourside
import hunt_profile_check
import hunt_profiler_env
from invisible_core.juggler._behaviour import TypingPersona, plan_typing

ENGINE = sys.argv[1]
STAMPS = os.environ.get("HUNT_STAMPS") == "1"
PROFILE = os.environ.get("HUNT_PROFILE") == "1"
ARM = os.environ.get("HUNT_ARM", sys.argv[1])
CUR_PROFILE = [None]
OURSIDE = ([], [])
DUMP_PREF = {"browser.dom.window.dump.enabled": True}
TEXT = "someone@example.com"
PLAN = plan_typing(TEXT, TypingPersona.from_seed(2), nonce=1)
SENT = []
SENT_W = []
S1 = []
HK = []


def _anchor():
    """wall clock = anchor + perf_counter(), the anchor taken on the tick time.time() changes (its granularity can be 15 ms on Windows)"""
    t0 = time.perf_counter()
    a = time.time()
    while time.time() == a and time.perf_counter() - t0 < 0.05:
        pass
    return time.time() - time.perf_counter()


ANCHOR = _anchor()


def wall_ms():
    return (ANCHOR + time.perf_counter()) * 1000


def mark():
    SENT.append(time.time())
    SENT_W.append(round(wall_ms(), 3))


PAGE = (b"<!doctype html><html><body><input id=f><script>window.__k=[];window.__u=[];document.addEventListener('keydown',"
        b"e=>__k.push([e.key, performance.timeOrigin + performance.now(), performance.timeOrigin + e.timeStamp]),true);"
        b"document.addEventListener('keyup',"
        b"e=>__u.push([e.key, performance.timeOrigin + performance.now(), performance.timeOrigin + e.timeStamp]),true);"
        b"window.__ev=[];for(const t of ['keydown','keyup'])document.addEventListener(t,"
        b"e=>__ev.push([t, e.key, performance.timeOrigin + performance.now(), performance.timeOrigin + e.timeStamp]),true);"
        b"</script></body></html>")


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


RUNS = int(os.environ.get("HUNT_RUNS", "5"))


def report(page, t_start, t_start_wall):
    """Read what the page saw and return the run's record (printed by emit(), for ours after its browser has ended)."""
    keys = page.evaluate("__k")
    ev = page.evaluate("__ev")
    # the content process hands over its in-memory stamps when asked (the listener the patched PageAgent installs after the first key)
    hs = page.evaluate("(() => { window.dispatchEvent(new Event('hunt-flush')); return window.__hs ? Array.from(window.__hs) : []; })()")         if STAMPS else []
    ups = page.evaluate("__u")
    planned, t = [], 0.0
    for dwell, gap in PLAN:
        planned.append(round(t))
        t += dwell + gap
    downs = [x for x in keys if x[0] != "Shift"]
    releases = [x for x in ups if x[0] != "Shift"]
    # clock index into a [key, handler clock, event clock] triple: 1 = "h" (handler), 2 = "e" (event.timeStamp)
    page_ms = [round(x[1] - downs[0][1]) for x in downs] if downs else []
    page_ms_e = [round(x[2] - downs[0][2]) for x in downs] if downs else []
    # a key's dwell: its keyup after its keydown, matched by order (the same key never overlaps itself in a plan)
    dwell_h, dwell_e, err_h, err_e = [], [], [], []
    if len(downs) == len(releases) == len(PLAN):
        for d, u, (planned_dwell, _gap) in zip(downs, releases, PLAN):
            dwell_h.append(round(u[1] - d[1], 1))
            dwell_e.append(round(u[2] - d[2], 1))
            err_h.append(round((u[1] - d[1]) - planned_dwell, 1))
            err_e.append(round((u[2] - d[2]) - planned_dwell, 1))
    plan_gaps = [planned[i + 1] - planned[i] for i in range(len(planned) - 1)]
    gaps_h = [page_ms[i + 1] - page_ms[i] for i in range(len(page_ms) - 1)]
    gaps_e = [page_ms_e[i + 1] - page_ms_e[i] for i in range(len(page_ms_e) - 1)]
    return {
        "engine": ENGINE, "arm": os.environ.get("HUNT_ARM", ENGINE), "planned_ms": planned,
        "sent_ms": [round(x * 1000 - SENT[0] * 1000) for x in SENT if True],
        "page_ms": page_ms, "page_ms_e": page_ms_e,
        "page_keys": [x[0] for x in keys], "send_offset_ms": round((SENT[0] - t_start) * 1000) if SENT else None,
        "keyup_ms": [round(x[1] - downs[0][1]) for x in releases] if downs else [],
        "keyup_ms_e": [round(x[2] - downs[0][2]) for x in releases] if downs else [],
        "planned_dwell_ms": [round(d, 1) for d, g in PLAN],
        "dwell_h_ms": dwell_h, "dwell_e_ms": dwell_e, "dwell_err_h_ms": err_h, "dwell_err_e_ms": err_e,
        "slip_last_ms": (page_ms[-1] - planned[-1]) if len(page_ms) == len(planned) else None,
        "slip_last_e_ms": (page_ms_e[-1] - planned[-1]) if len(page_ms_e) == len(planned) else None,
        "min_keydown_gap_h_ms": min(gaps_h) if gaps_h else None,
        "min_keydown_gap_e_ms": min(gaps_e) if gaps_e else None,
        "plan_min_keydown_gap_ms": min(plan_gaps) if plan_gaps else None,
        "all_keys_seen": len(downs) == len(PLAN) and len(releases) == len(PLAN),
        "sent_wall_ms": list(SENT_W), "ev": [[t, k, round(h, 3), round(e, 3)] for t, k, h, e in ev], "hs": hs, "hk": list(HK),
        "s1": [[t, d.decode("utf-8", "replace").rstrip("\x00")] for t, d in S1],
        "win": [round(t_start_wall, 3), round(wall_ms(), 3)], "stamps": STAMPS,
        "profile": CUR_PROFILE[0], "profiler": PROFILE, "pid": os.getpid(), "wr": OURSIDE[0], "wl": OURSIDE[1],
    }


def emit(rec):
    print("HUNT-TYPING " + json.dumps(rec), flush=True)


def emit_check(what, result, extra=None):
    out = dict(result, what=what)
    if extra:
        out["facts"] = dict(out.get("facts") or {}, **extra)
    print("PROFILE-CHECK " + json.dumps(out), flush=True)
    return out


def check_stock_profile(template, launch_wall, typing):
    """stock's browser ended inside b.close(); its profile (one process writes it: no pingsender, telemetry is off in Playwright's build) is the
    largest file of the template. Every file is listed in the facts so a second writer would show."""
    prev, calm = None, 0
    for _ in range(120):  # files complete: the set and the sizes unchanged for 3 reads
        now = [(pid, os.path.getsize(f)) for pid, f in hunt_profiler_env.files_of(template)]
        calm = calm + 1 if now == prev and now else 0
        prev = now
        if calm >= 3:
            break
        time.sleep(1.0)
    files = hunt_profiler_env.files_of(template)
    if not files:
        emit_check("stock " + os.path.basename(template), {"ok": False, "problems": ["no profile file for the template"], "facts": {}})
        return
    pid, path = max(files, key=lambda x: os.path.getsize(x[1]))
    res = hunt_profile_check.check_profile(path, launch_ms=launch_wall, typing=typing)
    emit_check("stock " + os.path.basename(path), res, {"files": [[p_, os.path.getsize(f_)] for p_, f_ in files], "chosen_pid": pid})


def finish_ours_profile(template, launch_wall, win):
    """after the with-block: wait for the parent pid to end, then check its profile; returns the fields to put in the record"""
    pid, path, problems, waited = hunt_profiler_env.wait_for_parent(HK, template)
    time.sleep(0.5)  # the browser has ended and written its stamps once; the last lines are still on the pipe
    anchor = hunt_profiler_env.anchor_of(HK, pid) if pid else None
    if path:
        res = hunt_profile_check.check_profile(path, parent_pid=pid, anchor_ms=anchor, launch_ms=launch_wall, typing=win)
        res["problems"] = problems + res["problems"]
        res["ok"] = res["ok"] and not problems
    else:
        res = {"ok": False, "problems": problems, "facts": {}}
    others = [(p_, os.path.getsize(f_)) for p_, f_ in hunt_profiler_env.files_of(template) if p_ != pid]
    name = hunt_profiler_env.concrete(template, pid) if pid else template
    res = emit_check("ours " + os.path.basename(name), res,
                     {"waited_s": round(waited, 1), "parent_pid": pid, "anchor_ms": anchor, "other_writers": others})
    return {"parent_pid": pid, "profile_file": path, "profile_check": res}


if ENGINE == "stock":
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["-purgecaches"]}
        if STAMPS:
            kw["firefox_user_prefs"] = DUMP_PREF
        if PROFILE:
            CUR_PROFILE[0] = hunt_profiler_env.profile_path(ARM, 0)
            kw["env"] = dict(os.environ, **hunt_profiler_env.profiler_env(CUR_PROFILE[0]))
        launch_wall = wall_ms()
        b = p.firefox.launch(**kw)
        page = b.new_page()
        first_start_wall = last_end_wall = None
        for _ in range(RUNS):
            SENT.clear()
            SENT_W.clear()
            page.goto(url)
            page.click("#f")
            t_start = time.time()
            t_start_wall = wall_ms()
            for (dwell, gap), ch in zip(PLAN, TEXT):
                mark()
                if ch == "@":
                    page.keyboard.down("Shift")
                page.keyboard.down(ch)
                time.sleep(dwell / 1000)
                page.keyboard.up(ch)
                if ch == "@":
                    page.keyboard.up("Shift")
                time.sleep(gap / 1000)
            if first_start_wall is None:
                first_start_wall = t_start_wall
            rec = report(page, t_start, t_start_wall)
            last_end_wall = rec["win"][1]
            emit(rec)
        b.close()
        if PROFILE:
            check_stock_profile(CUR_PROFILE[0], launch_wall, (first_start_wall, last_end_wall))
else:
    from invisible_core.juggler import keyboard as kb
    real_down = kb.Keyboard.down

    def down(self, key, *a, **k):
        if key != "Shift":
            mark()
            if STAMPS:
                hunt_ourside.take_s0(SENT_W[-1])
        return real_down(self, key, *a, **k)

    kb.Keyboard.down = down
    if STAMPS:
        # S1: os.write returned in the core's Connection._write (after the write, nothing else done there: a clock read, then the message
        # is kept raw in memory; the JSON is read only at the end). The module's `os` is replaced by a stand-in that forwards everything
        # and stamps the one write of a Page.dispatchKeyEvent message.
        import os as _os
        from invisible_core.juggler import connection as _conn

        class _OsStamp:
            def __getattr__(self, name):
                return getattr(_os, name)

            @staticmethod
            def write(fd, data):
                if b"Page.dispatchKeyEvent" in data:
                    n, t = hunt_ourside.os_timing(_os, fd, data)
                else:
                    n, t = _os.write(fd, data), wall_ms()
                if b"Page.dispatchKeyEvent" in data:
                    S1.append((round(t, 3), bytes(data)))
                return n

        _conn.os = _OsStamp()
        hunt_ourside.install(wall_ms)
        if PROFILE:
            hunt_profiler_env.install_core_patch(lambda: CUR_PROFILE[0])
    if STAMPS:
        # the browser's output is read by the core's one reader (ProcessOutput); it keeps the last 200 lines only, so the HUNTKEY ones are
        # also copied out as they arrive
        from invisible_core.juggler import connection as conn

        def pump(self):
            try:
                for raw in iter(self._stream.readline, b""):
                    text = raw.decode("utf-8", "replace").rstrip("\r\n")
                    i = text.find("HUNTKEY ")
                    if i >= 0:
                        HK.append(text[i + 8:])
                    with self._cv:
                        if text.strip():
                            self._lines.append(text)
                        if conn._READY in text:
                            self._ready = True
                        self._cv.notify_all()
            except (OSError, ValueError):
                pass
            finally:
                with self._cv:
                    self._eof = True
                    self._cv.notify_all()

        conn.ProcessOutput._pump = pump
    from invisible_playwright import InvisiblePlaywright
    for run_n in range(RUNS):
        if PROFILE:
            CUR_PROFILE[0] = hunt_profiler_env.profile_path(ARM, run_n)
        # a fresh browser per run: the plan is the one of the session's first field (typing nonce 1)
        kw = {"seed": 2, "binary_path": sys.argv[2], "headless": True, "extra_args": ["-purgecaches"]}
        if STAMPS:
            kw["extra_prefs"] = DUMP_PREF
        launch_wall = wall_ms()
        with InvisiblePlaywright(**kw) as browser:
            SENT.clear()
            SENT_W.clear()
            S1.clear()
            HK.clear()
            hunt_ourside.reset()
            page = browser.new_page()
            page.goto(url)
            t_start = time.time()
            t_start_wall = wall_ms()
            page.locator("#f").press_sequentially(TEXT)
            if STAMPS:
                OURSIDE[0][:], OURSIDE[1][:] = hunt_ourside.snapshot()
            rec = report(page, t_start, t_start_wall)
        if STAMPS and PROFILE:
            rec.update(finish_ours_profile(CUR_PROFILE[0], launch_wall, tuple(rec["win"])))
            rec["hk"] = list(HK)
        elif STAMPS:
            time.sleep(0.5)  # the browser has ended and written its stamps once; the last lines are still on the pipe
            rec["hk"] = list(HK)
        emit(rec)
srv.shutdown()
