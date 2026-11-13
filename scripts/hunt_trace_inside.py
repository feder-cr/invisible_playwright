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
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", path)
