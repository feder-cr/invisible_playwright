"""HUNT SCAFFOLDING, NEVER MERGED: put the slow-listener instrumentation into the installed invisible_core.

Run once per venv, after the install:
    python scripts/hunt_trace_slow.py
It copies scripts/hunt_slow_runtime.py into the core as invisible_core/_hunt_slow.py and patches invisible_core/juggler/connection.py by
exact text (an assert if the text is not there exactly once): the decode in Connection._deliver goes through _hunt_slow.decode, and
Connection.dispatch_event is replaced at the end of the module by the timed twin (see the head of hunt_slow_runtime.py). Nothing else
of the core or the wrapper is touched. Rows go to $HUNT_SLOW_TRACE, one file per process, written once at exit.
"""
import importlib.util
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))


def once(text, old, new, what):
    assert text.count(old) == 1, "%s: expected the text once, found %d" % (what, text.count(old))
    return text.replace(old, new, 1)


conn_path = importlib.util.find_spec("invisible_core.juggler.connection").origin
core_dir = os.path.dirname(os.path.dirname(conn_path))
shutil.copyfile(os.path.join(HERE, "hunt_slow_runtime.py"), os.path.join(core_dir, "_hunt_slow.py"))
print("copied the runtime to", os.path.join(core_dir, "_hunt_slow.py"))

s = io.open(conn_path, encoding="utf-8").read()
assert "_hunt_slow" not in s, "already patched"
s = once(s, 'msg = json.loads(raw.decode("utf-8"))', "msg = _hunt_slow.decode(self, raw)", "core _deliver decode")
for needle in ("for fn in subscribers:", "fn(method, params, session)", "with self._listeners_lock:", "self.delivered += 1"):
    assert needle in s, "core dispatch_event changed: " + needle
s = s.rstrip(chr(10)) + "\n\n\nfrom invisible_core import _hunt_slow  # HUNT\n_hunt_slow.hook_connection(Connection)\n"
io.open(conn_path, "w", encoding="utf-8", newline=chr(10)).write(s)
print("patched", conn_path)
