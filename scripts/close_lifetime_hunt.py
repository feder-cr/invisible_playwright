#!/usr/bin/env python3
"""Close-lifetime hunt: after close() returns, is the real browser still alive, and for how long?

THE QUESTION (measure only, no fix). Playwright's `browser.close()` returns after the browser has
exited. Here `Connection.close` (invisible_core/juggler/connection.py) closes the pipe and waits on the
pid that the spawn returned. On Windows that pid is Firefox's launcher process, which exits about a
second after the start unless the command line carries `-wait-for-browser` (the core adds it only when
the session is not TRUE headless). This script measures what a caller can observe: from the moment
close() (or the end of the `with` block) returns, how long do the browser's processes, and its profile
lock, stay around.

HOW A LAUNCH'S PROCESSES ARE FOUND. Every process of the launch carries one environment variable with a
token the script knows: the wrapper's own `INVPW_SESSION_TOKEN` (`InvisiblePlaywright._session_token`)
for the invisible arms, `HUNT_TOKEN` (passed through `launch(env=)`) for stock Playwright. A scanner
thread looks, every ~100 ms, at the processes whose executable lives in the engine's folder and keeps
those whose environment holds the token (psutil `environ()`), so the shutdown-ping sender that Firefox
starts while it exits is seen as well. Each member is identified by (pid, create_time): a reused pid is
a different process. A zombie is not alive.

THE REAL PARENT is the newest member that has no `-contentproc` in its command line and has `-profile`
(the launcher stub, when it is still alive, is older: it creates the browser). `how` in the result says
which rule picked it. The pid the core's spawn returned (the "Popen pid") is recorded by wrapping
`_spawn_windows` / `_spawn_posix` in `invisible_core.juggler.connection`, so the result says whether
the two are the same process and whether the Popen pid was still alive when close() returned.

THE CLOCK starts when close() returns (or the `with` block is left) and runs `POLL_S` (10 ms) apart
until every member is gone (confirmed by two further scans), at most `CAP_S` (60 s). Per tick:
parent alive, members alive, profile lock present (Windows: `parent.lock` exists, it is opened with
delete-on-close; Linux: `lock` symlink exists or `.parentlock` is held by another process, probed
with a non-blocking fcntl lock), Popen pid alive.

Subcommands: `one` (a single measurement, run by `cell`), `cell` (rounds of N workers at once),
`summary`, `meta`, `selftest` (fake process tree, no browser).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import platform
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

import psutil

IS_WIN = sys.platform == "win32"
CAP_S = 60.0
POLL_S = 0.010
SCAN_S = 0.100
# After the last member is gone the lock gets this long to go as well before it is called stale.
LOCK_GRACE_S = 2.0
RESULT_PREFIX = "RESULT "


def now() -> float:
    return time.perf_counter()


# --------------------------------------------------------------------------- processes

def alive(proc: psutil.Process) -> bool:
    """False when gone OR a zombie. A process that cannot be asked counts as alive if its pid exists."""
    try:
        if not proc.is_running():
            return False
        if not IS_WIN and proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return psutil.pid_exists(proc.pid)


class Member:
    def __init__(self, proc: psutil.Process, key, cmd: list, seen_at: float) -> None:
        self.proc = proc
        self.key = key
        self.pid = proc.pid
        self.cmd = cmd
        self.seen_at = seen_at
        self.is_content = "-contentproc" in cmd
        try:
            self.name = proc.name()
        except psutil.Error:
            self.name = "?"
        self.is_candidate_parent = (not self.is_content and "-profile" in cmd
                                    and "pingsender" not in self.name.lower())

    def brief(self) -> dict:
        return {"pid": self.pid, "name": self.name, "content": self.is_content,
                "tail": (self.cmd[-1] if self.cmd else "")[:60]}


class Tracker:
    """The processes of ONE launch: engine folder + token in the environment."""

    def __init__(self, token_var: str, token: str, engine_dir: str) -> None:
        self.token_var = token_var
        self.token = token
        self.engine_dir = os.path.normcase(os.path.abspath(engine_dir)).rstrip("\\/") + os.sep
        self.known: dict = {}
        self.pre_keys: set = set()      # the members that existed before close() was called
        self.rejected: set = set()
        self.lock = threading.Lock()
        self.scans = 0
        self.scan_max_s = 0.0
        self._stop = threading.Event()
        self._thread = None

    def scan(self) -> None:
        t0 = now()
        for p in psutil.process_iter(["pid", "exe", "create_time"]):
            exe = p.info.get("exe")
            if not exe or not os.path.normcase(exe).startswith(self.engine_dir):
                continue
            key = (p.pid, p.info.get("create_time"))
            if key in self.known or key in self.rejected:
                continue
            try:
                env = p.environ()
            except psutil.Error:
                continue  # may be exiting: asked again at the next scan
            if env.get(self.token_var) != self.token:
                self.rejected.add(key)
                continue
            try:
                cmd = p.cmdline()
            except psutil.Error:
                cmd = []
            member = Member(p, key, cmd, now())
            with self.lock:
                self.known[key] = member
        took = now() - t0
        self.scan_max_s = max(self.scan_max_s, took)
        self.scans += 1

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.scan()
                except Exception:
                    pass
                self._stop.wait(SCAN_S)
        self._thread = threading.Thread(target=loop, name="tracker-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)

    def members(self) -> list:
        with self.lock:
            return list(self.known.values())

    def kill_all(self) -> int:
        """Cleanup after a measurement: whatever is still alive and carries the token."""
        try:
            self.scan()
        except Exception:
            pass
        left = [m.proc for m in self.members() if alive(m.proc)]
        for p in left:
            try:
                p.kill()
            except psutil.Error:
                pass
        if left:
            psutil.wait_procs(left, timeout=5)
        return len(left)


def pick_parent(members: list):
    """The newest candidate: the launcher stub, if it is still alive, is older than the browser it creates."""
    cands = [m for m in members if m.is_candidate_parent]
    if not cands:
        return None, "no member with -profile and without -contentproc"
    cands.sort(key=lambda m: m.key[1] or 0)
    how = "newest of %d candidate(s) with -profile and without -contentproc" % len(cands)
    return cands[-1], how


def profile_of(member) -> str:
    cmd = member.cmd if member else []
    for i, a in enumerate(cmd):
        if a in ("-profile", "--profile") and i + 1 < len(cmd):
            return cmd[i + 1]
    return ""


def lock_present(profile: str):
    """None when there is no profile to ask about."""
    if not profile:
        return None
    if IS_WIN:
        return os.path.exists(os.path.join(profile, "parent.lock"))
    if os.path.lexists(os.path.join(profile, "lock")):
        return True
    try:
        fd = os.open(os.path.join(profile, ".parentlock"), os.O_RDWR)
    except OSError:
        return False
    try:
        import fcntl
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.lockf(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- the measurement

def poll_until_gone(tr: Tracker, parent, profile: str, popen, t_ref: float) -> dict:
    """From `t_ref` (close() returned) every POLL_S until the members are gone, at most CAP_S."""
    out = {"timeline": []}
    t_parent = t_popen = t_lock = t_zero = None
    zero_scans = None
    stale_since = None
    last = None
    ticks = 0
    max_gap = 0.0
    prev = 0.0
    peak = 0
    first = None
    capped = False
    k = 0
    while True:
        t = now() - t_ref
        ticks += 1
        max_gap = max(max_gap, t - prev)
        prev = t
        members = tr.members()
        n = sum(1 for m in members if alive(m.proc))
        pa = alive(parent.proc) if parent is not None else None
        pp = (popen.poll() is None) if popen is not None else None
        lk = lock_present(profile)
        peak = max(peak, n)
        state = (n, pa, lk, pp)
        if first is None:
            first = {"t": round(t, 4), "alive": n, "parent_alive": pa, "lock": lk, "popen_alive": pp,
                     "known": len(members)}
        if state != last:
            out["timeline"].append([round(t, 3), n, pa, lk, pp])
            last = state
        if pa is False and t_parent is None:
            t_parent = t
        if pp is False and t_popen is None:
            t_popen = t
        if lk is False and t_lock is None:
            t_lock = t
        if n == 0:
            if t_zero is None:
                t_zero = t
                zero_scans = tr.scans
            if tr.scans >= zero_scans + 2:
                # confirmed by two scans that started after the last member went
                if lk is not True:
                    break
                if stale_since is None:
                    stale_since = t
                elif t - stale_since >= LOCK_GRACE_S:
                    break
        else:
            t_zero = zero_scans = stale_since = None
        if t >= CAP_S:
            capped = True
            break
        k += 1
        time.sleep(max(0.0, (t_ref + k * POLL_S) - now()))
    members = tr.members()
    survivors = [m.brief() for m in members if alive(m.proc)] if capped else []
    out.update({
        "at_return": first,
        "t_parent_gone": t_parent,
        "t_popen_gone": t_popen,
        "t_procs_gone": None if capped else t_zero,
        "t_lock_gone": t_lock,
        "lock_stale_after_procs": bool(not capped and t_lock is None and lock_present(profile) is True),
        "capped": capped,
        "peak_alive": peak,
        "members_total": len(members),
        "late_members": [m.brief() for m in members if m.key not in tr.pre_keys],
        "survivors_at_cap": survivors,
        "poll_ticks": ticks,
        "poll_max_gap_ms": round(max_gap * 1000, 1),
        "scan_max_s": round(tr.scan_max_s, 3),
    })
    return out


def serve():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><title>close-lifetime</title><p>hello</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/" % server.server_address[1]


def wait_barrier(a, res) -> None:
    """With several workers at once: say ready, wait for the orchestrator's go (all browsers up first)."""
    if not a.barrier:
        return
    d = pathlib.Path(a.barrier)
    (d / ("ready_%d" % a.index)).write_text("1")
    end = time.monotonic() + 240
    while not (d / "go").exists():
        if time.monotonic() > end:
            res["barrier_timeout"] = True
            return
        time.sleep(0.005)


def install_spy() -> dict:
    """Record what the core's spawn returned: the Popen pid and the process object."""
    spy: dict = {}
    try:
        from invisible_core.juggler import connection as C
    except Exception as exc:
        spy["error"] = repr(exc)
        return spy
    name = "_spawn_windows" if IS_WIN else "_spawn_posix"
    original = getattr(C, name)

    def wrapped(executable, argv, env):
        r = original(executable, argv, env)
        spy["proc"] = r[2]
        spy["pid"] = r[2].pid
        spy["argv"] = [a for a in argv if a.startswith("-") and a != "-profile"]
        return r

    setattr(C, name, wrapped)
    return spy


def after(res: dict, tr: Tracker, popen, t1: float, profile: str, parent, how: str) -> None:
    res["parent"] = parent.brief() if parent else None
    res["parent_how"] = how
    res.update(poll_until_gone(tr, parent, profile, popen, t1))


def run_inv(a, res: dict) -> None:
    from invisible_playwright import InvisiblePlaywright
    truehl = a.arm == "inv-truehl"
    if truehl:
        os.environ["INVPW_TRUE_HEADLESS"] = "1"
    spy = install_spy()
    server, url = serve()
    engine_dir = os.path.dirname(a.binary)
    ip = InvisiblePlaywright(seed=a.seed, headless=truehl, humanize=False, binary_path=a.binary)
    res["engine"] = a.binary
    tr = None
    t_launch = now()

    def prepared(browser):
        nonlocal tr
        res["launch_s"] = round(now() - t_launch, 3)
        page = browser.new_page()
        page.goto(url, wait_until="load")
        tr = Tracker("INVPW_SESSION_TOKEN", ip._session_token.value, engine_dir)
        tr.scan()
        tr.pre_keys = set(tr.known)
        res["members_before_close"] = [m.brief() for m in tr.members()]
        res["popen_pid"] = spy.get("pid")
        res["core_argv"] = spy.get("argv")
        tr.start()
        return page

    try:
        if a.variant == "close":
            browser = ip.__enter__()
            try:
                prepared(browser)
                parent, how = pick_parent(tr.members())
                profile = profile_of(parent)
                popen = spy.get("proc")
                res["popen_is_parent"] = bool(parent and spy.get("pid") == parent.pid)
                res["popen_alive_before_close"] = (popen.poll() is None) if popen else None
                wait_barrier(a, res)
                t0 = now()
                browser.close()
                t1 = now()
                res["close_call_s"] = round(t1 - t0, 4)
                after(res, tr, popen, t1, profile, parent, how)
            finally:
                t2 = now()
                ip.__exit__(None, None, None)
                res["exit_after_s"] = round(now() - t2, 3)
        else:
            parent = popen = None
            how = ""
            profile = ""
            with ip as browser:
                prepared(browser)
                parent, how = pick_parent(tr.members())
                profile = profile_of(parent)
                popen = spy.get("proc")
                res["popen_is_parent"] = bool(parent and spy.get("pid") == parent.pid)
                res["popen_alive_before_close"] = (popen.poll() is None) if popen else None
                wait_barrier(a, res)
                t0 = now()
            t1 = now()
            res["close_call_s"] = round(t1 - t0, 4)
            after(res, tr, popen, t1, profile, parent, how)
    finally:
        if tr is not None:
            res["killed_at_end"] = tr.kill_all()
            tr.stop()
        server.shutdown()


def run_stock(a, res: dict) -> None:
    from playwright.sync_api import sync_playwright
    token = uuid.uuid4().hex
    env = dict(os.environ, HUNT_TOKEN=token)
    server, url = serve()
    tr = None

    def prepared(p):
        nonlocal tr
        exe = p.firefox.executable_path
        res["engine"] = exe
        t_launch = now()
        browser = p.firefox.launch(headless=True, env=env)
        res["launch_s"] = round(now() - t_launch, 3)
        page = browser.new_page()
        page.goto(url, wait_until="load")
        tr = Tracker("HUNT_TOKEN", token, os.path.dirname(exe))
        tr.scan()
        tr.pre_keys = set(tr.known)
        res["members_before_close"] = [m.brief() for m in tr.members()]
        tr.start()
        return browser

    try:
        if a.variant == "close":
            p = sync_playwright().start()
            try:
                browser = prepared(p)
                parent, how = pick_parent(tr.members())
                profile = profile_of(parent)
                wait_barrier(a, res)
                t0 = now()
                browser.close()
                t1 = now()
                res["close_call_s"] = round(t1 - t0, 4)
                after(res, tr, None, t1, profile, parent, how)
            finally:
                t2 = now()
                p.stop()
                res["exit_after_s"] = round(now() - t2, 3)
        else:
            with sync_playwright() as p:
                prepared(p)
                parent, how = pick_parent(tr.members())
                profile = profile_of(parent)
                wait_barrier(a, res)
                t0 = now()
            t1 = now()
            res["close_call_s"] = round(t1 - t0, 4)
            after(res, tr, None, t1, profile, parent, how)
    finally:
        if tr is not None:
            res["killed_at_end"] = tr.kill_all()
            tr.stop()
        server.shutdown()


def cmd_one(a) -> int:
    res = {"os": a.os_label, "arm": a.arm, "variant": a.variant, "round": a.round, "index": a.index,
           "parallel": a.parallel, "seed": a.seed, "worker_pid": os.getpid()}
    try:
        (run_stock if a.arm == "stock" else run_inv)(a, res)
    except BaseException as exc:  # a measurement that failed is a row, not a crash
        res["error"] = "%s: %s" % (type(exc).__name__, exc)
        res["traceback"] = traceback.format_exc()[-1500:]
    print(RESULT_PREFIX + json.dumps(res), flush=True)
    return 0


# --------------------------------------------------------------------------- orchestration

def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=5)


def cmd_cell(a) -> int:
    out = pathlib.Path(a.out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    name = "%s__%s__%s__p%d" % (a.os_label, a.arm, a.variant, a.parallel)
    rows = out / (name + ".jsonl")
    script = os.path.abspath(__file__)
    for rnd in range(a.rounds):
        barrier = None
        if a.parallel > 1:
            barrier = tempfile.mkdtemp(prefix="barrier_")
        workers = []
        for i in range(a.parallel):
            seed = a.seed_base + rnd * a.parallel + i
            log = open(out / "logs" / ("%s__r%02d_w%d.log" % (name, rnd, i)), "w+", encoding="utf-8")
            cmd = [sys.executable, "-u", script, "one", "--arm", a.arm, "--variant", a.variant,
                   "--binary", a.binary or "", "--os-label", a.os_label, "--round", str(rnd),
                   "--index", str(i), "--parallel", str(a.parallel), "--seed", str(seed)]
            if barrier:
                cmd += ["--barrier", barrier]
            workers.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, i))
        if barrier:
            # go once every worker has its browser up and a page open (or has already died)
            end = time.monotonic() + 300
            while time.monotonic() < end:
                ready = len([f for f in os.listdir(barrier) if f.startswith("ready_")])
                dead = len([1 for w, _, _ in workers if w.poll() is not None])
                if ready + dead >= a.parallel:
                    break
                time.sleep(0.05)
            pathlib.Path(barrier, "go").write_text("1")
        end = time.monotonic() + 300 + CAP_S + 120
        for w, log, i in workers:
            try:
                w.wait(timeout=max(1, end - time.monotonic()))
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_tree(w.pid)
            log.flush()
            log.seek(0)
            text = log.read()
            log.close()
            row = None
            for line in text.splitlines():
                if line.startswith(RESULT_PREFIX):
                    row = json.loads(line[len(RESULT_PREFIX):])
            if row is None:
                row = {"os": a.os_label, "arm": a.arm, "variant": a.variant, "round": rnd, "index": i,
                       "parallel": a.parallel, "error": "no result (worker %s)" % (
                           "timed out" if timed_out else "exited %s" % w.returncode)}
            elif timed_out:
                row["error"] = "worker timed out"
            with open(rows, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        brief = []
        for line in open(rows, encoding="utf-8").read().splitlines()[-a.parallel:]:
            r = json.loads(line)
            brief.append("err" if r.get("error") else ("CAP" if r.get("capped") else
                         "%.2fs" % (r.get("t_procs_gone") or 0)))
        print("%s round %d/%d: %s" % (name, rnd + 1, a.rounds, " ".join(brief)), flush=True)
        if barrier:
            import shutil
            shutil.rmtree(barrier, ignore_errors=True)
    return 0


# --------------------------------------------------------------------------- summary

def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def fmt(v, capped=False):
    if v is None:
        return "-"
    return (">=" if capped else "") + ("%.3f" % v)


def summarize(directory: str) -> str:
    groups: dict = {}
    for f in sorted(pathlib.Path(directory).glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            key = (r.get("os"), r.get("arm"), r.get("variant"), "x%d" % r.get("parallel", 1))
            groups.setdefault(key, []).append(r)
    lines = ["## Alive after close() returned (seconds from the return; capped runs count as 60 s = lower bound)", "",
             "| OS | arm | variant | at once | runs ok | err | procs alive at the return | still alive at 60 s | "
             "all procs gone p50 / p95 / max | parent gone p50 / p95 / max | lock gone p50 / p95 / max | "
             "popen pid is parent | popen pid alive at return | close() call p50 / p95 | exit after p50 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(groups, key=lambda k: tuple(str(x) for x in k)):
        rows = groups[key]
        ok = [r for r in rows if not r.get("error") and "t_parent_gone" in r]
        err = len(rows) - len(ok)
        capped = [r for r in ok if r.get("capped")]
        at_ret = [r for r in ok if (r.get("at_return") or {}).get("alive", 0) > 0]

        def series(field):
            vals = []
            for r in ok:
                v = r.get(field)
                vals.append(CAP_S if (v is None and r.get("capped")) else v)
            return [v for v in vals if v is not None]

        def trio(field):
            xs = series(field)
            hit = bool(capped) and any(x >= CAP_S for x in xs)
            return "%s / %s / %s" % (fmt(pct(xs, .5)), fmt(pct(xs, .95)), fmt(max(xs) if xs else None, hit))

        calls = [r["close_call_s"] for r in ok if "close_call_s" in r]
        exits = [r["exit_after_s"] for r in ok if "exit_after_s" in r]
        same = [r["popen_is_parent"] for r in ok if "popen_is_parent" in r]
        popen_alive = [r["popen_alive_before_close"] for r in ok if "popen_is_parent" in r]
        popen_at_ret = [(r.get("at_return") or {}).get("popen_alive") for r in ok if "popen_is_parent" in r]
        lines.append("| %s | %s | %s | %s | %d | %d | %d / %d | %d / %d | %s | %s | %s | %s | %s | %s / %s | %s |" % (
            key[0], key[1], key[2], key[3], len(ok), err, len(at_ret), len(ok), len(capped), len(ok),
            trio("t_procs_gone"), trio("t_parent_gone"), trio("t_lock_gone"),
            ("%d/%d" % (sum(1 for s in same if s), len(same))) if same else "-",
            ("%d/%d" % (sum(1 for s in popen_at_ret if s), len(popen_at_ret))) if popen_at_ret else "-",
            fmt(pct(calls, .5)), fmt(pct(calls, .95)), fmt(pct(exits, .5))))
    stale = sum(1 for rs in groups.values() for r in rs if r.get("lock_stale_after_procs"))
    lines += ["", "Runs whose profile lock was still there 2 s after the last process went: %d." % stale]
    late = {}
    for rs in groups.values():
        for r in rs:
            for m in r.get("late_members") or []:
                late[m["name"]] = late.get(m["name"], 0) + 1
    if late:
        lines.append("Processes that appeared only after the close started (name: count): %s." % ", ".join(
            "%s: %d" % kv for kv in sorted(late.items())))
    errs = [(k, r.get("error")) for k, rs in groups.items() for r in rs if r.get("error")]
    if errs:
        lines += ["", "### Errors (first 10)"] + ["- %s: %s" % (k, e) for k, e in errs[:10]]
    return "\n".join(lines) + "\n"


def cmd_summary(a) -> int:
    text = summarize(a.dir)
    sys.stdout.write(text)
    (pathlib.Path(a.dir) / "summary.md").write_text(text, encoding="utf-8")
    return 0


def cmd_meta(a) -> int:
    from importlib import metadata

    def ver(name):
        try:
            return metadata.version(name)
        except Exception:
            return None

    meta = {"os_label": a.os_label, "platform": platform.platform(), "python": sys.version,
            "cpu_count": os.cpu_count(), "invisible_playwright": ver("invisible-playwright"),
            "invisible_core": ver("invisible-core"), "playwright": ver("playwright"),
            "engine": a.binary, "engine_application_ini": None,
            "command": " ".join(sys.argv), "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "github_sha": os.environ.get("GITHUB_SHA"), "runner_image": os.environ.get("ImageVersion")}
    if a.binary:
        ini = pathlib.Path(a.binary).parent / "application.ini"
        try:
            meta["engine_application_ini"] = [l for l in ini.read_text(errors="replace").splitlines()
                                              if l.startswith(("Version=", "BuildID="))]
        except OSError:
            pass
    pathlib.Path(a.out).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(a.out) / ("meta_%s.json" % a.os_label)).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


# --------------------------------------------------------------------------- selftest

FAKE_PARENT = r"""
import os, subprocess, sys, time
profile = sys.argv[sys.argv.index('-profile') + 1]
lockname = 'parent.lock' if os.name == 'nt' else 'lock'
lockpath = os.path.join(profile, lockname)
if os.name == 'nt':
    open(lockpath, 'w').close()
else:
    os.symlink('fake:+1', lockpath)
kids = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(%s)' % os.environ['KID_S'],
                          '-contentproc', 'tab']) for _ in range(2)]
sys.stdin.read()          # the close: stdin EOF
time.sleep(float(os.environ['PARENT_S']))
os.unlink(lockpath)
sys.exit(0)
"""


def cmd_selftest(a) -> int:
    """A fake tree (a parent that exits 0.5 s after its stdin closes, children that stay 1.5 s more),
    through the same Tracker and poll loop. No browser."""
    token = uuid.uuid4().hex
    profile = tempfile.mkdtemp(prefix="selftest_profile_")
    env = dict(os.environ, SELFTEST_TOKEN=token, PARENT_S="0.5", KID_S="2.0")
    parent = subprocess.Popen([sys.executable, "-c", FAKE_PARENT, "-profile", profile, "-juggler-pipe"],
                              env=env, stdin=subprocess.PIPE)
    tr = Tracker("SELFTEST_TOKEN", token, os.path.dirname(psutil.Process(parent.pid).exe()))
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (len(tr.members()) >= 3 and lock_present(profile)):
            tr.scan()
            time.sleep(0.05)
        members = tr.members()
        assert len(members) == 3, "expected parent + 2 children, got %r" % [m.brief() for m in members]
        picked, how = pick_parent(members)
        assert picked is not None and picked.pid == parent.pid, (picked and picked.brief(), parent.pid)
        assert profile_of(picked) == profile
        assert lock_present(profile) is True
        tr.pre_keys = set(tr.known)
        tr.start()
        parent.stdin.close()
        t1 = now()
        res = poll_until_gone(tr, picked, profile, parent, t1)
        parent.wait(5)
        assert not res["capped"], res
        assert 0.3 <= res["t_parent_gone"] <= 1.3, res["t_parent_gone"]
        assert res["t_lock_gone"] is not None and res["t_lock_gone"] <= 1.3, res["t_lock_gone"]
        assert 1.5 <= res["t_procs_gone"] <= 3.5, res["t_procs_gone"]
        assert res["t_parent_gone"] < res["t_procs_gone"], res
        assert res["poll_max_gap_ms"] < 400, res["poll_max_gap_ms"]
        print("selftest PASS: parent gone %.3f s, lock gone %.3f s, all gone %.3f s, max poll gap %.1f ms, timeline %s" % (
            res["t_parent_gone"], res["t_lock_gone"], res["t_procs_gone"], res["poll_max_gap_ms"], res["timeline"]))
        rows = [dict(os="selftest", arm="fake", variant="close", parallel=1, **{k: v for k, v in res.items()
                if k != "timeline"}, close_call_s=0.0)]
        d = tempfile.mkdtemp(prefix="selftest_summary_")
        pathlib.Path(d, "x.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        text = summarize(d)
        assert "| selftest | fake | close | x1 | 1 | 0 |" in text, text
        return 0
    finally:
        tr.kill_all()
        tr.stop()
        if parent.poll() is None:
            parent.kill()
        import shutil
        shutil.rmtree(profile, ignore_errors=True)


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--arm", choices=["stock", "inv-headed", "inv-truehl"])
        p.add_argument("--variant", choices=["close", "with"])
        p.add_argument("--binary", default="")
        p.add_argument("--os-label", default=os.environ.get("RUNNER_OS", sys.platform))

    one = sub.add_parser("one")
    common(one)
    one.add_argument("--round", type=int, default=0)
    one.add_argument("--index", type=int, default=0)
    one.add_argument("--parallel", type=int, default=1)
    one.add_argument("--seed", type=int, default=7000)
    one.add_argument("--barrier", default="")
    one.set_defaults(fn=cmd_one)

    cell = sub.add_parser("cell")
    common(cell)
    cell.add_argument("--rounds", type=int, required=True)
    cell.add_argument("--parallel", type=int, default=1)
    cell.add_argument("--seed-base", type=int, default=7000)
    cell.add_argument("--out", default="evidence")
    cell.set_defaults(fn=cmd_cell)

    s = sub.add_parser("summary")
    s.add_argument("dir")
    s.set_defaults(fn=cmd_summary)

    m = sub.add_parser("meta")
    m.add_argument("--binary", default="")
    m.add_argument("--os-label", default=os.environ.get("RUNNER_OS", sys.platform))
    m.add_argument("--out", default="evidence")
    m.set_defaults(fn=cmd_meta)

    t = sub.add_parser("selftest")
    t.set_defaults(fn=cmd_selftest)

    a = ap.parse_args(argv)
    if a.cmd in ("one", "cell") and not (a.arm and a.variant):
        ap.error("--arm and --variant are required")
    if a.cmd in ("one", "cell") and a.arm != "stock" and not a.binary:
        ap.error("--binary is required for the invisible arms")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
