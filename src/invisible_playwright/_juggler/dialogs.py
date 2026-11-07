"""A dialog is answered after the time a reader would take, not at pipe speed.

⛔ WHAT IT REPLACES. `alert`, `confirm` and `prompt` were answered in the same
millisecond they opened: the client's auto-dismiss (no handler registered) and
a handler's own `accept()`/`dismiss()` both sent `Page.handleDialog` the moment
the event reached them. No person closes a dialog before it has been painted,
let alone read; a page timing `alert()`'s return saw a few milliseconds where
every browser in the world shows hundreds. The stock driver does the same, so
this is not a tell of one driver but of all of them, which is exactly why a
site can afford to test for it.

The pause is drawn from the session's seed, like every other rhythm of this
package: one seed, one reader, the same reader on every machine. It has a
reaction part (noticing the dialog, moving to the button) and a reading part
that grows with the words. Both are log-normal, both clamped, and each dialog
of a page draws its own, so two dialogs with the same text do not take the
same time.

The remainder is what is waited: a handler that spent its own time deciding is
not asked to wait again for what already passed.
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import Optional

#: Noticing the dialog and reaching the button: median and spread (log-normal).
REACTION_MEDIAN_MS = 650.0
REACTION_SIGMA = 0.35
#: Reading, per word.
PER_WORD_MEDIAN_MS = 230.0
PER_WORD_SIGMA = 0.30
#: No reader is faster or slower than this.
FLOOR_MS = 350.0
CEILING_MS = 8000.0


def _rng(seed: int, page: int, ordinal: int) -> random.Random:
    """One stream per (seed, page, dialog). Hashed to an int on purpose:
    `random.Random` seeds a tuple through `hash()`, and the hash of a str
    changes from one process to the next."""
    key = f"dialog:{seed}:{page}:{ordinal}".encode()
    return random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))


def _log_normal(rng: random.Random, median: float, sigma: float) -> float:
    return median * math.exp(rng.gauss(0.0, sigma))


def plan_answer_ms(seed: Optional[int], page: int, ordinal: int,
                   message: str) -> float:
    """How long after it opened this dialog is answered, in ms.

    `seed` is None when the session has no rhythm (humanising off): then there
    is no pause, which is what every other seeded rhythm does with None."""
    if seed is None:
        return 0.0
    rng = _rng(seed, page, ordinal)
    words = len(message.split())
    total = _log_normal(rng, REACTION_MEDIAN_MS, REACTION_SIGMA)
    for _ in range(words):
        total += _log_normal(rng, PER_WORD_MEDIAN_MS, PER_WORD_SIGMA)
    return min(CEILING_MS, max(FLOOR_MS, total))


def remaining_ms(planned_ms: float, opened_at: float, now: float) -> float:
    """What is still to wait: the plan minus the time already gone since the
    dialog opened (both clocks in seconds, from the same monotonic source)."""
    elapsed_ms = (now - opened_at) * 1000.0
    return max(0.0, planned_ms - elapsed_ms)
