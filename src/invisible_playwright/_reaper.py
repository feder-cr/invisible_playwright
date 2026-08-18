"""Make the browser tree die when this process does.

THIS MODULE IS NOW A RE-EXPORT. The implementation lives in
``invisible_core.process``. It went there to be shared with the profile manager,
which launched the same browser and had exactly the same bug - and had it a day
longer, because the fix was in this repository. That repository was deleted on
2026-08-18; the implementation stays in the core, where this package imports it
from.

Keeping the module rather than deleting it is deliberate: the names below are
imported by `launcher`, by `async_api` and by `tests/test_reaper.py`, and the
published wrapper reads `INVPW_SESSION_TOKEN` from a process environment, so the
token spelling is a compatibility surface that outlives any refactor here.

WHAT MOVING IT FIXED, beyond the duplication. The two copies had already drifted:
the core's version records only SUCCESSFUL job assignments, while this one added
a pid to ``_adopted`` whether the assignment succeeded or not - so a refused
process was never retried and the returned count was a number of attempts. On the
manager that reported eight processes adopted out of eight while eight survived
the kill. This package shipped that same accounting until 0.4.4.

The core's version also carries ``spawn_into``: create the process suspended,
assign it to the job while it is still alone, then resume, so the whole tree is
BORN inside the job. That exists because adopting afterwards cannot work for this
browser - ``AssignProcessToJobObject`` refuses six of eight processes with
ACCESS_DENIED, since Firefox has already placed its content processes in its own
sandbox jobs. This package does not need it today (Playwright spawns the browser
and the adoption path holds there), but it is one implementation now, so the next
person does not have to discover that twice.

The full write-up - the bug, the token design, and why identification must be
positive - is the module docstring of ``invisible_core.process``.
"""
from __future__ import annotations

from invisible_core.process import (  # noqa: F401  (re-export surface)
    JobObjectGuard,
    LifetimeGuard,
    NullGuard,
    SessionToken,
    TOKEN_VAR,
    alive,
    find_processes,
    guard_for,
    terminate,
    wait_until_gone,
)

# psutil is re-exported because `tests/test_reaper.py` monkeypatches
# `_reaper.psutil` to drive the unavailable-mechanism path. It is the same object
# the core holds; patching it here would NOT affect the core, so the tests that
# do that were moved with the implementation.
from invisible_core.process import psutil  # noqa: F401

__all__ = [
    "JobObjectGuard",
    "LifetimeGuard",
    "NullGuard",
    "SessionToken",
    "TOKEN_VAR",
    "alive",
    "find_processes",
    "guard_for",
    "terminate",
    "wait_until_gone",
]
