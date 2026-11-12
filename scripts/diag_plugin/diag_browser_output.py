"""A pytest plugin for the diagnosis (never part of the product or of a gate): every line the browser writes on its stdout/stderr pipe is also appended to a file per
launch under $DIAG_OUT. The wrapper keeps only the last 200 lines of that pipe in memory (invisible_core.juggler.connection.ProcessOutput); the children of the browser
(content, socket, GPU processes) write on the same pipe, so a "Sandbox: seccomp sandbox violation: pid N, tid M, syscall S" of any of them is in these files.

  PYTHONPATH=scripts/diag_plugin python -m pytest -p diag_browser_output ...
"""
import itertools
import os
import threading

_counter = itertools.count(1)
_lock = threading.Lock()


def _install():
    out = os.environ.get("DIAG_OUT")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    from invisible_core.juggler import connection

    original = connection.ProcessOutput._pump

    def pump(self):
        with _lock:
            n = next(_counter)
        path = os.path.join(out, "browser-%03d.log" % n)
        stream = self._stream
        real_readline = stream.readline

        class Tee:
            def __getattr__(self, name):
                return getattr(stream, name)

            def readline(self_inner, *a):
                raw = real_readline(*a)
                if raw:
                    with open(path, "ab") as f:
                        f.write(raw)
                return raw

        self._stream = Tee()
        return original(self)

    connection.ProcessOutput._pump = pump


try:
    _install()
except Exception as exc:      # a diagnosis tool never breaks the run it watches
    print('diag_browser_output: not installed: %s' % exc)
