"""HUNT SCAFFOLDING, NEVER MERGED: how long does threading.Thread.start take, and what if it did not wait for the new thread.

Imported first by hunt_netidle.py. $HUNT_THREAD_ARM picks the arm:
  plain   the real Thread.start, stamped: every call of 1 ms or more, and every call made by the pipe reader thread, is a line
          (wall clock, the caller's thread name, the target) in $HUNT_THREAD_TRACE;
  nowait  the same stamp, but Thread.start does not block on `self._started.wait()` (3.11 body, minus its last line): the caller
          returns as soon as the OS thread exists, instead of when it has been scheduled and has taken the GIL.
Both arms also write, to the same file: a canary (a daemon thread that sleeps 5 ms in a loop and writes every oversleep of 20 ms or more:
a stall of the whole process, GIL or CPU, whoever causes it) and every garbage collection of 20 ms or more.
Nothing else changes between the arms. The reader thread starts a thread inside two listeners: `Lifecycle._settle` (a threading.Timer,
under the lifecycle lock, on every load) and the adoption of a new target (`BrowserDispatcher`, a thread per `Browser.attachedToTarget`).
"""
import json
import os
import threading
import time

ARM = os.environ.get("HUNT_THREAD_ARM", "")
OUT = os.environ.get("HUNT_THREAD_TRACE")
_real_start = threading.Thread.start


def _row(**r):
    if OUT:
        with open(OUT, "a", encoding="utf-8") as fh:
            print(json.dumps(r), file=fh)


def _nowait(self):
    if not self._initialized:
        raise RuntimeError("thread.__init__() not called")
    if self._started.is_set():
        raise RuntimeError("threads can only be started once")
    with threading._active_limbo_lock:
        threading._limbo[self] = self
    try:
        threading._start_new_thread(self._bootstrap, ())
    except Exception:
        with threading._active_limbo_lock:
            del threading._limbo[self]
        raise


def _stamped(self):
    caller = threading.current_thread().name
    t0 = time.time()
    try:
        return (_nowait if ARM == "nowait" else _real_start)(self)
    finally:
        ms = (time.time() - t0) * 1000
        if ms >= 1 or "_read_loop" in caller:
            target = getattr(self, "_target", None)
            _row(kind="Thread.start", caller=caller[:60], target=getattr(target, "__qualname__", type(self).__name__)[:60],
                 ms=round(ms, 2), t=round(t0, 4))


def _canary():
    while True:
        wall, t0 = time.time(), time.perf_counter()
        time.sleep(0.005)
        over = (time.perf_counter() - t0 - 0.005) * 1000
        if over >= 20:
            _row(kind="canary", ms=round(over, 1), t=round(wall, 4))


_gc_started = {}


def _gc_callback(phase, info):
    if phase == "start":
        _gc_started["t"] = (time.time(), time.perf_counter())
    elif "t" in _gc_started:
        wall, t0 = _gc_started.pop("t")
        ms = (time.perf_counter() - t0) * 1000
        if ms >= 20:
            _row(kind="gc", ms=round(ms, 1), t=round(wall, 4), generation=info.get("generation"))


if ARM in ("plain", "nowait"):
    threading.Thread.start = _stamped
    import gc
    gc.callbacks.append(_gc_callback)
    _real_start(threading.Thread(target=_canary, daemon=True, name="hunt-canary"))
