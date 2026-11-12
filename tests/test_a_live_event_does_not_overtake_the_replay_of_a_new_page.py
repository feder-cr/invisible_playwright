"""An event the engine sends LIVE while the events a new page missed are being replayed to it does not reach it before them.

NO BROWSER HERE (the real dispatchers over a scripted Juggler connection, as `test_the_hand_over_of_a_popup_holds_under_concurrency`).

The browser buffers the events of a session nobody reads yet, and `PageDispatcher` is handed them (`BrowserDispatcher.replay`) once its
lifecycle and its injected script are subscribed. The replay ran on the adoption thread while the engine's reader went on delivering the
live events to the same subscribers: a popup's destination committed (live) in the middle of the replay of its first events, and the
replay then went on with the OLDER ones - the initial `about:blank` (no navigation of ours) and the `navigationStarted` of the same
navigation. The lifecycle ended at `about:blank` for good (a popup "at about:blank": the client was never told of the destination), or
had the navigation pending again after its own commit ("Execution context was destroyed" in the first evaluate). Measured on Windows with
the engine's events traced (`POPUP-63-SECONDA-CAUSA.md`): the second `Page.frameAttached` of every popup in the traces IS the replay, and
the engine sends each event once - 13 reds in 20 runs of the popup file on engine 63 after the hand-over fix, none of them with a second
`frameAttached` from the engine (`PageHandler._onFrameAttached` answers nothing for a frame it holds live).

Sequence of the test: the first events of a popup are buffered (frame, blank commit, the destination's navigation started) -> the page is
built and the replay starts, stopped after its first event -> the engine's reader delivers the destination's commit -> the replay goes on.
Whatever the interleaving, the lifecycle must end at the destination, with the navigation committed and not pending, and the client's
frame must be told the destination.

Known-bad input: the replay as it was -> the lifecycle ends at `about:blank` and the navigation of the destination is pending again.
"""
from __future__ import annotations

import threading

import pytest

from invisible_playwright._juggler import server as server_mod

from test_a_commit_at_any_point_of_the_hand_over_reaches_the_client_once import DEST
from test_the_hand_over_of_a_popup_holds_under_concurrency import World, _finish
from test_every_order_of_the_events_of_a_popup_gives_one_page_event import FRAME, SESSION, TARGET, _thread, _wait

pytestmark = pytest.mark.unit


def _case(stop_after, scenario="commit"):
    """The replay is stopped after `stop_after` of its events; the live event of the scenario is delivered by another thread (the reader):
    the destination's commit, or (`context`) the creation of a new utility context after the old one was destroyed."""
    parked, go, delivered = threading.Event(), threading.Event(), []
    with pytest.MonkeyPatch.context() as mp:
        original = server_mod.BrowserDispatcher.replay

        def replay(self, session, deliver):
            if session != SESSION:
                return original(self, session, deliver)
            count = [0]

            def slow(method, params, s):
                deliver(method, params, s)
                delivered.append(method)
                count[0] += 1
                if count[0] == stop_after:
                    parked.set()
                    go.wait(10)
            return original(self, session, slow)
        mp.setattr(server_mod.BrowserDispatcher, "replay", replay)
        # the adoption thread is held before the page is built, so that the first events of the popup are buffered when it is
        entered, begin = threading.Event(), threading.Event()
        original_build = server_mod.BrowserDispatcher._enter_building

        def enter_building(self, *args, **kwargs):
            if threading.current_thread().name == "invpw-adopt-%s" % TARGET:
                entered.set()
                begin.wait(10)
            return original_build(self, *args, **kwargs)
        mp.setattr(server_mod.BrowserDispatcher, "_enter_building", enter_building)
        world = World()
        reader = None
        try:
            conn = world.conn
            conn.attach(world.opener_target, world.context.context_id)
            assert entered.wait(5), "the adoption never started"
            conn.frame()
            conn.commit()          # the initial about:blank (navigation n1)
            conn.start()           # the navigation to the destination starts (n2)
            if scenario == "context":
                conn.dispatch_event("Runtime.executionContextDestroyed", {"executionContextId": "eutility"}, SESSION)
            begin.set()            # the page is built now, and the replay hands it those events
            assert parked.wait(5), "the replay never delivered %d event(s): %s" % (stop_after, delivered)
            live = conn.commit_dest if scenario == "commit" else (lambda: conn.dispatch_event("Runtime.executionContextCreated", {
                "executionContextId": "eutility2", "auxData": {"frameId": FRAME, "name": "utility"}}, SESSION))
            reader = threading.Thread(target=live, daemon=True, name="engine-reader")
            reader.start()
            reader.join(0.3)       # with the replay ordered, the reader waits behind it; unordered, it is through
            go.set()
            reader.join(5)
            assert not reader.is_alive(), "the reader never got through"
            conn.ready()
            assert _wait(lambda: _thread() is None), "the adoption thread never ended"
            page = world.browser._page_targets.get(TARGET)
            known = page.lifecycle.frames[FRAME]
            if scenario == "context":
                return page.injected.contexts.get((FRAME, "utility"))
            return known.url, known.navigation, sorted(known.states), page.frame.url
        finally:
            begin.set()
            go.set()
            world.close()


@pytest.mark.parametrize("stop_after", [1, 2, 3, 4, 5])
def test_the_destination_commit_delivered_while_the_replay_goes_on_is_the_last_word(stop_after):
    url, navigation, states, told = _case(stop_after)
    assert url == DEST, "the lifecycle ended at %r: an older event of the replay came after the live commit" % url
    assert navigation == "n2", "the frame's navigation is %r" % navigation
    assert "commit" in states
    assert told == DEST, "the client's frame is at %r" % told


@pytest.mark.parametrize("stop_after", [1, 2])
def test_a_context_created_while_the_replay_goes_on_is_not_undone_by_an_older_event(stop_after):
    """The same race for the injected script: the replay brings the creation of the utility context and its destruction, the engine creates a
    new one meanwhile. The new one is the context the injected script must end with."""
    context = _case(stop_after, "context")
    assert context == "eutility2", "the injected script ended with the utility context %r" % (context,)


def test_a_patch_of_the_class_made_after_the_browser_was_built_still_sees_the_events():
    """The reader looked the delivery up on the class at each event; the gate keeps doing so (the e2e fixtures that record the events patch
    `EventListeners.dispatch_event` after the browser exists)."""
    from invisible_core.juggler.connection import EventListeners

    world = World()
    try:
        seen = []
        original = EventListeners.dispatch_event
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(EventListeners, "dispatch_event", lambda self, method, params, session: seen.append((method, session)))
            world.conn.dispatch_event("Page.x", {}, "S-patched")
        assert seen == [("Page.x", "S-patched")]
        assert EventListeners.dispatch_event is original
    finally:
        world.close()


def test_a_gate_that_is_never_let_go_costs_the_reader_one_wait_and_is_said():
    """The bound is on the hold, not on each event: the first event waits GATE_WAIT_S, the next ones are delivered at once, and the
    connection records it."""
    import time

    world = World()
    held, release, got = threading.Event(), threading.Event(), []
    try:
        world.browser.GATE_WAIT_S = 0.2

        def holder():
            with world.browser.events_gate():
                held.set()
                release.wait(10)
        t = threading.Thread(target=holder, daemon=True)
        t.start()
        assert held.wait(5)
        world.conn.add_listener(lambda m, p, s: got.append(m) if s == "S-stuck" else None)
        began = time.monotonic()
        for n in range(3):
            world.conn.dispatch_event("Page.e%d" % n, {}, "S-stuck")
        took = time.monotonic() - began
        assert got == ["Page.e0", "Page.e1", "Page.e2"], got
        assert took < 1.0, "the reader waited %.2fs for three events: the bound is per event" % took
        assert world.browser._gate_timeouts == 1
        assert any("events gate" in str(e) for e in world.conn.handler_errors), world.conn.handler_errors
    finally:
        release.set()
        world.close()


def test_a_page_built_while_the_reader_is_stuck_in_a_handler_is_built_all_the_same_and_it_is_said():
    import warnings

    world = World()
    held, release = threading.Event(), threading.Event()
    try:
        world.browser.PAGE_GATE_WAIT_S = 0.2

        def stuck_reader():
            with world.browser.events_gate():
                held.set()
                release.wait(10)
        t = threading.Thread(target=stuck_reader, daemon=True)
        t.start()
        assert held.wait(5)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with world.browser.events_gate():
                pass
        assert any("held for more than" in str(w.message) for w in caught), [str(w.message) for w in caught]
    finally:
        release.set()
        world.close()
