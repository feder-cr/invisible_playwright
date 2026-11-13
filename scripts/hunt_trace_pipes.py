"""HUNT SCAFFOLDING, NEVER MERGED: stamp the moment the browser pipe delivers a /landed request, in the installed invisible_core.

invisible_core.juggler.connection _read_loop, on every chunk read from the browser. A chunk that carries "requestWillBeSent" and "/landed"
appends a wall-clock time (seconds) to the file named by $HUNT_PIPE_TRACE. Run once per venv after the install: python scripts/hunt_trace_pipes.py
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
                    with open(os.environ["HUNT_PIPE_TRACE"], "a") as _hf:
                        print("%.4f" % _ht.time(), file=_hf)
"""
assert s.count(old) == 1, "core _read_loop"
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s.replace(old, new, 1))
print("patched", path)
