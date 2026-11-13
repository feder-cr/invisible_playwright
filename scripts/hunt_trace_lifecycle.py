"""HUNT SCAFFOLDING, NEVER MERGED: patch the installed invisible_core lifecycle to trace what decides networkidle.

Every event the lifecycle applies (Page.* and Network.*) and every networkidle it gives is written as a JSON line, with time.monotonic(),
to the file named by $HUNT_LC_TRACE. Run once after the install: python scripts/hunt_trace_lifecycle.py
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


def _hunt_lc(**row):
    import json as _j, os as _o, time as _t
    path = _o.environ.get("HUNT_LC_TRACE")
    if not path:
        return
    row["t"] = round(_t.monotonic(), 4)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            print(_j.dumps(row, default=str), file=fh)
    except Exception:
        pass
'''
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", path)
