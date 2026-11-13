"""HUNT SCAFFOLDING, NEVER MERGED: stamp the moment the browser pipe delivers a /landed request, in the core and in stock Playwright.

Ours: invisible_core.juggler.connection _read_loop, on every chunk read from the browser. Stock: Playwright's PipeTransport2._dispatch in
the driver's coreBundle.js, on every chunk. A chunk that carries "requestWillBeSent" and "/landed" writes a wall-clock time (seconds) to the
file named by $HUNT_PIPE_TRACE. Run once after the install: python scripts/hunt_trace_pipes.py
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

pw = os.path.dirname(importlib.util.find_spec("playwright").origin)
bundle = os.path.join(pw, "driver", "package", "lib", "coreBundle.js")
s = io.open(bundle, encoding="utf-8").read()
old = r"""      _dispatch(buffer) {
        let end = buffer.indexOf("\0");
"""
new = r"""      _dispatch(buffer) {
        if (process.env.HUNT_PIPE_TRACE && buffer.includes("requestWillBeSent") && buffer.includes("/landed"))
          require("fs").appendFileSync(process.env.HUNT_PIPE_TRACE, (Date.now() / 1000).toFixed(4) + "\n");
        let end = buffer.indexOf("\0");
"""
assert s.count(old) >= 1, "stock _dispatch"  # two NUL-delimited transports in the bundle: both stamped
io.open(bundle, "w", encoding="utf-8", newline=chr(10)).write(s.replace(old, new))
print("patched", bundle)
