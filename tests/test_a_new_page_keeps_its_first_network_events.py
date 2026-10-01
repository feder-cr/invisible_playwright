"""A page's events from before its channel existed are delivered once, in order.

A page the site opens has usually requested and been answered its document
before its Page object exists, so the page holds its own events while it is
built and hands its Network events on once it can emit them. One that arrived
between the page subscribing and the browser's replay marking the session live
is held twice - live, and again from the browser's buffer - and the second
copy sits behind events older than it. These pin the hand-over without a
browser; the e2e file next door holds the outcome against a real one.
"""
from __future__ import annotations

import threading

import pytest

from invisible_playwright._juggler.server import PageDispatcher


def _page(held):
    page = PageDispatcher.__new__(PageDispatcher)
    page.session = "S1"
    page._held = list(held)
    page._held_lock = threading.Lock()
    page.got = []
    page._on_juggler_event = lambda m, p: page.got.append((m, p["n"]))
    return page


def _drain(page):
    # `_install_events` is the hand-over; `conn` is not touched by it any more.
    PageDispatcher._install_events(page)


@pytest.mark.unit
def test_a_twice_held_event_is_delivered_once_at_its_last_place():
    older = {"n": 1}
    racing = {"n": 2}
    later = {"n": 3}
    page = _page([("Network.responseReceived", racing),
                  ("Network.requestWillBeSent", older),
                  ("Network.responseReceived", racing),
                  ("Network.requestFinished", later)])
    _drain(page)
    assert page.got == [("Network.requestWillBeSent", 1),
                        ("Network.responseReceived", 2),
                        ("Network.requestFinished", 3)]


@pytest.mark.unit
def test_only_network_events_are_handed_on_and_holding_stops():
    page = _page([("Page.frameAttached", {"n": 1}),
                  ("Network.requestWillBeSent", {"n": 2})])
    _drain(page)
    assert page.got == [("Network.requestWillBeSent", 2)]
    assert page._held is None
    PageDispatcher._route_juggler_event(page, "Network.requestFinished", {"n": 3}, "S1")
    PageDispatcher._route_juggler_event(page, "Network.requestFinished", {"n": 4}, "S2")
    assert page.got[-1] == ("Network.requestFinished", 3) and len(page.got) == 2


@pytest.mark.unit
def test_while_building_events_are_held_not_handled():
    page = _page([])
    PageDispatcher._route_juggler_event(page, "Network.requestWillBeSent", {"n": 1}, "S1")
    assert page.got == [] and len(page._held) == 1
