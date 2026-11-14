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

PIPE-BUFFER hunt additions (Windows): ours runs in two arms that differ in one thing, the size of the two Juggler pipes the core makes in
`_spawn_windows`. HUNT_BIGPIPE=1 (arm ours_bigpipe): the first two `_winapi.CreatePipe` calls inside `_spawn_windows` (the browser's two protocol
pipes; the third is stdout, the fourth stdin) are made with `_winapi.CreatePipe(None, 1 << 20)` instead of `CreatePipe(0, 0)`. Nothing else of the spawn
is touched: the core still duplicates the browser's ends with inheritance, names them in the handle list, and wraps ITS ends into CRT fds with
msvcrt.open_osfhandle. HUNT_BIGPIPE=0 (arm ours_normal): the core as it is. At every launch the pipe sizes in effect are printed ("HUNT-PIPE" line and
the run's "pipe" field): GetNamedPipeInfo on the two handles the core keeps, and, once per process, an empirical probe of how many bytes a pipe
made with the same size takes before a writer blocks. Stock has no such pipes (its node driver makes them). S1 also keeps a "before os.write"
clock (third item of each "s1" entry), so a slow hop can be told from a slow write.
"""
import os
import http.server
import json
import sys
import threading
import time

from invisible_core.juggler._behaviour import TypingPersona, plan_typing

ENGINE = sys.argv[1]
STAMPS = os.environ.get("HUNT_STAMPS") == "1"
BIGPIPE = os.environ.get("HUNT_BIGPIPE") == "1"
if os.environ.get("HUNT_SWITCH"):
    # ours_switch: the GIL handed between threads every HUNT_SWITCH seconds (default 0.005); a diagnosis, not a fix
    sys.setswitchinterval(float(os.environ["HUNT_SWITCH"]))
    print("HUNT-SWITCH %s" % sys.getswitchinterval(), flush=True)
BIG = 1 << 20
DUMP_PREF = {"browser.dom.window.dump.enabled": True}
TEXT = "someone@example.com"
PLAN = plan_typing(TEXT, TypingPersona.from_seed(2), nonce=1)
SENT = []
SENT_W = []
S1 = []
HK = []
PIPE = {}


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
        "s1": [[t, d.decode("utf-8", "replace").rstrip("\x00"), pre] for t, d, pre in S1],
        "pipe": dict(PIPE),
        "win": [round(t_start_wall, 3), round(wall_ms(), 3)], "stamps": STAMPS,
    }


def emit(rec):
    print("HUNT-TYPING " + json.dumps(rec), flush=True)


if ENGINE == "stock":
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["-purgecaches"]}
        if STAMPS:
            kw["firefox_user_prefs"] = DUMP_PREF
        b = p.firefox.launch(**kw)
        page = b.new_page()
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
            emit(report(page, t_start, t_start_wall))
        b.close()
else:
    from invisible_core.juggler import keyboard as kb
    real_down = kb.Keyboard.down

    def down(self, key, *a, **k):
        if key != "Shift":
            mark()
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
                pre = wall_ms()
                n = _os.write(fd, data)
                t = wall_ms()
                if b"Page.dispatchKeyEvent" in data:
                    S1.append((round(t, 3), bytes(data), round(pre, 3)))
                return n

        _conn.os = _OsStamp()
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
    # ---- the pipes of the browser's protocol: sizes in effect, and in the bigpipe arm the size asked for ---------------------------------
    PIPE_DIAG = {"arm": os.environ.get("HUNT_ARM", ENGINE), "bigpipe": BIGPIPE}
    if sys.platform != "win32":
        if BIGPIPE:
            print("HUNT-PIPE-FATAL the bigpipe arm is Windows only", flush=True)
            sys.exit(3)
        PIPE_DIAG["note"] = "not Windows: the pipes are os.pipe, not measured"
    else:
        import ctypes
        import msvcrt
        import _winapi
        from ctypes import wintypes
        from invisible_core.juggler import connection as _pc

        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _k32.GetNamedPipeInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                          ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD))
        _k32.GetNamedPipeInfo.restype = wintypes.BOOL

        def pipe_info(fd):
            """GetNamedPipeInfo on the handle behind a CRT fd: {flags, out, in, max} (bytes), or {"error": ...}."""
            try:
                h = msvcrt.get_osfhandle(fd)
                flags, out, inn, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                if not _k32.GetNamedPipeInfo(h, ctypes.byref(flags), ctypes.byref(out), ctypes.byref(inn), ctypes.byref(mx)):
                    return {"error": "GetNamedPipeInfo failed, winerror %d" % ctypes.get_last_error()}
                return {"flags": flags.value, "out": out.value, "in": inn.value, "max": mx.value}
            except Exception as exc:  # a measurement must never stop the hunt
                return {"error": repr(exc)}

        def probe_capacity(size):
            """How many bytes a pipe made by CreatePipe(None, size) takes before a writer blocks: a thread writes 512-byte chunks into a
            pipe nobody reads; what it completed when it has made no progress for 0.3 s is the capacity (to 512 bytes). Closed afterwards."""
            r, w = _winapi.CreatePipe(None, size)
            done = [0]

            def writer():
                chunk = b"x" * 512
                try:
                    while done[0] < (64 << 20):
                        _winapi.WriteFile(w, chunk)
                        done[0] += 512
                except OSError:
                    pass

            t = threading.Thread(target=writer, daemon=True)
            t.start()
            last, still = -1, 0.0
            t0 = time.perf_counter()
            while still < 0.3 and time.perf_counter() - t0 < 20:
                time.sleep(0.05)
                if done[0] == last:
                    still += 0.05
                else:
                    last, still = done[0], 0.0
            got = done[0]
            _winapi.CloseHandle(r)  # the blocked write fails and the thread ends
            t.join(2)
            _winapi.CloseHandle(w)
            return got

        PIPE_DIAG["asked"] = BIG if BIGPIPE else 0
        PIPE_DIAG["probe_bytes"] = probe_capacity(BIG if BIGPIPE else 0)
        _real_create_pipe = _winapi.CreatePipe
        _tl = threading.local()

        def _create_pipe(attrs, size):
            n = getattr(_tl, "n", None)
            if n is None:  # not inside the core's spawn: untouched
                return _real_create_pipe(attrs, size)
            _tl.n = n + 1
            if BIGPIPE and n < 2:  # the core's first two pipes are the two protocol directions
                return _real_create_pipe(None, BIG)
            return _real_create_pipe(attrs, size)

        _winapi.CreatePipe = _create_pipe
        _real_spawn = _pc._spawn_windows

        def _spawn(executable, argv, env):
            _tl.n = 0
            try:
                res = _real_spawn(executable, argv, env)
            finally:
                calls, _tl.n = _tl.n, None
            to_browser, from_browser, _proc = res
            PIPE.clear()
            PIPE.update({"arm": PIPE_DIAG["arm"], "bigpipe": BIGPIPE, "createpipe_calls": calls, "asked": PIPE_DIAG["asked"],
                         "probe_bytes": PIPE_DIAG["probe_bytes"], "to_browser": pipe_info(to_browser), "from_browser": pipe_info(from_browser)})
            print("HUNT-PIPE " + json.dumps(PIPE), flush=True)
            if calls != 4:
                print("HUNT-PIPE-FATAL the core's _spawn_windows made %d CreatePipe calls, expected 4: the patch no longer fits the core" % calls,
                      flush=True)
                os._exit(3)
            return res

        _pc._spawn_windows = _spawn
        print("HUNT-PIPE-PATCH " + json.dumps(PIPE_DIAG), flush=True)
    from invisible_playwright import InvisiblePlaywright
    for _ in range(RUNS):
        # a fresh browser per run: the plan is the one of the session's first field (typing nonce 1)
        kw = {"seed": 2, "binary_path": sys.argv[2], "headless": True, "extra_args": ["-purgecaches"]}
        if STAMPS:
            kw["extra_prefs"] = DUMP_PREF
        with InvisiblePlaywright(**kw) as browser:
            SENT.clear()
            SENT_W.clear()
            S1.clear()
            HK.clear()
            page = browser.new_page()
            page.goto(url)
            t_start = time.time()
            t_start_wall = wall_ms()
            page.locator("#f").press_sequentially(TEXT)
            rec = report(page, t_start, t_start_wall)
        if STAMPS:
            time.sleep(0.5)  # the browser has ended and written its stamps once; the last lines are still on the pipe
            rec["hk"] = list(HK)
        emit(rec)
srv.shutdown()
