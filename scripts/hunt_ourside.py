"""HUNT SCAFFOLDING, NEVER MERGED: our process's side of the send hop (invisible_core Connection), timed in memory for every Page.dispatchKeyEvent.

Usage (scripts/hunt_typing.py, ours arms): ourside.install(clock) once, ourside.reset() at the start of a run, ourside.snapshot() when the run ends.

What is kept (everything in memory, nothing printed until the run's record is emitted):

  * LockProxy on Connection._write_lock and Connection._lock (the two locks the send path takes): for each acquisition the wait (request -> got), the
    holder as it was at the request (thread name, function that took it with file:line, how long it had held it), and for every hold of at least
    0.5 ms who held it and from when to when. The proxy wraps the SAME lock object, so mutual exclusion is the core's own.
  * Connection._write wrapped for Page.dispatchKeyEvent: entry, lock request, lock got, os.write start and end (S1 = its end), exit, and the CPU time of
    the thread (QueryThreadCycleTime, calibrated against the wall clock; GetThreadTimes ticks are 15 ms) around the whole call and around os.write. What
    is not CPU time in the call is: the lock wait, the syscall, the wait for the GIL after a blocking call, and being preempted. The residual
    wall - cpu - lock wait - os.write wall is what the GIL (or the scheduler) took while the thread was not waiting on the lock or inside the syscall.
  * a sampler thread, every ~4 ms: the Python stack (innermost 10 frames) of every thread (sys._current_frames) and how late the sampler itself
    woke up (a wake-up that is late by more than a few ms while this thread had nothing to wait for = the GIL was held by another thread or the OS
    did not run us: a GIL probe).
  * for each write whose decision-to-S1 time (S0 when known, else entry) is over 30 ms: the long case, with the holds / waits of both locks overlapping
    its window, the probe's worst lateness and the stacks sampled in the window (grouped by thread and stack).
"""
import ctypes
import os
import sys
import threading
import time

LONG_MS = 30.0
SAMPLE_S = 0.004
STACK_DEPTH = 10

_clock = None
_state = {"writes": [], "holds": [], "waits": [], "samples": [], "s0": None}
_last_os = {}
_cpu_ms = None
_installed = False


def _make_cpu():
    """thread CPU time in ms: cycles of the thread (Windows) over the calibrated cycles per second, else time.thread_time()"""
    if sys.platform == "win32":
        k32 = ctypes.windll.kernel32
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.QueryThreadCycleTime.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)]
        h = k32.GetCurrentThread()  # the pseudo handle of the calling thread, the same value in every thread

        def cycles():
            buf = ctypes.c_ulonglong()  # one per call: several threads read their own clocks
            k32.QueryThreadCycleTime(h, ctypes.byref(buf))
            return buf.value

        # calibration: a busy loop of 150 ms on this thread, cycles per wall second (an estimate: frequency may move; about 1 ms of error per 10 ms)
        c0, t0 = cycles(), time.perf_counter()
        while time.perf_counter() - t0 < 0.15:
            pass
        c1, t1 = cycles(), time.perf_counter()
        cps = (c1 - c0) / (t1 - t0)
        return (lambda: cycles() / cps * 1000.0), cps
    return (lambda: time.thread_time() * 1000.0), 0.0


class LockProxy:
    """Wraps a threading.Lock: same locking, plus who held it and who waited. `with` and acquire()/release() both work."""

    def __init__(self, real, name):
        self._real = real
        self.name = name
        self.holder = None  # (thread name, "func file:line", wall got)
        self.last_wait = {}  # thread ident -> (wall request, wall got, holder at the request)

    def _take(self, blocking, timeout, depth):
        t_req = _clock()
        h = self.holder if self._real.locked() else None
        ok = self._real.acquire(blocking, timeout) if timeout != -1 else self._real.acquire(blocking)
        if not ok:
            return False
        t_got = _clock()
        f = sys._getframe(depth)
        who = "%s %s:%d" % (f.f_code.co_name, os.path.basename(f.f_code.co_filename), f.f_lineno)
        self.holder = (threading.current_thread().name, who, t_got)
        self.last_wait[threading.get_ident()] = (t_req, t_got, h)
        if t_got - t_req >= 0.2:
            _state["waits"].append((self.name, threading.current_thread().name, who, round(t_req, 3), round(t_got, 3),
                                    (h[0], h[1], round(t_req - h[2], 3)) if h else None))
        return True

    def acquire(self, blocking=True, timeout=-1):
        return self._take(blocking, timeout, 2)

    def __enter__(self):
        self._take(True, -1, 2)
        return self

    def release(self):
        h = self.holder
        t = _clock()
        self.holder = None
        self._real.release()
        if h and t - h[2] >= 0.5:
            _state["holds"].append((self.name, h[0], h[1], round(h[2], 3), round(t, 3)))

    def __exit__(self, *a):
        self.release()

    def locked(self):
        return self._real.locked()


def os_timing(os_mod, fd, data):
    """os.write with its start/end wall and thread CPU kept for the calling thread; returns (bytes written, wall at the end)."""
    t0, c0 = _clock(), _cpu_ms()
    n = os_mod.write(fd, data)
    c1, t1 = _cpu_ms(), _clock()
    _last_os[threading.get_ident()] = (t0, t1, c1 - c0)
    return n, t1


def take_s0(wall):
    """the decision stamp (Keyboard.down, before the send) for the next write on this thread"""
    _state["s0"] = wall


def _sampler():
    me = threading.get_ident()
    prev = time.perf_counter()
    while True:
        time.sleep(SAMPLE_S)
        now = time.perf_counter()
        late = (now - prev - SAMPLE_S) * 1000.0
        prev = now
        t = _clock()
        names = {th.ident: th.name for th in threading.enumerate()}
        for ident, frame in sys._current_frames().items():
            if ident == me:
                continue
            parts, f, n = [], frame, 0
            while f is not None and n < STACK_DEPTH:
                parts.append("%s %s:%d" % (f.f_code.co_name, os.path.basename(f.f_code.co_filename), f.f_lineno))
                f, n = f.f_back, n + 1
            _state["samples"].append((round(t, 3), names.get(ident, str(ident)), " < ".join(parts)))
        _state["samples"].append((round(t, 3), "~probe", round(late, 2)))


def install(clock):
    """Patch invisible_core's Connection (lock proxies on every new connection, the timed _write). Idempotent."""
    global _clock, _cpu_ms, _installed
    if _installed:
        return
    from invisible_core.juggler import connection as conn
    _clock = clock
    _cpu_ms, cps = _make_cpu()
    print("HUNT-OURSIDE cpu clock: %s, cycles per second %.0f" % ("QueryThreadCycleTime" if cps else "thread_time", cps), flush=True)
    real_init = conn.Connection.__init__

    def init(self, *a, **k):
        real_init(self, *a, **k)
        self._write_lock = LockProxy(self._write_lock, "write_lock")
        self._lock = LockProxy(self._lock, "lock")

    conn.Connection.__init__ = init
    real_write = conn.Connection._write

    def write(self, msg):
        if not (isinstance(msg, dict) and msg.get("method") == "Page.dispatchKeyEvent"):
            return real_write(self, msg)
        s0, _state["s0"] = _state["s0"], None
        t_in, c_in = _clock(), _cpu_ms()
        ident = threading.get_ident()
        try:
            return real_write(self, msg)
        finally:
            c_out, t_out = _cpu_ms(), _clock()
            lw = getattr(self._write_lock, "last_wait", {}).get(ident)
            o = _last_os.pop(ident, None)
            p = msg.get("params") or {}
            rec = {"k": "%s:%s" % (p.get("type"), p.get("key")), "s0": round(s0, 3) if s0 else None, "in": round(t_in, 3),
                   "req": round(lw[0], 3) if lw else None, "got": round(lw[1], 3) if lw else None,
                   "holder": ([lw[2][0], lw[2][1], lw[2][2]] if lw and lw[2] else None),
                   "o0": round(o[0], 3) if o else None, "o1": round(o[1], 3) if o else None, "out": round(t_out, 3),
                   "cpu": round(c_out - c_in, 3), "cpu_os": round(o[2], 3) if o else None}
            _state["writes"].append(rec)

    conn.Connection._write = write
    threading.Thread(target=_sampler, name="hunt-sampler", daemon=True).start()
    _installed = True


def reset():
    for k in ("writes", "holds", "waits", "samples"):
        _state[k] = []
    _state["s0"] = None


def _start(w):
    return w["s0"] if w["s0"] is not None else w["in"]


def snapshot():
    """-> (writes, long cases). Each long case: the write, the locks' holds and waits overlapping its window, the probe's worst lateness and the stacks sampled in it."""
    writes = list(_state["writes"])
    holds, waits, samples = list(_state["holds"]), list(_state["waits"]), list(_state["samples"])
    cases = []
    for w in writes:
        if w["o1"] is None:
            continue
        a, b = _start(w), w["o1"]
        if b - a <= LONG_MS:
            continue
        stacks = {}
        late = []
        for t, name, st in samples:
            if a - 1 <= t <= b + 1:
                if name == "~probe":
                    late.append(st)
                else:
                    stacks[(name, st)] = stacks.get((name, st), 0) + 1
        cases.append({
            "write": w, "window": [round(a, 3), round(b, 3)],
            "holds": [h for h in holds if h[3] <= b and h[4] >= a][:40],
            "waits": [x for x in waits if x[4] >= a and x[3] <= b][:40],
            "probe_late_max_ms": max(late) if late else None, "probe_n": len(late),
            "stacks": sorted(([n, s, c] for (n, s), c in stacks.items()), key=lambda r: -r[2])[:14],
        })
    return writes, cases


def selftest():
    """Lock proxy, the holder it reports, os.write timing, the sampler and a long case, on plain threads and a pipe (no browser, no core)."""
    global _clock, _cpu_ms
    t0 = time.perf_counter()
    _clock = lambda: (time.time() - 0) * 1000.0
    _cpu_ms, cps = _make_cpu()
    reset()
    lock = LockProxy(threading.Lock(), "write_lock")
    started = threading.Event()

    def holder():
        with lock:
            started.set()
            time.sleep(0.08)

    th = threading.Thread(target=holder, name="holder-thread")
    th.start()
    started.wait()
    s_in = _clock()
    with lock:
        pass
    s_out = _clock()
    th.join()
    t, g, h = lock.last_wait[threading.get_ident()]
    assert g - t > 40, (g - t)
    assert h and h[0] == "holder-thread" and h[1].startswith("holder "), h
    assert any(x[0] == "write_lock" and x[3] >= 0 for x in _state["holds"]), _state["holds"]
    assert any(w[0] == "write_lock" and w[1] == threading.current_thread().name for w in _state["waits"]), _state["waits"]
    r, w = os.pipe()
    n, tend = os_timing(os, w, b"x" * 10)
    assert n == 10 and threading.get_ident() in _last_os
    os.close(r)
    os.close(w)
    # the sampler for a little while, with a thread that sits in a function
    threading.Thread(target=_sampler, name="hunt-sampler", daemon=True).start()
    stop = threading.Event()
    threading.Thread(target=lambda: stop.wait(0.3), name="waiter").start()
    time.sleep(0.25)
    stop.set()
    assert any(s[1] == "waiter" for s in _state["samples"]), "no stack sampled for the waiter"
    assert any(s[1] == "~probe" for s in _state["samples"])
    # long cases from fake write records: one over the lock window (holds, waits), one over the last 50 ms (stacks)
    now = _clock()
    for a, s0 in ((s_out, s_in - 5), (now, now - 50)):
        _state["writes"].append({"k": "keydown:a", "s0": s0, "in": s0 + 1, "req": s0 + 1.1, "got": a - 0.5, "holder": ["holder-thread", "f x.py:1", 3.0], "o0": a - 0.4,
                                 "o1": a, "out": a + 0.1, "cpu": 0.2, "cpu_os": 0.05})
    writes, cases = snapshot()
    assert len(cases) == 2, cases
    assert cases[0]["holds"] and cases[0]["waits"], cases[0]
    assert cases[1]["stacks"] and cases[1]["probe_n"] > 0, cases[1]
    print("ourside selftest ok (cpu clock %s, %.0f ms)" % ("cycles" if cps else "thread_time", (time.perf_counter() - t0) * 1000))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
