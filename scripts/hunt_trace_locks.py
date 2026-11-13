"""HUNT SCAFFOLDING, NEVER MERGED: time the reader's path in the installed invisible_core and invisible_playwright.

Run once per venv, AFTER scripts/hunt_trace_pipes.py and scripts/hunt_trace_lifecycle.py (it builds on their stamps):
    python scripts/hunt_trace_locks.py
It copies scripts/hunt_locks_runtime.py into the core as invisible_core/_hunt_locks.py and patches, by exact text (an assert if the text
is not there once):
  core connection.py   Connection.dispatch_event (each listener timed), Connection._deliver (wall against CPU), the locks of the
                       connection, and the chunk hook in _read_loop;
  core lifecycle.py    Lifecycle._cv; the file write of the lifecycle stamp is timed (it runs on the reader, under _cv);
  core injected.py     InjectedScript._contexts_changed;
  core instance.py     Browser._sessions_ready, _buffer_lock;
  wrapper server.py    the events gate (who holds it, how long gated_dispatch waits), PageDispatcher._close_lock / _held_lock /
                       _frames_lock / _announce_lock, BrowserDispatcher._sessions_ready / _buffer_lock / _targets_lock / _pages_ready.
Rows go to $HUNT_LOCKS_TRACE, one JSON file per process (see the head of hunt_locks_runtime.py).
"""
import importlib.util
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))


def origin(name):
    return importlib.util.find_spec(name).origin


def read(path):
    return io.open(path, encoding="utf-8").read()


def write(path, text):
    io.open(path, "w", encoding="utf-8", newline=chr(10)).write(text)


def once(text, old, new, what):
    assert text.count(old) == 1, "%s: expected the text once, found %d" % (what, text.count(old))
    return text.replace(old, new, 1)


IMPORT = "\n\nfrom invisible_core import _hunt_locks as _hunt_h  # HUNT\n"

# ── the runtime, inside the core ──────────────────────────────────────────────────────────────────────────────────────────────────
conn_path = origin("invisible_core.juggler.connection")
core_dir = os.path.dirname(os.path.dirname(conn_path))
shutil.copyfile(os.path.join(HERE, "hunt_locks_runtime.py"), os.path.join(core_dir, "_hunt_locks.py"))
print("copied the runtime to", os.path.join(core_dir, "_hunt_locks.py"))

# ── core: connection ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
s = read(conn_path)
assert "HUNT_PIPE_TRACE" in s, "run scripts/hunt_trace_pipes.py first"
s = once(s, """                if not chunk:
                    break
                if b"requestWillBeSent" in chunk and b"/landed" in chunk and os.environ.get("HUNT_PIPE_TRACE"):
""", """                if not chunk:
                    break
                _hunt_t0 = _hunt_h.chunk_begin()
                if b"requestWillBeSent" in chunk and b"/landed" in chunk and os.environ.get("HUNT_PIPE_TRACE"):
""", "core _read_loop (before the stamp)")
s = once(s, """                        print("%.4f" % _ht.time(), file=_hf)
""", """                        print("%.4f" % _ht.time(), file=_hf)
                _hunt_h.on_chunk(chunk, _hunt_t0)
""", "core _read_loop (after the stamp)")
for needle in ("for fn in subscribers:", "fn(method, params, session)", "with self._listeners_lock:", "self.delivered += 1"):
    assert needle in s, "core dispatch_event changed: " + needle
s = s.rstrip(chr(10)) + IMPORT + "_hunt_h.hook_connection(Connection)\n"
write(conn_path, s)
print("patched", conn_path)

# ── core: lifecycle, injected, instance ─────────────────────────────────────────────────────────────────────────────────────────────
path = origin("invisible_core.juggler.lifecycle")
s = read(path)
assert "def _hunt_lc(" in s, "run scripts/hunt_trace_lifecycle.py first"
s = s.rstrip(chr(10)) + IMPORT + '_hunt_h.hook_class(Lifecycle, ["_cv"], "Lifecycle.")\n_hunt_lc = _hunt_h.timed_fn(_hunt_lc, "hunt_lc_write")\n'
write(path, s)
print("patched", path)

path = origin("invisible_core.juggler.injected")
s = read(path)
assert "class InjectedScript" in s
s = s.rstrip(chr(10)) + IMPORT + '_hunt_h.hook_class(InjectedScript, ["_contexts_changed"], "InjectedScript.")\n'
write(path, s)
print("patched", path)

path = origin("invisible_core.juggler.instance")
s = read(path)
assert "class Browser" in s
s = s.rstrip(chr(10)) + IMPORT + '_hunt_h.hook_class(Browser, ["_sessions_ready", "_buffer_lock"], "core.Browser.")\n'
write(path, s)
print("patched", path)

# ── wrapper: server.py ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
path = origin("invisible_playwright._juggler.server")
s = read(path)
s = once(s, "            gate = threading.RLock()\n",
         '            gate = _hunt_h.timed(threading.RLock(), "events_gate", quiet=True)\n', "wrapper gate")
s = once(s, """                got = gate.acquire(timeout=owner.GATE_WAIT_S) if waiting else gate.acquire(blocking=False)
""", """                _hg = (_hunt_h.gate_begin(), _hunt_h.gate_snap(gate))
                got = gate.acquire(timeout=owner.GATE_WAIT_S) if waiting else gate.acquire(blocking=False)
                _hunt_h.gate_end(_hg[0], gate, got, waiting, _hg[1])
""", "wrapper gated_dispatch")
assert "class PageDispatcher(" in s and "class BrowserDispatcher(" in s
s = s.rstrip(chr(10)) + IMPORT + (
    '_hunt_h.hook_class(PageDispatcher, ["_close_lock", "_held_lock", "_frames_lock", "_announce_lock"], "PageDispatcher.")\n'
    '_hunt_h.hook_class(BrowserDispatcher, ["_sessions_ready", "_buffer_lock", "_targets_lock", "_pages_ready"], "BrowserDispatcher.")\n')
write(path, s)
print("patched", path)
