"""A popup's page that is closed (the caller closes the context or the browser) while it is being adopted is not announced to the client afterwards.

NO BROWSER HERE (the real dispatchers over a scripted Juggler connection, as `test_the_hand_over_of_a_popup_holds_under_concurrency`).

The page's guid exists for the client from the moment the page is built (its `__create__`), and the context's `page` event is what tells the client
to treat it as a page. A close that comes in between sends `close` and `__dispose__` for that guid; the adoption then said `page` for it, and the
client threw (an event naming an object it had dropped, the family of `'dict' object has no attribute '_object'`).

Sequence: the adoption thread is parked before the hook goes in -> the page is closed (`announce_closed`) -> the adoption thread goes on to
announce the page. Nothing may be sent for the page, or about it, after its `__dispose__`.

Known-bad input: 436f815 + the fixes of the concurrency review, without the guard in `announce` -> a `page` event after the `__dispose__`.
"""
from __future__ import annotations

import threading

import pytest

from test_the_hand_over_of_a_popup_holds_under_concurrency import World, _adopter, _wait, _thread, POINTS, Parking, _install
from invisible_playwright._juggler import server as server_mod

pytestmark = pytest.mark.unit


def test_a_page_closed_before_its_announcement_is_never_announced():
    parking, told = Parking(), {}
    sent, caught, at_hook, go = [], [], threading.Event(), threading.Event()
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, parking, told)
        original = server_mod.PageDispatcher._hear_lifecycle

        def hear(self):
            if _adopter() and not caught:
                caught.append(self)
                at_hook.set()
                go.wait(10)
            return original(self)
        mp.setattr(server_mod.PageDispatcher, "_hear_lifecycle", hear)
        world = World()
        mp.setattr(world.server, "send_up", lambda message: sent.append(message))
        try:
            world.born()
            for i in range(3):
                assert parking.reached[i].wait(5), "the adoption thread never reached %s" % POINTS[i]
                parking.release[i].set()
            assert at_hook.wait(5), "the adoption thread never reached the hook"
            page = caught[0]
            page.announce_closed()
            disposed_at = next(i for i, m in enumerate(sent) if m.get("method") == "__dispose__" and m.get("guid") == page.guid)
            go.set()
            parking.open_all()
            assert _wait(lambda: _thread() is None), "the adoption thread never ended"
            after = [m for m in sent[disposed_at + 1:] if m.get("method") in ("page", "__create__")
                     or m.get("params", {}).get("page", {}).get("guid") == page.guid]
            assert not after, "told after the page's __dispose__: %s" % [(m.get("method"), m.get("guid")) for m in after]
        finally:
            go.set()
            parking.open_all()
            world.close()
