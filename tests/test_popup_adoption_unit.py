"""White-box unit tests for popup adoption (no browser).

These pin the concurrency contracts of `_juggler/server.py` deterministically:
hammering from N threads must behave exactly like one call. They run in the
default (non-e2e) suite.
"""
from __future__ import annotations

import threading
import types

import pytest

from invisible_playwright._juggler.server import PageDispatcher

pytestmark = pytest.mark.unit


class _FakeServer:
    """Just enough of `Server` for `announce_closed`/`dispose`."""

    def __init__(self):
        self.sent = []
        self._lock = threading.Lock()

    def send_up(self, message):
        with self._lock:
            self.sent.append(message)

    def unregister(self, _obj):
        pass

    def descendants_of(self, _obj):
        return []


def _bare_page():
    """A `PageDispatcher` without a browser: only the close path is wired."""
    page = PageDispatcher.__new__(PageDispatcher)
    page.server = _FakeServer()
    page.guid = "page@test"
    page.parent = None
    page._close_lock = threading.Lock()
    page._announced_closed = False
    page.disposed = False
    page._detach_listeners = lambda: None
    page.ended = []
    page.context = types.SimpleNamespace(browser=types.SimpleNamespace(
        _page_over=page.ended.append))
    return page


def test_announce_closed_holds_lock_and_emits_close_once():
    """The once-guard runs serialized: the check, the set and the `close`
    emission all happen under `_close_lock`, and a second call (the shape of
    `op_close` racing the engine's detach for the same target) emits nothing.

    Timing-based reproduction was tried first and discarded: 32 threads over
    a barrier, 50 rounds, 3 runs, switch interval at 1 us - zero doubles on
    the unlocked code, because CPython holds the GIL across the 6-bytecode
    check-then-set. The race is real (four call sites on three threads) but
    not winnable on purpose from here, so this pins the mechanism instead:
    on the unfixed code there is no `_close_lock` at all and this fails."""
    page = _bare_page()
    real_lock = page._close_lock
    held = []

    class _RecLock:
        def __enter__(self):
            held.append(True)
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    page._close_lock = _RecLock()
    page.announce_closed()
    page.announce_closed()
    closes = [m for m in page.server.sent if m.get("method") == "close"]
    assert len(closes) == 1, f"expected exactly one close, got {len(closes)}"
    assert held, "announce_closed did not run under _close_lock"
    assert page.ended == [page], (
        "the browser's registries must be left exactly once, from the "
        "once-guard, not by each caller of announce_closed")
