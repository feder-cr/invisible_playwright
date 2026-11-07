"""A dialog is answered after the time a reader takes, counted from when it opened.

⛔ WHAT IT REPLACES. `Page.handleDialog` left in the same millisecond
`Page.dialogOpened` arrived, whoever answered: the client's auto-dismiss (no
handler registered) or a handler's own `accept()`. A page timing `alert()`'s
return saw single digits of milliseconds where every hand shows hundreds. The
stock driver behaves the same, which is why a site can afford to test for it.

Known-bad: `PageDispatcher.send_async` forwarding every command straight to
`send`, with nothing recorded at `Page.dialogOpened`.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from invisible_playwright._juggler import dialogs
from invisible_playwright._juggler import server as server_mod
from invisible_playwright._juggler.server import PageDispatcher

SEED = 4242


# ── the plan ───────────────────────────────────────────────────────────────

def test_the_pause_is_the_seed_s_and_grows_with_the_words():
    short = dialogs.plan_answer_ms(SEED, 1, 1, "Sure?")
    long = dialogs.plan_answer_ms(SEED, 1, 1,
                                  "Leaving this page will discard the changes "
                                  "you made to the form. Leave anyway?")
    assert short == dialogs.plan_answer_ms(SEED, 1, 1, "Sure?")  # one seed, one reader
    assert dialogs.FLOOR_MS <= short < long <= dialogs.CEILING_MS
    # Another seed, another reader; another dialog of the same page, another draw.
    assert dialogs.plan_answer_ms(SEED + 1, 1, 1, "Sure?") != short
    assert dialogs.plan_answer_ms(SEED, 1, 2, "Sure?") != short


def test_no_seed_means_no_pause():
    """`None` is how every seeded rhythm of this package spells 'off'."""
    assert dialogs.plan_answer_ms(None, 1, 1, "Sure?") == 0.0


def test_only_the_remainder_is_waited():
    assert dialogs.remaining_ms(900.0, opened_at=10.0, now=10.2) == pytest.approx(700.0)
    # A handler that took longer than the plan is not asked to wait again.
    assert dialogs.remaining_ms(900.0, opened_at=10.0, now=12.0) == 0.0


# ── the wiring ─────────────────────────────────────────────────────────────

class _Server:
    """The transport: it runs blocking work, and it knows NO seed. The seed
    lives on the browser (`BrowserDispatcher.session_seed`), reached from a
    page through `page.browser`; 0.46.0's first CI run hung three e2e tests
    because the pause read it from here."""

    async def run_blocking(self, fn: Any, *args: Any) -> Any:
        return fn(*args)


class _Browser:
    def __init__(self, seed: Any) -> None:
        self.session_seed = seed


class _Context:
    def __init__(self, seed: Any) -> None:
        self.browser = _Browser(seed)


def _page(seed: Any, sent: List[Any]) -> PageDispatcher:
    """The dispatcher's dialog half alone: no engine, no client."""
    page = PageDispatcher.__new__(PageDispatcher)
    page.server = _Server()
    page.context = _Context(seed)
    page.number = 1
    page._dialogs_open = {}
    page._dialogs_seen = 0
    page.send = lambda command, params, timeout=None: sent.append((command, params))
    page._emit_fused_dialog = lambda *a: None
    return page


def _open_then_answer(monkeypatch: Any, seed: Any, handler_took_s: float,
                      message: str = "Leave this page?") -> Dict[str, Any]:
    clock = {"now": 100.0}
    slept: List[float] = []
    sent: List[Any] = []
    monkeypatch.setattr(server_mod.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(server_mod.time, "sleep", slept.append)
    page = _page(seed, sent)
    page._on_juggler_event("Page.dialogOpened",
                           {"dialogId": "d1", "type": "confirm", "message": message})
    clock["now"] += handler_took_s
    asyncio.run(page.send_async("Page.handleDialog", {"dialogId": "d1", "accept": True}))
    return {"slept": slept, "sent": sent, "page": page}


def test_the_answer_waits_what_the_reader_still_needs(monkeypatch):
    planned = dialogs.plan_answer_ms(SEED, 1, 1, "Leave this page?")
    r = _open_then_answer(monkeypatch, SEED, handler_took_s=0.0)
    assert r["slept"] == [pytest.approx(planned / 1000.0)]
    assert r["sent"] == [("Page.handleDialog", {"dialogId": "d1", "accept": True})]
    assert r["page"]._dialogs_open == {}  # answered: forgotten


def test_a_slow_handler_is_not_made_slower(monkeypatch):
    r = _open_then_answer(monkeypatch, SEED, handler_took_s=30.0)
    assert r["slept"] == []
    assert len(r["sent"]) == 1


def test_a_session_without_rhythm_answers_at_once(monkeypatch):
    r = _open_then_answer(monkeypatch, None, handler_took_s=0.0)
    assert r["slept"] == []
    assert len(r["sent"]) == 1


def test_the_second_dialog_of_a_page_draws_its_own_pause(monkeypatch):
    clock = {"now": 100.0}
    slept: List[float] = []
    sent: List[Any] = []
    monkeypatch.setattr(server_mod.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(server_mod.time, "sleep", slept.append)
    page = _page(SEED, sent)
    for dialog_id in ("d1", "d2"):
        page._on_juggler_event("Page.dialogOpened",
                               {"dialogId": dialog_id, "type": "alert", "message": "Saved"})
        asyncio.run(page.send_async("Page.handleDialog",
                                    {"dialogId": dialog_id, "accept": True}))
    assert len(slept) == 2 and slept[0] != slept[1]
    assert slept == [dialogs.plan_answer_ms(SEED, 1, n, "Saved") / 1000.0 for n in (1, 2)]


def test_the_answer_goes_out_even_when_the_pause_cannot_be_planned(monkeypatch):
    """A dialog left open blocks the page's script for good. Whatever fails in
    planning the pause is a warning, never a swallowed answer."""
    slept: List[float] = []
    sent: List[Any] = []
    monkeypatch.setattr(server_mod.time, "sleep", slept.append)
    page = _page(SEED, sent)
    page.context = None  # no browser to ask: the planning raises
    page._on_juggler_event("Page.dialogOpened",
                           {"dialogId": "d1", "type": "alert", "message": "Hi"})
    with pytest.warns(RuntimeWarning, match="answering at once"):
        asyncio.run(page.send_async("Page.handleDialog", {"dialogId": "d1", "accept": True}))
    assert slept == [] and sent == [("Page.handleDialog", {"dialogId": "d1", "accept": True})]


def test_a_command_that_is_not_a_dialog_goes_straight_through(monkeypatch):
    slept: List[float] = []
    sent: List[Any] = []
    monkeypatch.setattr(server_mod.time, "sleep", slept.append)
    page = _page(SEED, sent)
    asyncio.run(page.send_async("Page.bringToFront", {}))
    assert slept == [] and sent == [("Page.bringToFront", {})]
