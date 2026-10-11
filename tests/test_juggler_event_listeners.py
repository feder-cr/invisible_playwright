"""The subscriber registry, and the two ways it used to break.

⛔ THE DEFECT THESE GUARD WAS INVISIBLE TO EVERY GATE THIS PROJECT HAD. Event
delivery used to be a chain of closures - each subscriber captured the previous
`on_event` and installed itself - so one event was delivered by a nested call
per subscriber, and the Python stack depth WAS the number of subscribers.
Nothing unsubscribed, and a page registers three (Lifecycle, InjectedScript,
PageDispatcher), so the chain grew by 3 for every page opened in the life of a
browser. Measured 2026-08-28: page 0 at 4 links, page 325 at 979, and just past
there the default recursion limit of 1000 was crossed. From that moment every
event raised RecursionError into the read loop's handler, which correctly
refuses to die and records the failure in a list nobody reads - so the browser
went on answering commands while delivering no events at all, and `new_page`
timed out after 20s waiting for a session that had already been announced.

Our own e2e opens dozens of pages, not hundreds, so it never came close.
Playwright's own suite hit it at 63% and every one of the ~150 tests after that
point failed identically, which is how it was finally seen.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import InjectedScript
from invisible_core.juggler.lifecycle import Lifecycle


class FakeConnection(EventListeners):
    """The registry itself plus the `send` the subscribers call."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params, session))
        return {}


def test_many_subscribers_do_not_consume_the_python_stack():
    """⛔ THE MUTATION FOR THIS ONE IS THE OLD IMPLEMENTATION.

    Rewriting `dispatch_event` as the chain it replaced - each subscriber
    calling the next - fails here with RecursionError, which is exactly the
    production failure. 5000 is chosen to be five times the default recursion
    limit, so the test cannot pass by the limit happening to be raised.
    """
    c = FakeConnection()
    served = []
    for i in range(5000):
        c.add_listener(lambda m, p, s, i=i: served.append(i))
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    assert len(served) == 5000, (
        "%d subscribers out of 5000 were served: delivery is not flat"
        % len(served))
    assert not c.handler_errors, c.handler_errors[:3]


def test_a_subscriber_that_raises_does_not_cost_the_others_their_event():
    """The chain delivered to the rest as its LAST statement, so one raising
    subscriber silenced everything registered before it - for that event, with
    no error anywhere the caller could see it."""
    c = FakeConnection()
    seen = []

    def explodes(m, p, s):
        raise RuntimeError("boom")

    c.add_listener(explodes)
    c.add_listener(lambda m, p, s: seen.append(m))
    c.dispatch_event("Page.eventFired", {}, "S1")
    assert seen == ["Page.eventFired"], "the raising subscriber took the event"
    assert any("boom" in e for e in c.handler_errors)


def test_registering_the_same_subscriber_twice_delivers_once():
    """Two deliveries of one event announce a frame twice, which reads as the
    browser having sent it twice - a bug in the wrong place entirely."""
    c = FakeConnection()
    seen = []
    fn = lambda m, p, s: seen.append(m)
    c.add_listener(fn)
    c.add_listener(fn)
    c.dispatch_event("Page.frameAttached", {}, "S1")
    assert seen == ["Page.frameAttached"]


def test_removing_a_subscriber_that_was_never_added_is_not_an_error():
    """`announce_closed` runs on two paths and must be safe on both."""
    c = FakeConnection()
    c.remove_listener(lambda m, p, s: None)  # must not raise


@pytest.mark.parametrize("which", ["lifecycle", "injected"])
def test_a_page_subscriber_leaves_the_list_as_it_found_it(which):
    """⛔ THE HALF THAT IS EASY TO FORGET. A flat list still grows without
    bound if nothing ever unsubscribes, and the failure would be slower but
    identical in kind: the memory of every page a long-lived browser opened."""
    c = FakeConnection()
    before = len(c._listeners)
    subscriber = (Lifecycle(c, "S1") if which == "lifecycle"
                  else InjectedScript(c, "S1"))
    assert len(c._listeners) == before + 1, "it did not subscribe at all"
    subscriber.detach()
    assert len(c._listeners) == before, (
        "%s.detach() left its subscriber behind" % which)


def test_detaching_twice_is_harmless():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    v.detach()
    v.detach()
    assert len(c._listeners) == 0


def test_the_page_dispatcher_unsubscribes_all_three_when_the_page_closes():
    """⛔ ASSERTED ON THE CODE, because the three-subscriber cleanup has no
    unit-sized seam: building a real `PageDispatcher` needs a browser. What
    can be asserted here is that the once-guard that announces a close is the
    same one that unsubscribes - putting the removal anywhere else would mean
    two places deciding a page is over, and the one that got missed would leak
    a subscriber per page exactly as before. The behaviour itself is measured
    by `test_a_long_session_does_not_accumulate_subscribers` below, which is
    marked e2e because it needs a browser and forty real pages.
    """
    import inspect
    from invisible_playwright._juggler import server

    source = inspect.getsource(server.PageDispatcher.announce_closed)
    assert "_detach_listeners()" in source, (
        "announce_closed no longer unsubscribes: every closed page now leaks "
        "three subscribers")

    detacher = inspect.getsource(server.PageDispatcher._detach_listeners)
    for name in ("remove_listener", "lifecycle.detach", "injected.detach"):
        assert name in detacher, "_detach_listeners forgot %s" % name


@pytest.mark.e2e
def test_a_long_session_does_not_accumulate_subscribers(firefox_binary):
    """⛔ THE ONE THAT WOULD HAVE CAUGHT IT, and the reason it is worth its
    forty pages: this defect is only visible by ACCUMULATION. Every other gate
    in this project drives a handful of pages and cannot distinguish a
    registry that cleans up from one that does not.

    Forty rather than four hundred: the count is not what proves the point -
    a subscriber list that is flat after forty open-and-close cycles is flat
    after four hundred, while a leaking one is already forty times over by
    then. Four hundred would add three minutes to every e2e run to restate
    the same fact.
    """
    from invisible_playwright._pw.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True,
                                   executable_path=firefox_binary)
        server = browser._impl_obj._connection._transport._server
        conn = next(d.conn for d in list(server._objects.values())
                    if getattr(d, "conn", None) is not None)
        context = browser.new_context()
        page = context.new_page()
        page.close()
        # After one full cycle the list is at its resting length: whatever the
        # browser itself subscribes stays, and the page's three are gone.
        resting = len(conn._listeners)
        # ⛔ TWO REGISTRIES, TWO LEAKS, AND THEY WERE DIFFERENT DEFECTS. The
        # subscriber list grew by three per page and crossed Python's
        # recursion limit; the server's guid registry grew by one, because a
        # closed page was never disposed. Only the first one crashed anything,
        # which is exactly why the second is asserted here rather than left
        # for someone to notice.
        resting_objects = len(server._objects)
        for _ in range(40):
            page = context.new_page()
            page.close()
        leaked = len(conn._listeners) - resting
        leaked_objects = len(server._objects) - resting_objects
        context.close()
        browser.close()

    assert leaked == 0, (
        "forty open-and-close cycles left %d subscribers behind; before the "
        "registry this grew by three per page and crossed Python's recursion "
        "limit at about 330 pages, after which the browser answered commands "
        "and delivered no events at all" % leaked)
    assert leaked_objects == 0, (
        "forty open-and-close cycles left %d objects in the server's guid "
        "registry; a closed page must be disposed, which is also what the "
        "driver does and what diff_protocol's fifth dimension compares"
        % leaked_objects)


def test_a_call_on_a_disposed_object_is_the_error_the_client_swallows():
    """⛔ THE CLASS NAME IS THE CONTRACT, and this is the half that regressed.

    Disposing a closed page - added the same day, to stop the guid registry
    growing - made a perfectly ordinary sequence raise: `context.close()`
    cascades the close to its pages, then a fixture teardown calls
    `page.close()` on one of them. The client is built for exactly that race
    and swallows it, but only when the error arrives named `TargetClosedError`,
    because `_page.py` reads `is_target_closed_error(e)` and re-raises anything
    else. A plain `Error` propagated, and two of Playwright's own tests went
    red on a teardown that had never been a problem.

    ⛔ AND THE OTHER CASE MUST STAY DISTINGUISHABLE. A guid that never existed
    is a defect in this server, not a race, and answering both with
    `TargetClosedError` would make the client swallow our bugs.
    """
    from invisible_playwright._juggler.dispatcher import (
        Dispatcher, ProtocolException, Server, TargetClosedError)

    class Toy(Dispatcher):
        TYPE = "Toy"
        METHODS = {"poke": "op_poke"}

        def op_poke(self, params):
            return {"ok": True}

    server = Server()
    sent = []
    server.send_up = lambda message: sent.append(message)
    toy = Toy(server, None, {})

    assert server.handle({"guid": toy.guid, "method": "poke",
                          "params": {}}) == {"ok": True}

    toy.dispose()
    try:
        server.handle({"guid": toy.guid, "method": "poke", "params": {}})
    except TargetClosedError as failure:
        assert type(failure).__name__ == "TargetClosedError", (
            "the client maps the error CLASS NAME onto its own type; rename "
            "this class and the client stops swallowing the race")
        assert "closed" in str(failure)
    else:
        raise AssertionError("a call on a disposed object did not say so")

    try:
        server.handle({"guid": "toy@never", "method": "poke", "params": {}})
    except TargetClosedError:
        raise AssertionError(
            "a guid that never existed was reported as a closed target, which "
            "is how a defect in this server becomes an exception the client "
            "silently swallows")
    except ProtocolException as failure:
        assert "never created" in str(failure)
