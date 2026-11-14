"""HUNT SCAFFOLDING, NEVER MERGED: the runtime that scripts/hunt_trace_slow.py copies into the installed invisible_core as
invisible_core/_hunt_slow.py. It names the line of code behind the few slow listener calls left on the connection's READER thread.

  (1) every listener call of Connection.dispatch_event made ON THE READER: wall time, CPU time of the thread (cycle counter, exact; see
      below), the event method and the listener. All calls go into per-(listener, event) totals; a call of 2 ms or more is also a row
      {"k": "lst"} (id, listener, event, wall, cpu, end time).
  (2) a sampler thread (started by the thread that builds the Connection, never by the reader): while a listener call (or a decode) on the
      reader has lasted more than 20 ms it takes sys._current_frames()[reader] every 2 ms and keeps the innermost 12 frames as
      "file:line:function" (innermost first), a row {"k": "smp"} (call id, ms into the call, ms since the previous sample, stack).
  (3) the decode of a message (json.loads of the raw bytes, in Connection._deliver): a row {"k": "dec"} when it took more than 20 ms, with
      the message size, wall and CPU; the event method is added when the message turns out to be an event.
  (4) off-CPU per call = wall - CPU (cycle counter): what a call spent waiting for the GIL, a lock, or being preempted.

Everything stays in memory and is written ONCE, at process exit (atexit), to $HUNT_SLOW_TRACE, one JSON line per row, last row {"k": "totals"}.
The sampler needs the GIL to sample: while the reader holds it inside one C call (a big json.loads, say) the sampler cannot run, so the gap
between samples (field "dt") is itself a measure, and the first sample after such a gap shows where the call was when the GIL came free.
Nothing here may break the engine: every hook swallows its own failure.
"""
import atexit
import json
import os
import sys
import threading
import time

SAMPLE_AFTER_MS = 20.0   # the sampler starts on a call that has lasted this long
SAMPLE_EVERY_S = 0.002
STACK_DEPTH = 12
LST_ROW_MS = 2.0         # a listener call this long or longer is a row (all of them are in the totals)
DEC_ROW_MS = 20.0        # a decode this long or longer is a row
MAX_SAMPLES = 40000      # per process
MAX_SAMPLES_PER_CALL = 600

_pc = time.perf_counter
ROWS: list = []
TOTALS: dict = {}        # "listener | event" -> [n, wall ms sum, cpu ms sum, wall ms max]
STATE = {"started": False, "cyc_per_s": None, "pid": os.getpid(), "calls": 0, "samples": 0, "stop": False, "sampler_ticks": 0,
         "sampler_error": None}
_TL = threading.local()
#: What the reader is doing right now: (call id, perf_counter at the start, kind "lst" or "dec", reader thread ident), or None.
_CUR = None
_ID = [0]


def _path():
    return os.environ.get("HUNT_SLOW_TRACE")


#: read once, at import: no environment lookup on the reader's path
ENABLED = bool(_path())


# ── cpu time of the calling thread ────────────────────────────────────────────────────────────────────────────────────────────────
# time.thread_time() on Windows is GetThreadTimes, accounted per scheduler tick (~15.6 ms): too coarse for one call. The cycle counter of
# the thread (QueryThreadCycleTime) is exact; its rate is measured once, in short slices, taking the best slice (one that was not
# preempted), because the cycles tick only while the thread runs.
_cycles = None
if sys.platform == "win32":
    try:
        import ctypes
        _k32 = ctypes.windll.kernel32
        _k32.GetCurrentThread.restype = ctypes.c_void_p
        _k32.QueryThreadCycleTime.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)]
        _k32.QueryThreadCycleTime.restype = ctypes.c_int
        _cyc_box = threading.local()

        def _cycles():
            box = getattr(_cyc_box, "v", None)
            if box is None:
                box = _cyc_box.v = ctypes.c_ulonglong()
            _k32.QueryThreadCycleTime(_k32.GetCurrentThread(), ctypes.byref(box))
            return box.value
    except Exception:
        _cycles = None


def _calibrate():
    """cycles per second of a thread that runs, from the best of 25 slices of about 3 ms of busy work. None if there is no counter."""
    if _cycles is None:
        return None
    best = 0.0
    for _ in range(25):
        c0, t0 = _cycles(), _pc()
        while _pc() - t0 < 0.003:
            pass
        c1, t1 = _cycles(), _pc()
        if t1 > t0 and c1 > c0:
            best = max(best, (c1 - c0) / (t1 - t0))
    return best or None


def _cpu_ms(c0, c1):
    rate = STATE["cyc_per_s"]
    if _cycles is None or not rate:
        return None
    return (c1 - c0) / rate * 1000.0


def _now_cyc():
    return _cycles() if _cycles is not None else 0


# ── names ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _listener_name(fn):
    try:
        owner = getattr(fn, "__self__", None)
        q = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
        if owner is not None and "." not in q:
            q = "%s.%s" % (type(owner).__name__, q)
        mod = getattr(fn, "__module__", None) or ""
        return "%s:%s" % (mod.rsplit(".", 1)[-1], q) if mod else q
    except Exception:
        return "?"


def _short(path):
    """site-packages/invisible_core/juggler/lifecycle.py -> invisible_core/juggler/lifecycle.py; a stdlib or other path -> its last two parts."""
    p = path.replace("\\", "/")
    i = p.rfind("site-packages/")
    if i >= 0:
        return p[i + len("site-packages/"):]
    parts = p.split("/")
    return "/".join(parts[-2:])


def _stack(frame):
    out = []
    f = frame
    while f is not None and len(out) < STACK_DEPTH:
        c = f.f_code
        if not c.co_filename.endswith("_hunt_slow.py"):   # the hunt's own frames are not the code under test
            out.append("%s:%d:%s" % (_short(c.co_filename), f.f_lineno, c.co_name))
        f = f.f_back
    return out


# ── the one write ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def flush():
    """Once, at exit: stop the sampler, write every row, then the totals."""
    STATE["stop"] = True
    path = _path()
    if not path:
        return
    try:
        n = len(ROWS)
        batch = ROWS[:n]
        lines = [json.dumps(r, default=str) for r in batch]
        lines.append(json.dumps({"k": "totals", "pid": STATE["pid"], "cyc_per_s": STATE["cyc_per_s"], "calls": STATE["calls"],
                                 "samples": STATE["samples"], "sampler_ticks": STATE["sampler_ticks"],
                                 "sampler_error": STATE["sampler_error"],
                                 "items": {k: [v[0], round(v[1], 3), round(v[2], 3), round(v[3], 3)] for k, v in TOTALS.items()}}))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


# ── the sampler ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _sampler():
    last_id = None
    last_t = 0.0
    taken = 0
    try:
        while not STATE["stop"]:
            time.sleep(SAMPLE_EVERY_S)
            STATE["sampler_ticks"] += 1
            cur = _CUR
            if cur is None:
                last_id = None
                continue
            cid, t0, kind, ident = cur
            now = _pc()
            if (now - t0) * 1000.0 <= SAMPLE_AFTER_MS:
                continue
            if cid != last_id:
                last_id, last_t, taken = cid, t0, 0
            if taken >= MAX_SAMPLES_PER_CALL or STATE["samples"] >= MAX_SAMPLES:
                continue
            frame = sys._current_frames().get(ident)
            if frame is None or _CUR is not cur:
                continue   # the call ended while the frames were taken
            st = _stack(frame)
            ROWS.append({"k": "smp", "id": cid, "kind": kind, "at": round((now - t0) * 1000.0, 1),
                         "dt": round((now - last_t) * 1000.0, 1), "st": st})
            last_t = now
            taken += 1
            STATE["samples"] += 1
    except Exception as e:  # never break the run
        STATE["sampler_error"] = repr(e)[:200]


def _start():
    """From the thread that builds a Connection, never from the reader: the sampler and the one write at exit (the CPU calibration was
    made at import, before any Connection existed)."""
    if STATE["started"] or not ENABLED:
        return
    STATE["started"] = True
    try:
        atexit.register(flush)
        threading.Thread(target=_sampler, name="hunt-slow-sampler", daemon=True).start()
    except Exception:
        pass


# ── the hooks ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _on_reader(conn):
    try:
        return threading.get_ident() == conn._reader.ident
    except Exception:
        return False


def decode(conn, raw):
    """In Connection._deliver: json.loads(raw.decode("utf-8")), timed on the reader. Raises what json.loads raises, as the original does."""
    global _CUR
    if not ENABLED or not _on_reader(conn):
        return json.loads(raw.decode("utf-8"))
    _ID[0] += 1
    cid = _ID[0]
    t0 = _pc()
    c0 = _now_cyc()
    _CUR = (cid, t0, "dec", threading.get_ident())
    try:
        return json.loads(raw.decode("utf-8"))
    finally:
        _CUR = None
        try:
            ms = (_pc() - t0) * 1000.0
            _TL.dec = None
            if ms >= DEC_ROW_MS:
                cpu = _cpu_ms(c0, _now_cyc())
                row = {"k": "dec", "id": cid, "ms": round(ms, 3), "cpu": None if cpu is None else round(cpu, 3), "n": len(raw),
                       "t": round(time.time(), 4), "ev": None}
                ROWS.append(row)
                _TL.dec = row
        except Exception:
            pass


def dispatch_event(self, method, params, session):
    """Connection.dispatch_event with each listener timed on the reader. The same loop as the core's."""
    global _CUR
    self.delivered += 1
    with self._listeners_lock:
        subscribers = list(self._listeners)
    reader = ENABLED and _on_reader(self)
    if reader:
        dec = getattr(_TL, "dec", None)
        if dec is not None:
            dec["ev"] = method
            _TL.dec = None
        ident = threading.get_ident()
    for fn in subscribers:
        if reader:
            _ID[0] += 1
            cid = _ID[0]
            t0 = _pc()
            c0 = _now_cyc()
            _CUR = (cid, t0, "lst", ident)
        try:
            fn(method, params, session)
        except Exception as failure:
            if len(self.handler_errors) < 32:
                self.handler_errors.append("%s: %s" % (method, failure))
        if reader:
            _CUR = None
            try:
                c1 = _now_cyc()
                ms = (_pc() - t0) * 1000.0
                cpu = _cpu_ms(c0, c1)
                name = _listener_name(fn)
                STATE["calls"] += 1
                key = "%s | %s" % (name, method)
                t = TOTALS.get(key)
                if t is None:
                    TOTALS[key] = [1, ms, cpu or 0.0, ms]
                else:
                    t[0] += 1
                    t[1] += ms
                    t[2] += cpu or 0.0
                    if ms > t[3]:
                        t[3] = ms
                if ms >= LST_ROW_MS:
                    ROWS.append({"k": "lst", "id": cid, "name": name, "ev": method, "ms": round(ms, 3),
                                 "cpu": None if cpu is None else round(cpu, 3), "t": round(time.time(), 4)})
            except Exception:
                pass


def hook_connection(cls):
    """Called at the end of invisible_core.juggler.connection with the Connection class."""
    cls.dispatch_event = dispatch_event
    if ENABLED and STATE["cyc_per_s"] is None:
        STATE["cyc_per_s"] = _calibrate()
    original = cls.__init__
    if getattr(original, "_hunt_wrapped", False):
        return

    def __init__(self, *a, **k):
        original(self, *a, **k)
        try:
            _start()
        except Exception:
            pass
    __init__._hunt_wrapped = True
    cls.__init__ = __init__
