"""HUNT SCAFFOLDING, NEVER MERGED: stamp the moment the browser pipe delivers a /landed request, in the installed invisible_core.

invisible_core.juggler.connection _read_loop, on every chunk read from the browser. A chunk that carries "requestWillBeSent" and "/landed"
appends a wall-clock time (seconds) to a list in memory; the list is written ONCE, at process exit (atexit), to the file named by $HUNT_PIPE_TRACE
(no file is opened or written on the reader during the run). Run once per venv after the install: python scripts/hunt_trace_pipes.py
(only the core part of the original in the trace worktree: the arms here are ours only).
"""
import importlib.util
import io
import os

path = importlib.util.find_spec("invisible_core.juggler.connection").origin
s = io.open(path, encoding="utf-8").read()
old = """                chunk = os.read(self._from_browser, 65536)
                if not chunk:
                    break
"""
new = """                chunk = os.read(self._from_browser, 65536)
                if not chunk:
                    break
                if b"requestWillBeSent" in chunk and b"/landed" in chunk and os.environ.get("HUNT_PIPE_TRACE"):
                    import time as _ht
                    _HUNT_PIPE.append(_ht.time())
"""
assert s.count(old) == 1, "core _read_loop"
s = s.replace(old, new, 1).rstrip(chr(10)) + '''


_HUNT_PIPE = []


def _hunt_pipe_dump():
    import os as _o
    path = _o.environ.get("HUNT_PIPE_TRACE")
    if path and _HUNT_PIPE:
        try:
            with open(path, "w") as fh:
                fh.write("".join("%.4f" % t + chr(10) for t in _HUNT_PIPE))
        except Exception:
            pass


import atexit as _hunt_atexit
_hunt_atexit.register(_hunt_pipe_dump)
'''
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", path)
