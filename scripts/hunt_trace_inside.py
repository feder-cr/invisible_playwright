"""HUNT SCAFFOLDING, NEVER MERGED: where the time goes between our pipe reader and the lifecycle.

Patches the installed invisible_core: every listener call in Connection.dispatch_event that takes 20 ms or more, and every wait of
Lifecycle._on_event for the lifecycle's lock of 20 ms or more, is written (wall clock) to $HUNT_INSIDE_TRACE with the listener's name,
the event and the thread that held the lock. Run once after the install: python scripts/hunt_trace_inside.py
"""
import importlib.util
import io

path = importlib.util.find_spec("invisible_core.juggler.connection").origin
s = io.open(path, encoding="utf-8").read()
old = """        for fn in subscribers:
            try:
                fn(method, params, session)
            except Exception as failure:
"""
new = """        for fn in subscribers:
            try:
                _ht0 = time.time()
                fn(method, params, session)
                _hdt = time.time() - _ht0
                if _hdt >= 0.02:
                    _hunt_inside(kind="listener", fn=getattr(fn, "__qualname__", repr(fn))[:120], ev=method, ms=round(_hdt * 1000), t=round(_ht0, 4))
            except Exception as failure:
"""
assert s.count(old) == 1, "dispatch_event"
s = s.replace(old, new, 1)
if "\nimport time\n" not in s:
    s = s.replace("\nimport json\n", "\nimport json\nimport time\n", 1)
s = s.rstrip(chr(10)) + '''


def _hunt_inside(**row):
    import json as _j, os as _o
    path = _o.environ.get("HUNT_INSIDE_TRACE")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            print(_j.dumps(row), file=fh)
'''
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", path)

path = importlib.util.find_spec("invisible_core.juggler.lifecycle").origin
s = io.open(path, encoding="utf-8").read()
old = """    def _on_event(self, method: str, p: dict) -> None:
        with self._cv:
"""
new = """    def _on_event(self, method: str, p: dict) -> None:
        import time as _hti
        _hw0 = _hti.time()
        self._cv.acquire()
        _hwait = _hti.time() - _hw0
        self._cv.release()
        if _hwait >= 0.02:
            from invisible_core.juggler.connection import _hunt_inside
            _hunt_inside(kind="lifecycle lock wait", ev=method, ms=round(_hwait * 1000), t=round(_hw0, 4), holder=getattr(self, "_hunt_holder", None))
        with self._cv:
"""
assert s.count(old) == 1, "_on_event"
s = s.replace(old, new, 1)
old = """        if self.announce is not None:
            self.announce(what, frame_id, url, **detail)
"""
new = """        if self.announce is not None:
            import time as _hta
            _ha0 = _hta.time()
            self.announce(what, frame_id, url, **detail)
            _hadt = _hta.time() - _ha0
            if _hadt >= 0.02:
                from invisible_core.juggler.connection import _hunt_inside
                _hunt_inside(kind="announce under the lifecycle lock", ev=what, ms=round(_hadt * 1000), t=round(_ha0, 4),
                             fn=getattr(self.announce, "__qualname__", "?"))
"""
assert s.count(old) == 1, "_tell"
s = s.replace(old, new, 1)
old = """        self._cv = threading.Condition()
"""
new = """        self._cv = _HuntCondition()
"""
assert s.count(old) == 1, "_cv"
s = s.replace(old, new, 1)
s = s.rstrip(chr(10)) + """


import atexit as _hatexit
import sys as _hsys
import threading as _hthreading
import time as _htime

_HUNT_MAX = {}


def _hunt_record(holder, held):
    import os as _o
    m = _HUNT_MAX.get(holder)
    _HUNT_MAX[holder] = (max(held, m[0]) if m else held, (m[1] if m else 0) + 1, (m[2] if m else 0.0) + held)
    if held >= 0.02 and _o.environ.get("HUNT_INSIDE_TRACE"):
        from invisible_core.juggler.connection import _hunt_inside
        _hunt_inside(kind="lifecycle lock held", fn=holder, ms=round(held * 1000), t=round(_htime.time(), 4))


def _hunt_dump():
    import json as _j, os as _o
    path = _o.environ.get("HUNT_LOCK_MAX")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, (mx, n, tot) in sorted(_HUNT_MAX.items(), key=lambda kv: -kv[1][0]):
                print(_j.dumps({"holder": k, "max_ms": round(mx * 1000), "count": n, "total_ms": round(tot * 1000)}), file=fh)


_hatexit.register(_hunt_dump)


class _HuntCondition(_hthreading.Condition):
    # How long the lifecycle's lock is HELD, outermost holds only, per holder (the function that took it); the waits inside
    # Condition.wait are not held time.
    def __init__(self):
        super().__init__()
        self._hdepth = 0
        self._hsince = 0.0
        self._hholder = None

    def __enter__(self):
        r = super().__enter__()
        self._hdepth += 1
        if self._hdepth == 1:
            f = _hsys._getframe(1)
            self._hholder = "%s:%d" % (f.f_code.co_qualname, f.f_lineno)
            self._hsince = _htime.time()
        return r

    def __exit__(self, *a):
        self._hdepth -= 1
        if self._hdepth == 0:
            _hunt_record(self._hholder, _htime.time() - self._hsince)
        return super().__exit__(*a)

    def wait(self, timeout=None):
        holder = self._hholder
        _hunt_record(holder, _htime.time() - self._hsince)
        depth = self._hdepth
        try:
            return super().wait(timeout)
        finally:
            self._hdepth = depth
            self._hholder = holder
            self._hsince = _htime.time()
"""
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", path)
