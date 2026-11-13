"""HUNT SCAFFOLDING, NEVER MERGED: the runtime that scripts/hunt_trace_locks.py copies into the installed invisible_core as
invisible_core/_hunt_locks.py. It times, on the connection's READER thread only, where a message spends the time between the pipe read and
the lifecycle:

  (a) every listener call of Connection.dispatch_event (row k=lst: name, event, wall ms, cpu ms),
  (b) the wrapper's gated_dispatch wait on the events gate (row k=gate, with who held it),
  (c) every lock / condition the reader takes or waits on; a wait of 5 ms or more is a row k=lock (name, ms, holder, where it was taken),
  (d) per message: wall against CPU time of the thread (row k=msg), so wall - cpu - lock waits - gate = time off the CPU (GIL, preemption).

One JSON line per row, to $HUNT_LOCKS_TRACE (one file per process); the rows of everything small are not written but are counted in a
{"k": "totals"} row written at the end (and the rows of the messages of a chunk that carries /landed are all written). Times in rows are
time.time() of the END of the span, "ms" its length (time.perf_counter()), so a row is [t - ms/1000, t] on the wall clock of the other stamps.
Nothing here may break the engine: every hook swallows its own failure.
"""
import atexit
import json
import os
import sys
import threading
import time

LOCK_ROW_MS = 5.0     # a wait this long or longer is a row
LST_ROW_MS = 0.5      # a listener call this long or longer is a row (all of them in a /landed chunk)
MSG_ROW_MS = 1.0      # a message this long or longer is a row (all of them in a /landed chunk)
GATE_ROW_MS = 1.0

_pc = time.perf_counter
READERS: set = set()
_TL = threading.local()
ROWS: list = []
TOTALS: dict = {}     # item -> [n, sum ms, max ms]
MSGS = {"n": 0, "wall_ms": 0.0, "cpu_ts_ms": 0.0, "cpu_cyc_ms": 0.0, "chunks": 0, "bytes": 0}
_state = {"flusher": False, "cyc_per_s": None, "pid": os.getpid()}


# ── cpu time of the calling thread ────────────────────────────────────────────────────────────────────────────────────────────────
# time.thread_time() on Windows is GetThreadTimes: accounted per scheduler tick (~15.6 ms), too coarse for a message. The cycle counter
# of the thread (QueryThreadCycleTime) is exact; its rate is measured once, in short slices, taking the best slice (a slice that was
# not preempted), because the cycles tick only while the thread runs.
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


def _cpu_ms_cycles(c0, c1):
    rate = _state["cyc_per_s"]
    if _cycles is None or not rate:
        return None
    return (c1 - c0) / rate * 1000.0


def _mark_cpu():
    return (time.thread_time(), _cycles() if _cycles is not None else 0)


# ── rows and totals ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _path():
    return os.environ.get("HUNT_LOCKS_TRACE")


def _row(**r):
    if _path():
        ROWS.append(r)


def _count(item, ms):
    t = TOTALS.get(item)
    if t is None:
        TOTALS[item] = [1, ms, ms]
    else:
        t[0] += 1
        t[1] += ms
        if ms > t[2]:
            t[2] = ms


def flush(final=False):
    path = _path()
    if not path:
        return
    try:
        n = len(ROWS)
        batch = ROWS[:n]
        del ROWS[:n]
        lines = [json.dumps(r, default=str) for r in batch]
        if final:
            lines.append(json.dumps({"k": "totals", "pid": _state["pid"], "cyc_per_s": _state["cyc_per_s"], "msgs": MSGS,
                                     "items": {k: [v[0], round(v[1], 3), round(v[2], 3)] for k, v in TOTALS.items()}}))
        if lines:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _flusher():
    while True:
        time.sleep(1.0)
        flush()


def _start_flusher():
    """From the thread that builds a Connection, never from the reader."""
    if _state["flusher"] or not _path():
        return
    _state["flusher"] = True
    try:
        _state["cyc_per_s"] = _calibrate()
        threading.Thread(target=_flusher, daemon=True, name="hunt-flusher").start()
        atexit.register(lambda: flush(final=True))
    except Exception:
        pass


def _on_reader():
    return threading.get_ident() in READERS


def _ev():
    return getattr(_TL, "method", None)


def _site(frame):
    try:
        return "%s:%d" % (os.path.basename(frame.f_code.co_filename), frame.f_lineno)
    except Exception:
        return None


# ── a lock that says who holds it and how long the reader waited for it ────────────────────────────────────────────────────────────
class Timed:
    """Stands in for a Lock, RLock or Condition (what they have of acquire / release / with / wait / notify). It delegates to the real one."""

    def __init__(self, real, name, quiet=False):
        self._real = real
        self._name = name
        self._quiet = quiet
        self._depth = 0
        self.owner_name = None
        self.owner_t = 0.0
        self.owner_site = None

    # who holds it now (read without a lock: a hint, and said so)
    def _snap(self):
        n = self.owner_name
        if n is None:
            return None
        return (n, self.owner_t, self.owner_site)

    def _mark(self, frame):
        self._depth += 1
        self.owner_name = threading.current_thread().name
        self.owner_t = _pc()
        self.owner_site = _site(frame)

    def _waited(self, kind, ms, snap, got=True):
        """Called on the reader only. The events gate (quiet) is timed by gate_end instead."""
        if self._quiet:
            return
        _TL.lock_ms = getattr(_TL, "lock_ms", 0.0) + ms
        _count("%s:%s" % (kind, self._name), ms)
        if ms >= LOCK_ROW_MS and not self._quiet:
            held_ms = None
            if snap is not None:
                held_ms = round((_pc() - snap[1]) * 1000, 1)
            _row(k="lock", op=kind, name=self._name, ms=round(ms, 3), t=round(time.time(), 4), ev=_ev(), got=got,
                 holder=snap[0] if snap else None, held_at=snap[2] if snap else None, held_so_far_ms=held_ms)

    def acquire(self, *args, **kw):
        frame = sys._getframe(1)
        return self._acq(args, kw, frame)

    def _acq(self, args, kw, frame):
        try:
            reader = threading.get_ident() in READERS
        except Exception:
            reader = False
        if reader:
            snap = self._snap()
            t0 = _pc()
            ok = self._real.acquire(*args, **kw)
            ms = (_pc() - t0) * 1000.0
            if ok:
                self._mark(frame)
            try:
                self._waited("lock", ms, snap, ok)
            except Exception:
                pass
            return ok
        ok = self._real.acquire(*args, **kw)
        if ok:
            self._mark(frame)
        return ok

    def release(self):
        if self._depth > 0:
            self._depth -= 1
        if self._depth == 0:
            self.owner_name = None
        return self._real.release()

    def __enter__(self):
        self._acq((), {}, sys._getframe(1))
        return self

    def __exit__(self, *exc):
        self.release()
        return None

    def locked(self):
        return self._real.locked()

    def _is_owned(self):
        return self._real._is_owned()

    # Condition
    def wait(self, timeout=None):
        depth = self._depth
        self._depth = 0
        self.owner_name = None
        reader = _on_reader()
        snap = None
        t0 = _pc()
        try:
            return self._real.wait(timeout)
        finally:
            ms = (_pc() - t0) * 1000.0
            self._depth = depth
            self.owner_name = threading.current_thread().name
            self.owner_t = _pc()
            self.owner_site = _site(sys._getframe(1))
            if reader:
                try:
                    # a wait is a pause the caller asked for; it is counted apart, and not into the lock waits of the message
                    _count("wait:%s" % self._name, ms)
                    if ms >= LOCK_ROW_MS and not self._quiet:
                        _row(k="lock", op="wait", name=self._name, ms=round(ms, 3), t=round(time.time(), 4), ev=_ev(), got=True,
                             holder=snap, held_at=None, held_so_far_ms=None)
                except Exception:
                    pass

    def wait_for(self, predicate, timeout=None):
        endtime = None
        waittime = timeout
        result = predicate()
        while not result:
            if waittime is not None:
                if endtime is None:
                    endtime = time.monotonic() + waittime
                else:
                    waittime = endtime - time.monotonic()
                    if waittime <= 0:
                        break
            self.wait(waittime)
            result = predicate()
        return result

    def notify(self, n=1):
        return self._real.notify(n)

    def notify_all(self):
        return self._real.notify_all()

    notifyAll = notify_all

    def __getattr__(self, name):
        return getattr(self._real, name)


def timed(real, name, quiet=False):
    if isinstance(real, Timed):
        return real
    return Timed(real, name, quiet)


def hook_class(cls, names, prefix=""):
    """After cls.__init__, each attribute of `names` that exists on the instance is replaced by a Timed over the real object."""
    original = cls.__init__
    if getattr(original, "_hunt_wrapped", False):
        return

    def __init__(self, *a, **k):
        original(self, *a, **k)
        try:
            for n in names:
                v = self.__dict__.get(n)
                if v is not None and not isinstance(v, Timed):
                    self.__dict__[n] = Timed(v, prefix + n)
        except Exception:
            pass
    __init__._hunt_wrapped = True
    __init__.__qualname__ = original.__qualname__
    cls.__init__ = __init__


def timed_fn(fn, name):
    """A function whose duration, on the reader, is counted as `name` (a row from 1 ms)."""
    def wrapper(*a, **k):
        if not _on_reader():
            return fn(*a, **k)
        t0 = _pc()
        try:
            return fn(*a, **k)
        finally:
            ms = (_pc() - t0) * 1000.0
            _count(name, ms)
            if ms >= 1.0:
                _row(k="io", name=name, ms=round(ms, 3), t=round(time.time(), 4), ev=_ev())
    return wrapper


# ── the connection ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _listener_name(fn):
    try:
        owner = getattr(fn, "__self__", None)
        q = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
        if owner is not None and "." not in q:
            q = "%s.%s" % (type(owner).__name__, q)
        return q
    except Exception:
        return "?"


def _core_dispatch_event(self, method, params, session):
    """Connection.dispatch_event with each listener timed on the reader. Same loop as the core's."""
    self.delivered += 1
    with self._listeners_lock:
        subscribers = list(self._listeners)
    reader = _on_reader()
    if reader:
        _TL.method = method
    verbose = getattr(_TL, "verbose", False)
    for fn in subscribers:
        if reader:
            t0 = _pc()
            u0, c0 = _mark_cpu()
        try:
            fn(method, params, session)
        except Exception as failure:
            if len(self.handler_errors) < 32:
                self.handler_errors.append("%s: %s" % (method, failure))
        if reader:
            try:
                ms = (_pc() - t0) * 1000.0
                u1, c1 = _mark_cpu()
                name = _listener_name(fn)
                _count("lst:" + name, ms)
                _TL.lst_ms = getattr(_TL, "lst_ms", 0.0) + ms
                if ms >= LST_ROW_MS or verbose:
                    cyc = _cpu_ms_cycles(c0, c1)
                    _row(k="lst", name=name, ev=method, ms=round(ms, 3), t=round(time.time(), 4),
                         cpu_ts=round((u1 - u0) * 1000, 3), cpu_cyc=None if cyc is None else round(cyc, 3))
            except Exception:
                pass


def _wrap_deliver(orig):
    def _deliver(self, raw):
        if not _on_reader():
            return orig(self, raw)
        _TL.lock_ms = 0.0
        _TL.gate_ms = 0.0
        _TL.lst_ms = 0.0
        _TL.method = None
        t0 = _pc()
        u0, c0 = _mark_cpu()
        try:
            return orig(self, raw)
        finally:
            try:
                wall = (_pc() - t0) * 1000.0
                u1, c1 = _mark_cpu()
                cpu_ts = (u1 - u0) * 1000.0
                cyc = _cpu_ms_cycles(c0, c1)
                MSGS["n"] += 1
                MSGS["wall_ms"] += wall
                MSGS["cpu_ts_ms"] += cpu_ts
                if cyc is not None:
                    MSGS["cpu_cyc_ms"] += cyc
                landed = b"/landed" in raw and b"requestWillBeSent" in raw
                if wall >= MSG_ROW_MS or landed or getattr(_TL, "verbose", False):
                    _row(k="msg", t=round(time.time(), 4), ms=round(wall, 3), cpu_ts=round(cpu_ts, 3),
                         cpu_cyc=None if cyc is None else round(cyc, 3), lock=round(_TL.lock_ms, 3), gate=round(_TL.gate_ms, 3),
                         lst=round(_TL.lst_ms, 3), ev=_ev(), landed=landed, n=len(raw))
            except Exception:
                pass
    return _deliver


def chunk_begin():
    return _pc()


def on_chunk(chunk, t_begin):
    """In _read_loop, right after the pipe read (and the older stamp of /landed): every message of a chunk with /landed is written."""
    try:
        if not _on_reader():
            READERS.add(threading.get_ident())
        MSGS["chunks"] += 1
        MSGS["bytes"] += len(chunk)
        landed = b"requestWillBeSent" in chunk and b"/landed" in chunk
        _TL.verbose = landed
        if landed:
            io_ms = (_pc() - t_begin) * 1000.0
            _count("hunt_pipe_stamp", io_ms)
            _row(k="chunk", t=round(time.time(), 4), n=len(chunk), stamp_ms=round(io_ms, 3))
    except Exception:
        pass


def gate_begin():
    return _pc()


def gate_end(t0, gate, got, waiting, snap=None):
    """In gated_dispatch, after `gate.acquire`: the reader's wait for the events gate."""
    try:
        if not _on_reader():
            return
        ms = (_pc() - t0) * 1000.0
        _TL.gate_ms = getattr(_TL, "gate_ms", 0.0) + ms
        _count("gate", ms)
        if ms >= GATE_ROW_MS:
            holder = snap
            _row(k="gate", t=round(time.time(), 4), ms=round(ms, 3), ev=_ev(), got=bool(got), waiting=bool(waiting),
                 holder=holder[0] if holder else None, held_at=holder[2] if holder else None,
                 held_so_far_ms=round((t0 - holder[1]) * 1000, 1) if holder else None)
    except Exception:
        pass


def gate_snap(gate):
    try:
        return gate._snap()
    except Exception:
        return None


def hook_connection(cls):
    """Called at the end of invisible_core.juggler.connection with the Connection class."""
    cls.dispatch_event = _core_dispatch_event
    cls._deliver = _wrap_deliver(cls._deliver)
    original = cls.__init__

    def __init__(self, *a, **k):
        original(self, *a, **k)
        try:
            for n in ("_lock", "_write_lock", "_listeners_lock"):
                v = self.__dict__.get(n)
                if v is not None and not isinstance(v, Timed):
                    self.__dict__[n] = Timed(v, "Connection." + n)
            READERS.add(self._reader.ident)
            _start_flusher()
        except Exception:
            pass
    __init__._hunt_wrapped = True
    cls.__init__ = __init__
