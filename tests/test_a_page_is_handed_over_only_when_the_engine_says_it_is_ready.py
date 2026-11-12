"""A page is handed over to the client only once the engine says Page.ready.

NO BROWSER HERE: the real dispatchers over a scripted Juggler connection (the one of the registry test) whose pages announce their
frame at birth and say `Page.ready` only when the test tells them to, as the real engine does after its transient about:blank
navigations.

⛔ THE KNOWN-BAD INPUT. The engine commits the page's initial about:blank AFTER it announces the frame; a `goto` sent in between is
'interrupted by another navigation to about:blank'. A wrapper that hands the page over on the main frame alone (no wait for
Page.ready) returns `new_page()` while the engine is still in that window; so does the adoption of a popup.

Known-bad: with the wait for Page.ready removed (a21e1de), the first two tests fail - the page is delivered before Page.ready; with
the wait INSIDE the constructor (7db28e4..6a60dce), the last two fail - a popup opened by a link has its first request held by a
route until its page answers, so a page that is only built once it is ready never becomes ready, and what the engine says between
the commit and the ready (the commit itself, the `load`) is said to a page that does not exist yet and is lost; the client's popup
then has no `load` to wait for. The page is BUILT at once and ANNOUNCED when ready.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_playwright._juggler import remote
from invisible_playwright._juggler.server import FrameDispatcher, LocalUtilsDispatcher

from test_a_closed_context_leaves_nothing_in_the_registry import ScriptedJuggler

pytestmark = pytest.mark.unit

#: how long a page that must NOT be delivered yet is watched
WATCH = 0.4


class LateReadyJuggler(ScriptedJuggler):
    """The engine of the registry test, except that Page.ready is held back until `say_ready(session)`."""

    def __init__(self):
        super().__init__()
        self.held = {}
        self.released = set()
        self.queued = []

    def dispatch_event(self, method, params, session=None):
        if method == "Page.ready" and session not in self.released:
            self.held[session] = (method, params)
            return None
        return super().dispatch_event(method, params, session)

    def send(self, method, params=None, session=None, timeout=30):
        if method == "Browser.getInfo":
            # a reply comes after everything the engine sent before it: the events queued as "sent with the ready" arrive first
            queued, self.queued = self.queued, []
            for event, event_params, event_session in queued:
                super().dispatch_event(event, event_params, event_session)
            return {}
        return super().send(method, params, session, timeout)

    def say_ready(self, session):
        self.released = set(self.released) | {session}
        method, params = self.held.pop(session)
        super().dispatch_event(method, params, session)

    def popup_born(self, opener_target, context_id, n):
        """A target the opener opened: attached and framed, but not ready."""
        target, session = "p%d" % n, "ps%d" % n
        self.sessions[target] = session
        self.dispatch_event("Browser.attachedToTarget", {
            "sessionId": session,
            "targetInfo": {"targetId": target, "type": "page", "openerId": opener_target,
                           "browserContextId": context_id}}, None)
        self.dispatch_event("Page.frameAttached", {"frameId": "f" + target, "parentFrameId": None}, session)
        for world in ("", "utility"):
            self.dispatch_event("Runtime.executionContextCreated", {
                "executionContextId": "e%s%s" % (target, world),
                "auxData": {"frameId": "f" + target, "name": world}}, session)
        self.dispatch_event("Page.ready", {}, session)
        return target, session


@pytest.fixture
def late_browser():
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    conn = LateReadyJuggler()
    browser = remote.SharedBrowserDispatcher(server, browser_type, conn, "firefox/1")
    yield browser
    for session in list(conn.held):
        conn.say_ready(session)
    time.sleep(0.1)
    for context in list(browser.contexts):
        try:
            server.handle({"guid": context.guid, "method": "close", "params": {}})
        except Exception:
            pass
    server.shutdown()


def _new_context(browser):
    server = browser.server
    return server.object(server.handle(
        {"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])


def test_new_page_returns_only_after_page_ready(late_browser):
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    answer = {}

    def new_page():
        answer["reply"] = server.handle(
            {"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})

    worker = threading.Thread(target=new_page, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not conn.held and time.monotonic() < deadline:
        time.sleep(0.01)
    assert conn.held, "the page was never born in the scripted engine"
    worker.join(WATCH)
    assert worker.is_alive() and "reply" not in answer, (
        "new_page() returned before the engine said Page.ready: the page would be handed over inside the window where a goto is "
        "interrupted by the engine's own about:blank")
    assert not context.pages, "the page was registered before it was ready"
    (session,) = conn.held
    conn.say_ready(session)
    worker.join(5)
    assert not worker.is_alive() and "reply" in answer, "new_page() did not return after Page.ready"
    assert len(context.pages) == 1


def test_a_popup_is_announced_only_after_page_ready(late_browser):
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    # the opener: ready at once
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    assert len(context.pages) == 1
    opener_target = next(iter(conn.sessions))
    announced = []
    original = context.emit

    def spy(event, params=None):
        if event == "page":
            announced.append(time.monotonic())
        return original(event, params)

    context.emit = spy
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    time.sleep(WATCH)
    assert not announced and len(context.pages) == 1, (
        "the popup was announced before the engine said Page.ready for it")
    conn.say_ready(session)
    deadline = time.monotonic() + 5
    while not announced and time.monotonic() < deadline:
        time.sleep(0.01)
    assert announced, "the popup was never announced after Page.ready"
    assert len(context.pages) == 2


def _unannounced_page(browser, session):
    return next(o for o in browser.server._objects.values()
                if o.TYPE == "Page" and getattr(o, "session", None) == session)


def test_a_popup_exists_and_listens_while_it_waits_for_its_ready(late_browser):
    """The first request of a popup opened by a link is held by a route until its page answers: the page must be built to answer."""
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    deadline = time.monotonic() + 5
    page = None
    while page is None and time.monotonic() < deadline:
        try:
            page = _unannounced_page(late_browser, session)
        except StopIteration:
            time.sleep(0.01)
    assert page is not None, "the popup was not built while the engine had not said Page.ready: nothing answers its first request"
    time.sleep(0.2)
    assert page._held is None, "the popup is built but does not listen: the events of its birth are held"
    assert len(context.pages) == 1, "and it was announced before it was ready"


def test_what_a_popup_does_before_its_ready_reaches_its_frame(late_browser, monkeypatch):
    """The engine says Page.ready at the commit of the popup's first document; the `load` follows at once. Both reach the client."""
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    said = []
    original = FrameDispatcher.emit

    def spy(self, event, params=None):
        said.append((self.frame_id, event, params))
        return original(self, event, params)

    monkeypatch.setattr(FrameDispatcher, "emit", spy)
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    frame_id = "f" + target
    url = "http://127.0.0.1/popup"
    conn.dispatch_event("Page.navigationStarted", {"frameId": frame_id, "navigationId": "n1", "url": url}, session)
    conn.dispatch_event("Page.navigationCommitted", {"frameId": frame_id, "navigationId": "n1", "url": url, "name": ""}, session)
    conn.dispatch_event("Page.eventFired", {"frameId": frame_id, "name": "DOMContentLoaded"}, session)
    conn.dispatch_event("Page.eventFired", {"frameId": frame_id, "name": "load"}, session)
    conn.say_ready(session)
    deadline = time.monotonic() + 5
    while len(context.pages) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(context.pages) == 2, "the popup was never announced"
    popup = context.pages[1]
    assert popup.frame.url == url, "the frame of the popup was told about:blank: its commit was said to a page that did not exist"
    heard = set(popup.frame.initializer["loadStates"])
    heard |= {params["add"] for fid, event, params in said
              if fid == frame_id and event == "loadstate" and params and "add" in params}
    assert "load" in heard, ("the client's frame never heard `load` of the popup (heard %s): wait_for_url(until=load) would wait for ever"
                             % sorted(heard))


def test_a_popup_waits_for_its_ready_without_a_limit_and_a_requested_page_for_twenty_seconds(late_browser, monkeypatch):
    """Playwright's popup is reported on Page.ready with no limit (a destination that answers after 25 s gives the event at 25 s); the
    page a caller asked for waits 20 s and fails."""
    from invisible_playwright._juggler.server import PageDispatcher
    waited = []
    original = PageDispatcher.wait_until_ready

    def spy(self, timeout=20.0):
        waited.append(timeout)
        return original(self, timeout)

    monkeypatch.setattr(PageDispatcher, "wait_until_ready", spy)
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    conn.popup_born(opener_target, context.context_id, 1)
    deadline = time.monotonic() + 5
    while len(waited) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert waited == [20.0, None], "a requested page waits 20 s, a popup without a limit: %r" % (waited,)


def test_a_popup_that_never_gets_its_ready_ends_with_its_context_and_says_nothing(late_browser):
    """204, a server that never answers: no ready, no `page` event, no warning; closing the context ends the wait."""
    import warnings
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    announced = []
    original = context.emit
    context.emit = lambda event, params=None: (announced.append(event) if event == "page" else None, original(event, params))[1]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        conn.popup_born(opener_target, context.context_id, 1)
        time.sleep(0.5)
        assert not announced, "a popup with no ready was announced"
        assert late_browser._building, "the popup is not waiting"
        server.handle({"guid": context.guid, "method": "close", "params": {}})
        deadline = time.monotonic() + 5
        while late_browser._building and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not late_browser._building, "closing the context did not end the wait of the popup"
        time.sleep(0.2)
    assert not announced, "a popup of a closed context was announced"
    assert not [w for w in caught if "adoption" in str(w.message)], [str(w.message) for w in caught]


def test_a_popup_is_announced_with_the_address_of_the_commit_that_came_with_its_ready(late_browser):
    """The engine says Page.ready and, in the same turn, the commit of the document: the client is told the page after the commit, so
    the url it reads at the `page` event is the destination, as Playwright's is (3 of 4 popups said about:blank)."""
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    urls = []
    original = context.emit

    def spy(event, params=None):
        if event == "page":
            page = next(o for o in server._objects.values() if o.TYPE == "Page" and o.channel == params["page"])
            urls.append(page.frame.url)
        return original(event, params)

    context.emit = spy
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    frame_id = "f" + target
    url = "http://127.0.0.1/slow"
    deadline = time.monotonic() + 5
    while not any(o.TYPE == "Page" and getattr(o, "session", None) == session for o in server._objects.values())             and time.monotonic() < deadline:
        time.sleep(0.01)
    conn.queued.append(("Page.navigationStarted", {"frameId": frame_id, "navigationId": "n1", "url": url}, session))
    conn.queued.append(("Page.navigationCommitted", {"frameId": frame_id, "navigationId": "n1", "url": url, "name": ""}, session))
    conn.say_ready(session)           # the commit follows in the stream: it is delivered with the round trip that comes back after it
    deadline = time.monotonic() + 5
    while not urls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert urls == [url], "the popup was announced with %r, not with the address of the commit that came with its ready" % (urls,)


def test_a_download_started_by_a_popup_that_is_not_announced_is_reported_on_its_opener(late_browser):
    """`<a target=_blank href=file>` with `Content-Disposition: attachment`: the new tab never gets a Page.ready and the engine closes it
    at once. Playwright reports the download on the OPENER (ffBrowser._onDownloadCreated); looking only among the announced pages
    lost it (no `download` event anywhere)."""
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    opener = next(o for o in server._objects.values() if o.TYPE == "Page" and o.target_id == opener_target)
    said = []
    original = opener.emit
    opener.emit = lambda event, params=None: (said.append((event, params)), original(event, params))[1]
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    deadline = time.monotonic() + 5
    while target not in late_browser._building and time.monotonic() < deadline:
        time.sleep(0.01)
    conn.dispatch_event("Browser.downloadCreated", {
        "uuid": "u1", "browserContextId": context.context_id, "pageTargetId": target, "frameId": "f" + target,
        "url": "http://127.0.0.1/file", "suggestedFileName": "f.bin"}, None)
    downloads = [p for e, p in said if e == "download"]
    assert len(downloads) == 1, "the download of the popup was not reported on its opener: %r" % (said,)
    assert downloads[0]["suggestedFilename"] == "f.bin"


def test_the_load_of_a_popup_said_before_its_announcement_is_said_again_right_after_it(late_browser, monkeypatch):
    """A listener registered in the handler of the `page` event (`page.on("load")`) hears the load of a popup in Playwright, because the
    page is reported after its ready and the load follows. Here the document may have loaded before the client hears of the page: the
    two states are said again after the announcement, in order, and the first time they already set the state `wait_for_load_state`
    reads."""
    from invisible_playwright._juggler import dispatcher as base

    trace = []
    original = base.Dispatcher.emit

    def spy(self, method, params=None):
        if method == "page":
            trace.append("page")
        elif getattr(self, "TYPE", "") == "Frame" and method == "loadstate" and params and "add" in params:
            trace.append(params["add"])
        return original(self, method, params)

    monkeypatch.setattr(base.Dispatcher, "emit", spy)
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    deadline = time.monotonic() + 5
    while target not in late_browser._building and time.monotonic() < deadline:
        time.sleep(0.01)
    frame_id, url = "f" + target, "http://127.0.0.1/slow"
    conn.dispatch_event("Page.navigationStarted", {"frameId": frame_id, "navigationId": "n1", "url": url}, session)
    conn.dispatch_event("Page.navigationCommitted", {"frameId": frame_id, "navigationId": "n1", "url": url, "name": ""}, session)
    conn.dispatch_event("Page.eventFired", {"frameId": frame_id, "name": "DOMContentLoaded"}, session)
    conn.dispatch_event("Page.eventFired", {"frameId": frame_id, "name": "load"}, session)
    del trace[:]            # what is said from here on: the ready, the announcement, the replay
    conn.say_ready(session)
    deadline = time.monotonic() + 5
    while trace.count("load") < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    assert "page" in trace, trace
    after = trace[trace.index("page") + 1:]
    assert after == ["domcontentloaded", "load"], "after the announcement the client heard %r" % (after,)


def test_a_popup_whose_target_goes_away_while_its_page_is_being_built_is_announced_and_closed(late_browser, monkeypatch):
    """A download's empty tab closes within milliseconds; on a loaded machine the page of the popup is still being built when the engine
    detaches it. That detach went by with nobody to tell, and the wait for a ready that never comes went on for ever: the popup was never
    announced (the full suite failed once on it)."""
    from invisible_playwright._juggler.server import PageDispatcher
    original_init = PageDispatcher.__init__

    def slow_init(self, *args, **kwargs):
        time.sleep(0.4)             # the build is slower than the detach
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(PageDispatcher, "__init__", slow_init)
    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    monkeypatch.undo()              # the opener is built at normal speed
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    monkeypatch.setattr(PageDispatcher, "__init__", slow_init)
    opener_target = next(iter(conn.sessions))
    announced, closed = [], []
    original = context.emit
    context.emit = lambda event, params=None: (announced.append(event) if event == "page" else None, original(event, params))[1]
    target, session = conn.popup_born(opener_target, context.context_id, 1)
    conn.dispatch_event("Browser.detachedFromTarget", {"targetId": target, "sessionId": session}, None)
    deadline = time.monotonic() + 5
    while "page" not in announced and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "page" in announced, "the popup that went away while its page was being built was never announced"


@pytest.mark.parametrize("command", ["Page.setInitScripts", "Browser.getInfo"])
def test_a_command_of_the_adoption_that_finds_the_session_gone_announces_and_closes_the_popup(late_browser, command, monkeypatch):
    """The tab goes away WHILE the adoption initializes it: the engine answers 'cannot find session' to a command of the adoption. Like a
    detach before the ready, that ends in the page announced and closed (Playwright's does), never in a discard with a warning: the
    download of a link into a new tab was lost one time in a hundred (independent run, quiet machine)."""
    import warnings
    from invisible_core.juggler.connection import ProtocolError

    server, conn = late_browser.server, late_browser.conn
    context = _new_context(late_browser)
    conn.released = {"s1"}
    server.handle({"guid": context.guid, "method": "newPage", "params": {"pageNumber": 1}})
    opener_target = next(iter(conn.sessions))
    original_send = conn.send

    def send(method, params=None, session=None, timeout=30):
        if method == command and session not in (None, "s1"):
            raise ProtocolError('%s: ERROR: cannot find session with id "%s"' % (method, session))
        return original_send(method, params, session, timeout)

    monkeypatch.setattr(conn, "send", send)
    announced = []
    original = context.emit
    context.emit = lambda event, params=None: (announced.append(event) if event == "page" else None, original(event, params))[1]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        target, session = conn.popup_born(opener_target, context.context_id, 1)
        if command == "Browser.getInfo":
            conn.say_ready(session)       # this one is asked once the page is ready
        else:
            conn.dispatch_event("Browser.detachedFromTarget", {"targetId": target, "sessionId": session}, None)
        deadline = time.monotonic() + 5
        while "page" not in announced and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
    assert "page" in announced, "the popup whose session was gone was discarded instead of announced"
    assert not [w for w in caught if "adoption" in str(w.message)], [str(w.message) for w in caught]
