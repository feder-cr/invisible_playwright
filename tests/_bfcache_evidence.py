"""What a test that expects a page back from the back-forward cache writes down BEFORE it asserts anything, and the wait that leaves it
a page the cache can take.

The test used to read `window.__shown` after `go_back()` and assert on it with nothing else saved: a red said "the page was loaded again" and
nobody could tell whether the engine had announced a restore (and the page was loaded again anyway), or Firefox had refused or evicted the page.
`read_after_go_back` takes, in one place and before any assertion, what tells them apart:

* `__shown`: `[False, True]` is a `pageshow` with `persisted` after the first one; `[False]` alone is a document that was built again;
* the type of the navigation entry and the `readyState` of the document that answers;
* the protocol events since `go_back()`: a restore is announced by the engine with a navigation id that starts with `nav-bfcache-` (and
  `Page.frameDocumentRestored`); a document that loaded again comes with a `Page.eventFired` `load` and new execution contexts.

`decide_outcome` writes that down FIRST (stdout, which pytest prints with the failure) and then says which of the two outcomes happened:
the page was `restored` from the cache, or the document was built again (`reloaded`). The second is accepted ONLY where Firefox itself may
not use the cache: a navigation the PARENT starts (`Page.navigate`, `goto`, to the same site or to another) is checked by the parent against the
requests in the old document's loadgroup, and the navigation's own DocumentChannel may still be there when the check runs (measured 2026-11-12,
SHIPBFCache log: `AllowedInBFCache` found "requests in the loadgroup" in 15 of 56 goto sessions, never after the page's own navigation). The test then asserts the worker's contract for the outcome that happened, whole, and says which it was.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List

#: The navigation id of a restore: Juggler's FrameTree.announceRestoredDocument gives it `nav-bfcache-<n>`; a document loaded again has the id of its load.
RESTORE_PREFIX = "nav-bfcache-"

#: The protocol events that say what the engine did with the document, and the one parameter that names each.
_SAID = {
    "Page.navigationStarted": "navigationId",
    "Page.navigationCommitted": "navigationId",
    "Page.eventFired": "name",
    "Page.frameDocumentRestored": "frameId",
    "Runtime.executionContextsCleared": None,
    "Runtime.executionContextCreated": None,
    "Page.workerCreated": "workerId",
    "Page.workerDestroyed": "workerId",
}


@dataclass
class Evidence:
    shown: object
    navigation_type: object
    ready_state: object
    events: List[str] = field(default_factory=list)

    @property
    def restored(self) -> bool:
        return self.shown == [False, True]

    @property
    def engine_announced_a_restore(self) -> bool:
        return any(e.startswith("Page.navigationCommitted " + RESTORE_PREFIX) for e in self.events)

    def text(self) -> str:
        return ("[bfcache] after go_back: __shown=%r navigation entry type=%r readyState=%r; the engine announced a restore: %s; events: %s"
                % (self.shown, self.navigation_type, self.ready_state, "yes" if self.engine_announced_a_restore else "no",
                   self.events))

    def why_not(self) -> str:
        if self.engine_announced_a_restore:
            return ("the engine announced a restore (%s...) but the page did not run a pageshow with persisted after its first one"
                    % RESTORE_PREFIX)
        return ("the page was built again instead of restored from the back-forward cache (no restore was announced by the engine), "
                "so this test proves nothing")


def summarize_events(events, start: int) -> List[str]:
    """The events since ``start`` that say what became of the document, one short string each, in order."""
    said = []
    for method, params in events[start:]:
        if method not in _SAID:
            continue
        key = _SAID[method]
        said.append(method if key is None else "%s %s" % (method, params.get(key)) + (
            " %s" % params.get("url") if method == "Page.navigationCommitted" else ""))
    return said


def read_after_go_back(page, events, start: int) -> Evidence:
    """Everything the test knows right after ``go_back()``, taken before any assertion."""
    return Evidence(
        shown=page.evaluate("window.__shown"),
        navigation_type=page.evaluate("() => { const e = performance.getEntriesByType('navigation')[0]; return e ? e.type : null; }"),
        ready_state=page.evaluate("document.readyState"),
        events=summarize_events(events, start),
    )


def decide_outcome(evidence: Evidence, may_reload: bool, write: Callable[[str], None] = print) -> str:
    """Write the evidence down (always, first), then say what happened: ``"restored"``, or ``"reloaded"`` when the document was built again
    and ``may_reload`` (the case where Firefox may refuse the cache); anything else fails, naming why and carrying the evidence. A restore
    the engine announced that the page did not see fails even where a reload is accepted: that is a defect of ours, not of the cache."""
    outcome = "restored" if evidence.restored else (
        "reloaded" if may_reload and not evidence.engine_announced_a_restore else "failed")
    write(evidence.text() + " => outcome: " + outcome)
    assert outcome != "failed", "%s: %s" % (evidence.why_not(), evidence.text())
    return outcome


def wait_cacheable(page, seconds: float = 10.0) -> None:
    """Until every document of the page that records `pageshow` has had it. A document whose `pageshow` has not run is still marked as
    loading and Firefox will not cache it (Document::OnPageShow clears the mark): the lab does not leave before that, however slow the
    machine is. The worker has started and answered before this: the callers asked it twice."""
    def shown() -> bool:
        return all(frame.evaluate("(window.__shown || [0]).length >= 1") for frame in page.frames)

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if shown():
            return
        time.sleep(0.1)
    assert shown(), "a document of the page never ran its pageshow: %r" % [f.url for f in page.frames]
