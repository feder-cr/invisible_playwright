"""HUNT SCAFFOLDING, NEVER MERGED: patch the installed invisible_core lifecycle to trace what decides networkidle.

Every event the lifecycle applies (Page.* and Network.*) and every networkidle it gives is written as a JSON line, with time.time(),
to a list in memory, written ONCE at process exit (atexit) to the file named by $HUNT_LC_TRACE (no file is opened or written on the reader
during the run). Run once after the install: python scripts/hunt_trace_lifecycle.py
"""
import importlib.util
import io

path = importlib.util.find_spec("invisible_core.juggler.lifecycle").origin
s = io.open(path, encoding="utf-8").read()
old = """    def _on_event(self, method: str, p: dict) -> None:
        with self._cv:
"""
new = """    def _on_event(self, method: str, p: dict) -> None:
        with self._cv:
            if method.startswith(("Page.", "Network.")):
                _hunt_lc(ev=method, frame=p.get("frameId"), nav=p.get("navigationId"), url=(p.get("url") or "")[:90],
                         rid=p.get("requestId"), name=p.get("name"), open=len(self._open),
                         states={k: sorted(v.states) for k, v in self.frames.items()})
"""
assert s.count(old) == 1, "_on_event"
s = s.replace(old, new, 1)
old = """            for f in waiting:
                f.states.add(state)
                self._tell(state, f.id)
"""
new = """            for f in waiting:
                if state == "networkidle":
                    _hunt_lc(ev="GIVE networkidle", frame=f.id, nav=f.navigation, url=(f.url or "")[:90], open=len(self._open),
                             quiet=round(quiet_for, 3))
                f.states.add(state)
                self._tell(state, f.id)
"""
assert s.count(old) == 1, "_settle"
s = s.replace(old, new, 1)
s = s.rstrip(chr(10)) + '''


_HUNT_LC = []


def _hunt_lc(**row):
    import os as _o, time as _t
    if not _o.environ.get("HUNT_LC_TRACE"):
        return
    row["t"] = round(_t.time(), 4)
    _HUNT_LC.append(row)


def _hunt_lc_dump():
    import json as _j, os as _o
    path = _o.environ.get("HUNT_LC_TRACE")
    if path and _HUNT_LC:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(_j.dumps(r, default=str) + chr(10) for r in _HUNT_LC))
        except Exception:
            pass


import atexit as _hunt_atexit
_hunt_atexit.register(_hunt_lc_dump)
'''
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", path)
