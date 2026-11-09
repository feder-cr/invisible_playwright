"""Two opt-in switches of the served browser, both OFF unless the environment says so.

  INVPW_MEM_LOG=<file>      a memory probe: one JSON line every INVPW_MEM_LOG_INTERVAL
                            seconds (default 2) and one at each context close.
  INVPW_MALLOC_TRIM=1       `malloc_trim(0)` (glibc, Linux only) after each message of
                            TRIM_BYTES or more has been sent, and at each context close.

NOTHING HERE RUNS WITH THE TWO OFF: the endpoint asks `probe_from_env` and
`trimmer_from_env` once, gets None, and every hook on the hot path is a test of
that None. `ctypes` is imported only by a trimmer or a probe that needs it.

The line of the probe (`t` is `time.monotonic()`, `event` is "tick" or
"context_closed"):
  t, event, uss_mb, rss_mb         null where the platform gives neither (psutil is
                                   optional; Linux falls back to /proc/self/statm for rss)
  registry_objects, contexts       the guid registry, and the BrowserContext among it
  clients, outbox_total_bytes,     what is queued for sockets and not yet sent: the sum of
  outbox_max_messages              the texts, and the longest queue of one client
  gc_counts                        {"generations": gc.get_count(), "objects": len(gc.get_objects())}
  malloc                           glibc `mallinfo2` {arena, uordblks, fordblks, hblkhd}, else null
"""
from __future__ import annotations

import gc
import json
import os
import sys
import threading
import time
import warnings
from typing import Any, Callable, Dict, Optional

#: A message of at least this many bytes is a reply with a body worth giving back to the OS.
TRIM_BYTES = 64 * 1024

DEFAULT_INTERVAL = 2.0


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _load_libc() -> Any:
    import ctypes
    return ctypes.CDLL("libc.so.6", use_errno=True)


_WARNED = False


def trimmer_from_env(environ: Optional[Dict[str, str]] = None) -> Optional[Callable[[], None]]:
    """`malloc_trim(0)` as a callable, or None: off (the default), not Linux
    (said once), or no such function in the libc."""
    global _WARNED
    environ = os.environ if environ is None else environ
    if environ.get("INVPW_MALLOC_TRIM") != "1":
        return None
    if not _is_linux():
        if not _WARNED:
            _WARNED = True
            warnings.warn("INVPW_MALLOC_TRIM=1 is ignored: malloc_trim is glibc on Linux",
                          RuntimeWarning, stacklevel=2)
        return None
    try:
        libc = _load_libc()
        trim = libc.malloc_trim
    except (OSError, AttributeError):
        return None
    return lambda: trim(0)


def _mallinfo() -> Optional[Dict[str, int]]:
    if not _is_linux():
        return None
    try:
        import ctypes
        libc = _load_libc()
        names = ("arena", "ordblks", "smblks", "hblks", "hblkhd",
                 "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")

        class Info(ctypes.Structure):
            _fields_ = [(n, ctypes.c_size_t) for n in names]

        libc.mallinfo2.restype = Info
        info = libc.mallinfo2()
        return {k: int(getattr(info, k)) for k in ("arena", "uordblks", "fordblks", "hblkhd")}
    except Exception:
        return None


def _memory_mb() -> "tuple[Optional[float], Optional[float]]":
    try:
        import psutil
        info = psutil.Process().memory_full_info()
        return round(info.uss / 1048576, 2), round(info.rss / 1048576, 2)
    except Exception:
        pass
    rss = None
    if _is_linux():
        try:
            with open("/proc/self/statm") as statm:
                rss = round(int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576, 2)
        except Exception:
            rss = None
    return None, rss


class MemProbe:
    """Writes the lines; `sample` is also what the tests read."""

    def __init__(self, endpoint: Any, path: str, interval: float = DEFAULT_INTERVAL) -> None:
        self.endpoint = endpoint
        self.path = path
        self.interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="invpw-mem-probe")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.write("tick")
            self._stop.wait(self.interval)

    @staticmethod
    def outbox_of(client: Any) -> "tuple[int, int]":
        """(bytes, messages) queued for one client: read from the queue's own deque, so
        sending costs nothing for it. The message the writer holds in `send` is not here."""
        try:
            queued = list(client._outbox._queue)
        except Exception:
            return 0, 0
        return sum(len(t) for t in queued if t), len(queued)

    def sample(self, event: str) -> Dict[str, Any]:
        endpoint = self.endpoint
        with endpoint._lock:
            clients = list(endpoint.clients)
        sizes = [self.outbox_of(c) for c in clients]
        uss, rss = _memory_mb()
        with endpoint.server._lock:
            objects = list(endpoint.server._objects.values())
        return {
            "t": round(time.monotonic(), 3), "event": event,
            "uss_mb": uss, "rss_mb": rss,
            "registry_objects": len(objects),
            "contexts": sum(1 for o in objects if o.TYPE == "BrowserContext"),
            "clients": len(clients),
            "outbox_total_bytes": sum(b for b, _ in sizes),
            "outbox_max_messages": max((m for _, m in sizes), default=0),
            "gc_counts": {"generations": list(gc.get_count()), "objects": len(gc.get_objects())},
            "malloc": _mallinfo(),
        }

    def write(self, event: str) -> None:
        try:
            line = json.dumps(self.sample(event))
            with self._lock, open(self.path, "a", encoding="utf-8") as out:
                out.write(line + "\n")
        except Exception:
            pass  # a probe never takes the server down


def probe_from_env(endpoint: Any, environ: Optional[Dict[str, str]] = None) -> Optional[MemProbe]:
    environ = os.environ if environ is None else environ
    path = environ.get("INVPW_MEM_LOG")
    if not path:
        return None
    try:
        interval = float(environ.get("INVPW_MEM_LOG_INTERVAL") or DEFAULT_INTERVAL)
    except ValueError:
        interval = DEFAULT_INTERVAL
    return MemProbe(endpoint, path, interval)
