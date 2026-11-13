"""HUNT SCAFFOLDING, NEVER MERGED: stamp how long the engine's reader waits for the events gate (BrowserDispatcher.gated_dispatch).

Patches the installed invisible_playwright server module: every wait of 20 ms or more for `gate.acquire` is a line (wall clock, the event) in
$HUNT_THREAD_TRACE, kind "gate wait". Run once after the install: python scripts/hunt_trace_gate.py
"""
import importlib.util
import io

path = importlib.util.find_spec("invisible_playwright._juggler.server").origin
s = io.open(path, encoding="utf-8").read()
old = """                got = gate.acquire(timeout=owner.GATE_WAIT_S) if waiting else gate.acquire(blocking=False)
"""
new = """                _hg0 = time.time()
                got = gate.acquire(timeout=owner.GATE_WAIT_S) if waiting else gate.acquire(blocking=False)
                _hgdt = time.time() - _hg0
                if _hgdt >= 0.02:
                    import json as _hj, os as _ho
                    if _ho.environ.get("HUNT_THREAD_TRACE"):
                        with open(_ho.environ["HUNT_THREAD_TRACE"], "a", encoding="utf-8") as _hf:
                            print(_hj.dumps({"kind": "gate wait", "ev": method, "ms": round(_hgdt * 1000, 1), "t": round(_hg0, 4), "got": bool(got)}), file=_hf)
"""
assert s.count(old) == 1, "gated_dispatch"
io.open(path, "w", encoding="utf-8", newline=chr(10)).write(s.replace(old, new, 1))
print("patched", path)
