"""A garbage collection of the served browser every N main-frame navigations: OFF unless asked.

INVPW_GC_EVERY_NAV=N (a positive integer; absent, empty, 0, negative or not a number is
off). Every N-th commit of a MAIN frame, counted over the whole served browser and not per
page, the server sends Juggler's `Heap.collectGarbage` (GC and cycle collection of the
parent process and its children). Nothing a page can read changes; only timing does.

⛔ NEVER FROM THE CALLER'S THREAD. `note_main_frame_commit` is called from the connection's
reader thread, under the lifecycle's lock, and the reply to a blocking send can only be
delivered by that same thread: a send from there is a deadlock. The send runs on a daemon
thread of its own, with at most ONE collection in flight; a round that falls due while one
runs is skipped and counted (`skipped`), not queued. Every error (browser closed, timeout,
connection gone) is swallowed and counted (`failed`): a collection never fails or delays a
navigation.

Same convention as `memprobe.py`: `from_env` gives None when off, so the hot path is a test
of None.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

#: A collection of a large browser is slow; this only ends a send that will never answer.
TIMEOUT_S = 120.0

ENV = "INVPW_GC_EVERY_NAV"


class NavigationGc:
    """`send(method, params, timeout)` is a blocking command call of a PAGE session: Juggler answers
    `Heap.collectGarbage` from its page handler, and the browser-level handler refuses it ("does not
    implement method"). So the page that committed hands its own `send` to `note_main_frame_commit`; the one
    given here is only the default of a caller that has none."""

    def __init__(self, every: int, send: Optional[Callable[..., Any]] = None) -> None:
        self.every = every
        self._send = send
        self._lock = threading.Lock()
        self._running: Optional[threading.Thread] = None
        self.commits = 0
        self.started = 0
        self.skipped = 0
        self.failed = 0

    def note_main_frame_commit(self, send: Optional[Callable[..., Any]] = None) -> None:
        with self._lock:
            self.commits += 1
            if self.commits % self.every:
                return
            if self._running is not None:
                self.skipped += 1
                return
            thread = threading.Thread(target=self._collect, args=(send or self._send,), daemon=True,
                                      name="invpw-nav-gc")
            self._running = thread
            self.started += 1
        try:
            thread.start()
        except Exception:
            with self._lock:
                self._running = None
                self.failed += 1

    def _collect(self, send: Optional[Callable[..., Any]]) -> None:
        try:
            if send is None:
                raise RuntimeError("no page session to send the collection with")
            send("Heap.collectGarbage", {}, timeout=TIMEOUT_S)
        except BaseException as exc:  # a collection never takes anything down
            with self._lock:
                self.failed += 1
            log.debug("Heap.collectGarbage failed: %r", exc)
        finally:
            with self._lock:
                self._running = None

    def join(self, timeout: Optional[float] = None) -> None:
        """Waits for the collection in flight, if any (for the tests and the measures)."""
        with self._lock:
            thread = self._running
        if thread is not None:
            thread.join(timeout)


def from_env(send: Optional[Callable[..., Any]] = None,
             environ: Optional[Dict[str, str]] = None) -> Optional[NavigationGc]:
    environ = os.environ if environ is None else environ
    try:
        every = int((environ.get(ENV) or "").strip())
    except ValueError:
        return None
    if every <= 0:
        return None
    return NavigationGc(every, send)
