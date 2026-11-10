"""INVPW_GC_EVERY_NAV=N: `Heap.collectGarbage` every N-th main-frame navigation, off by default.

No browser. Known-bad: with the option and the module missing (the code before it) every test
here fails on import; a send made from the caller's thread fails the thread test (it would be
a deadlock in the reader thread); queueing the round that fell due during a running one fails
the skip test; an exception out of the send fails the failure test; counting a sub-frame's
commit fails the frame test.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle
from invisible_playwright._juggler import navgc
from invisible_playwright._juggler.dispatcher import Dispatcher
from invisible_playwright._juggler.server import (
    FrameDispatcher,
    JugglerServer,
    PageDispatcher,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", [None, "", "0", "-3", "abc", "  ", "1.5"])
def test_off_when_absent_empty_zero_negative_or_not_a_number(value):
    environ = {} if value is None else {navgc.ENV: value}
    assert navgc.from_env(Mock(), environ) is None


def test_on_for_a_positive_integer():
    gc = navgc.from_env(Mock(), {navgc.ENV: "3"})
    assert gc is not None and gc.every == 3


def test_every_third_commit_collects_from_a_thread_of_its_own():
    calls = []

    def send(method, params, timeout=None):
        calls.append((method, params, threading.current_thread(), timeout))

    gc = navgc.NavigationGc(3, send)
    for _ in range(7):
        gc.note_main_frame_commit()
        gc.join(5)
    assert [c[0] for c in calls] == ["Heap.collectGarbage"] * 2
    assert gc.commits == 7 and gc.started == 2 and gc.skipped == 0 and gc.failed == 0
    for _, params, thread, timeout in calls:
        assert thread is not threading.current_thread()
        assert params == {}
        assert timeout is not None and timeout > 0


def test_a_round_that_falls_due_while_one_runs_is_skipped_not_queued():
    entered, release = threading.Event(), threading.Event()
    sent = []

    def send(method, params, timeout=None):
        sent.append(method)
        entered.set()
        release.wait(10)

    gc = navgc.NavigationGc(2, send)
    gc.note_main_frame_commit()
    gc.note_main_frame_commit()          # round 1 starts and blocks
    assert entered.wait(5)
    gc.note_main_frame_commit()
    gc.note_main_frame_commit()          # round 2 falls due: skipped
    assert gc.skipped == 1 and gc.started == 1
    release.set()
    gc.join(5)
    assert sent == ["Heap.collectGarbage"]   # nothing was queued behind it
    entered.clear()
    gc.note_main_frame_commit()
    gc.note_main_frame_commit()          # round 3 starts
    gc.join(5)
    assert sent == ["Heap.collectGarbage"] * 2 and gc.started == 2 and gc.skipped == 1


@pytest.mark.parametrize("error", [RuntimeError("Target closed"), TimeoutError("slow"),
                                   ConnectionError("gone")])
def test_a_failing_send_is_swallowed_and_counted(error):
    def send(method, params, timeout=None):
        raise error

    gc = navgc.NavigationGc(1, send)
    gc.note_main_frame_commit()
    gc.join(5)
    gc.note_main_frame_commit()          # the failure freed the slot
    gc.join(5)
    assert gc.failed == 2 and gc.started == 2 and gc.skipped == 0


def test_off_sends_nothing_and_makes_no_thread(monkeypatch):
    server = JugglerServer()
    assert server.nav_gc is None
    before = threading.active_count()
    page = make_page(server)
    page.lifecycle.announce = page._on_lifecycle
    page._on_lifecycle("commit", "main", "https://a.test/")
    assert threading.active_count() == before


def make_page(server):
    server.attach(SimpleNamespace(emit_message=lambda m: None))
    context = Dispatcher(server, None)
    context.options = {}
    page = object.__new__(PageDispatcher)
    page.context = context
    page.lifecycle = Lifecycle(EventListeners(), "session")
    page.injected = Mock()
    page.actions = Mock()
    page._frames = {}
    page._navigation_requests = {}
    page._announced_closed = False
    Dispatcher.__init__(page, server, context)
    page.frame = FrameDispatcher(server, page, "main")
    for fid, parent in (("main", None), ("sub", "main")):
        page.lifecycle._on_event("Page.frameAttached", {"frameId": fid, "parentFrameId": parent})
    return page


def test_only_a_main_frame_commit_counts():
    server = JugglerServer()
    server.nav_gc = navgc.NavigationGc(1000, Mock())
    page = make_page(server)
    page._on_lifecycle("commit", "sub", "https://a.test/frame")
    page._on_lifecycle("commit", "sub", "https://a.test/frame2")
    assert server.nav_gc.commits == 0
    page._on_lifecycle("commit", "main", "https://a.test/")
    assert server.nav_gc.commits == 1
    page._on_lifecycle("commit", "sub", "https://a.test/frame3")
    assert server.nav_gc.commits == 1


def test_a_closed_page_does_not_count():
    server = JugglerServer()
    server.nav_gc = navgc.NavigationGc(1000, Mock())
    page = make_page(server)
    page._announced_closed = True
    page._on_lifecycle("commit", "main", "https://a.test/")
    assert server.nav_gc.commits == 0


def test_a_collection_is_sent_with_the_session_of_the_page_that_committed():
    # Juggler answers Heap.collectGarbage from its PAGE handler: sent with the browser's connection it fails
    # with 'Handler for does not implement method "Heap.collectGarbage"' (measured on the engine).
    server = JugglerServer()
    server.nav_gc = navgc.NavigationGc(1)
    page = make_page(server)
    page.send = Mock()
    page._on_lifecycle("commit", "main", "https://a.test/")
    server.nav_gc.join(5)
    page.send.assert_called_once_with("Heap.collectGarbage", {}, timeout=navgc.TIMEOUT_S)
    assert (server.nav_gc.started, server.nav_gc.failed) == (1, 0)


def test_no_session_to_send_with_is_a_counted_failure_not_an_error():
    gc = navgc.NavigationGc(1)
    gc.note_main_frame_commit()
    gc.join(5)
    assert (gc.started, gc.failed) == (1, 1)


def test_the_endpoint_builds_it_once_from_the_environment(monkeypatch):
    from invisible_playwright._juggler import remote

    def endpoint():
        server = remote._SharedServer()
        browser_type = remote.SharedBrowserType(server)
        browser = Dispatcher(server, browser_type)
        browser.conn = Mock()
        return remote.RemoteEndpoint(server, browser_type, browser), server, browser

    monkeypatch.delenv(navgc.ENV, raising=False)
    ep, server, _ = endpoint()
    assert ep.nav_gc is None and server.nav_gc is None
    monkeypatch.setenv(navgc.ENV, "1")
    ep, server, browser = endpoint()
    assert ep.nav_gc is not None and server.nav_gc is ep.nav_gc
    browser.conn.send.assert_not_called()  # the browser-level handler cannot take the command


def test_page_request_gc_is_sent_with_the_page_session_not_the_browser_connection():
    # Same defect as the option's first version: page.request_gc() failed on the engine with
    # 'Handler for does not implement method "Heap.collectGarbage"' because it used the browser connection.
    page = make_page(JugglerServer())
    page.send = Mock()
    page.context.browser = SimpleNamespace(conn=Mock())
    assert page.op_request_gc({}) is None
    page.send.assert_called_once_with("Heap.collectGarbage", {}, timeout=None)
    page.context.browser.conn.send.assert_not_called()
