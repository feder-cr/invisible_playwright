"""`goto` / `reload` / `go_back` answer with a Response, not with None.

**Why this file exists.** Until 2026-09-08 `op_goto` ended with a hardcoded
`return {"response": None}` ([B200]). Nothing in this project noticed for a
month, and the reason is worth keeping: every probe, every gate and almost
every test in the suite writes `page.goto(url)` and throws the answer away, so
a defect that lives ENTIRELY in a return value had no assertion anywhere near
it. The network layer underneath was correct the whole time - the `response`
events, their statuses and `expect_response` all worked - which is why the
browser looked healthy while Crawlee, which reads what `goto` returns and
treats a None as a failed load, failed every single request.

The lesson generalises past this bug: a value nobody reads is a value nobody
tests. These tests read it.

**The two halves.** The unit half feeds the server the network events a real
navigation produces, in the order the engine sends them, and asks the page for
the response afterwards - which is where the two traps live: `_requests` is
emptied on `requestFinished`, so by the time `goto` stops waiting for `load`
the document request is no longer in flight, and a redirect chain gives every
hop the SAME navigationId, so the answer has to be the last one. The e2e half
drives a real browser at a local server and reads `.status`.

Known-bad inputs, each run against this file before it was trusted:

* the previous `return {"response": None}` in `op_goto` -> the goto tests go
  red, unit and e2e;
* the same in `_history` -> the reload / go_back tests go red;
* indexing the navigation by plain assignment instead of re-insertion -> the
  eviction test goes red, because a redirect chain ages at its first hop;
* returning the FIRST hop of a redirect instead of the last -> the redirect
  tests go red on both halves.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.favicon import FaviconRequests
from invisible_core.juggler.lifecycle import Lifecycle, NavigationMark


# -- the unit half: the server, fed the events a navigation produces ---------

def _bare_page():
    """A `PageDispatcher` with only what the network branches touch.

    Built the way `test_screencast.py` builds one: `object.__new__` plus the
    attributes under test, so this exercises the SHIPPED event handler rather
    than a re-description of it.
    """
    from invisible_playwright._juggler.server import JugglerServer, PageDispatcher

    server = JugglerServer()
    up: list = []
    server.attach(type("R", (), {"emit_message": lambda self, m: up.append(m)})())

    page = object.__new__(PageDispatcher)
    page.server = server
    page.guid = "page@1"
    page.disposed = False
    page.parent = None
    page.initializer = {}
    page._requests = {}
    page._request_log = []
    page._navigation_requests = {}
    page._announced_closed = False
    # The network branches ask the session's lifecycle which requests are the
    # favicon; an empty memory answers "none", as for a page without one.
    page.lifecycle = SimpleNamespace(favicons=FaviconRequests())
    page.messages = up
    page.context = SimpleNamespace(emit=lambda *a, **k: None, intercepting=False,
                                   options={})
    page.frame = SimpleNamespace(frame_id="F1")
    page.frame_for = lambda fid: SimpleNamespace(channel={"guid": "frame@%s" % fid})
    return page


@pytest.mark.parametrize("has_request", [False, True])
@pytest.mark.parametrize("source", ["goto", "committed"])
def test_new_document_request_is_a_channel_or_omitted(has_request, source):
    from invisible_playwright._juggler.server import FrameDispatcher

    page = _bare_page()
    if has_request:
        _navigate(page, "NAV1")
    frame = object.__new__(FrameDispatcher)
    frame.server = page.server
    frame.guid = "frame@1"
    frame.disposed = False
    frame.page = page
    frame.frame_id = "F1"
    page.frame_for = lambda fid: frame

    def committed():
        # As the lifecycle announces a commit (`_on_lifecycle`).
        page._on_lifecycle("commit", "F1", "about:blank", navigation="NAV1")

    if source == "goto":
        def goto(*args, **kwargs):
            committed()
            return {"navigationId": "NAV1", "url": "about:blank"}

        page.lifecycle = SimpleNamespace(goto=goto)
        frame.op_goto({"url": "about:blank"})
    else:
        committed()
    events = [m for m in page.messages if m["method"] == "navigated"]
    expected = ({"request": page._navigation_requests["NAV1"].channel}
                if has_request else {})
    assert events
    assert all(event["params"]["newDocument"] == expected for event in events)


def test_navigation_document_uses_the_final_redirect_request():
    page = _bare_page()
    _navigate(page, "NAV1", request_id="initial", status=302)
    _navigate(page, "NAV1", request_id="redirected", status=200)
    assert page.navigation_document("NAV1") == {
        "request": page._navigation_requests["NAV1"].channel,
    }
    assert page._navigation_requests["NAV1"].request_id == "redirected"


def _navigate(page, navigation_id, *, request_id="R1", status=200,
              url="http://127.0.0.1/", finish=True):
    """The three events the engine sends for one document request."""
    page._on_juggler_event("Network.requestWillBeSent", {
        "requestId": request_id, "url": url, "method": "GET",
        "frameId": "F1", "navigationId": navigation_id,
        "isIntercepted": False, "headers": []})
    page._on_juggler_event("Network.responseReceived", {
        "requestId": request_id, "status": status, "statusText": "OK",
        "headers": []})
    if finish:
        page._on_juggler_event("Network.requestFinished",
                               {"requestId": request_id})


def test_the_document_request_is_reachable_by_its_navigation_id():
    page = _bare_page()
    _navigate(page, "NAV1")
    request = page._navigation_requests["NAV1"]
    assert request.navigation_id == "NAV1"
    assert page.navigation_response("NAV1") == request.response.channel


def test_the_answer_SURVIVES_requestFinished_emptying_the_in_flight_map():
    """The first trap. `goto` waits for `load`, which happens well after the
    document request has finished, and `requestFinished` pops `_requests` - so
    an implementation that looked the request up there would find nothing
    exactly when it is asked."""
    page = _bare_page()
    _navigate(page, "NAV1", finish=True)
    assert page._requests == {}, "the precondition this test is about"
    assert page.navigation_response("NAV1") is not None


def test_a_redirect_chain_answers_with_the_LAST_hop():
    """The second trap. `NetworkObserver.js` copies the navigationId onto every
    hop (`this.navigationId = redirectedFrom.navigationId`), so all of them
    match and Playwright answers with the final response."""
    page = _bare_page()
    _navigate(page, "NAV1", request_id="R1", status=302,
              url="http://127.0.0.1/from")
    _navigate(page, "NAV1", request_id="R1-redirect1", status=200,
              url="http://127.0.0.1/to")
    response = page._navigation_requests["NAV1"].response
    assert response.initializer["status"] == 200
    assert response.initializer["url"] == "http://127.0.0.1/to"
    assert page.navigation_response("NAV1") == response.channel


def test_a_subresource_is_not_indexed_and_does_not_answer_for_a_navigation():
    """Only the main document channel carries a navigationId, which is what
    makes the correlation exact instead of a guess about ordering."""
    page = _bare_page()
    _navigate(page, None, request_id="R2", url="http://127.0.0.1/app.js")
    assert page._navigation_requests == {}
    request = page._request_log[-1]
    assert request.initializer["isNavigationRequest"] is False


def test_a_navigation_request_says_so_in_its_initializer():
    page = _bare_page()
    _navigate(page, "NAV1")
    assert page._request_log[-1].initializer["isNavigationRequest"] is True


def test_no_navigation_and_no_response_are_both_answered_with_None():
    """None is a real answer here: a same-document navigation has no
    navigationId at all, and a document request that never got a response has
    nothing to hand back. Neither is an error and neither is an empty object."""
    page = _bare_page()
    assert page.navigation_response(None) is None
    assert page.navigation_response("NEVER-HAPPENED") is None
    page._on_juggler_event("Network.requestWillBeSent", {
        "requestId": "R9", "url": "http://127.0.0.1/", "method": "GET",
        "frameId": "F1", "navigationId": "NAV9", "headers": []})
    assert page.navigation_response("NAV9") is None, (
        "the request exists but has no response yet")


def test_the_index_is_capped_and_a_redirect_chain_does_not_age_its_entry():
    """A driver must not grow without bound because the page navigates in a
    loop - and the eviction must not throw away the navigation in progress,
    which is what a plain assignment would allow: the entry would keep the
    position of the FIRST hop and get old while the chain is still running."""
    from invisible_playwright._juggler.server import PageDispatcher

    page = _bare_page()
    _navigate(page, "OLDEST", request_id="R0")
    for i in range(PageDispatcher.LOG_LIMIT - 1):
        _navigate(page, "NAV%d" % i, request_id="R%d" % (i + 1))
    # the chain continues on the oldest entry, which re-inserts it
    _navigate(page, "OLDEST", request_id="R0-redirect1", status=200)
    _navigate(page, "PUSHES-ONE-OUT", request_id="RX")

    assert len(page._navigation_requests) == PageDispatcher.LOG_LIMIT
    assert page.navigation_response("OLDEST") is not None, (
        "the redirect chain still in progress was evicted")
    assert "NAV0" not in page._navigation_requests, (
        "nothing was evicted, so the cap does not hold")


def test_op_goto_ANSWERS_WITH_THE_CHANNEL_and_not_with_None():
    """⛔ THIS ONE HAS TO BE A UNIT TEST, and the mutation run says why.

    Restoring the old hardcoded `return {"response": None}` in `op_goto` left
    every other test in this file green: the only assertion on that return
    value lived in the e2e half, which the DEFAULT selection deselects and
    which needs a binary nobody has on a fresh checkout. A gate that can only
    fail on a machine with a browser is how a return value stays broken for a
    month. Same for `_history` below.
    """
    from invisible_playwright._juggler.server import FrameDispatcher

    page = _bare_page()
    _navigate(page, "NAV1")
    page.lifecycle = SimpleNamespace(
        goto=lambda url, **kw: {"navigationId": "NAV1", "url": url})

    frame = object.__new__(FrameDispatcher)
    frame.server = page.server
    frame.guid = "frame@1"
    frame.disposed = False
    frame.page = page
    frame.frame_id = "F1"

    answer = frame.op_goto({"url": "http://127.0.0.1/"})
    assert answer == {"response": page._navigation_requests["NAV1"]
                      .response.channel}


def test_op_goto_answers_None_when_the_navigation_created_no_document():
    """The anchor case, which the engine reports with a null navigationId.
    Playwright answers null here, so filling it in would be an invented
    object rather than a fix."""
    from invisible_playwright._juggler.server import FrameDispatcher

    page = _bare_page()
    page.lifecycle = SimpleNamespace(
        goto=lambda url, **kw: {"navigationId": None, "url": url})

    frame = object.__new__(FrameDispatcher)
    frame.server = page.server
    frame.guid = "frame@1"
    frame.disposed = False
    frame.page = page
    frame.frame_id = "F1"

    assert frame.op_goto({"url": "http://127.0.0.1/#x"}) == {"response": None}


def test_reload_and_goBack_answer_with_the_channel_of_the_new_navigation():
    """History gives no navigationId of its own, so the answer is anchored on
    the navigation the WAIT discovered - see the docstring above for why this
    is asserted here and not only in the e2e."""
    page = _bare_page()
    _navigate(page, "NAV-NEW")
    page.lifecycle = SimpleNamespace(
        mark=lambda fid: NavigationMark("NAV-OLD", 0),
        wait_for_new_navigation=lambda *a, **kw: "NAV-NEW")
    page.send = lambda method, params=None: {"success": True}

    expected = {"response": page._navigation_requests["NAV-NEW"]
                .response.channel}
    assert page.op_reload({}) == expected
    assert page.op_go_back({}) == expected


def test_goBack_at_the_start_of_history_is_still_None():
    """A refused goBack is an ordinary answer in Playwright, not a failure,
    and it must not start waiting for a navigation that will never happen."""
    page = _bare_page()
    page.lifecycle = SimpleNamespace(
        mark=lambda fid: NavigationMark("NAV-OLD", 0),
        wait_for_new_navigation=lambda *a, **kw: pytest.fail(
            "it waited for a navigation the browser refused to start"))
    page.send = lambda method, params=None: {"success": False}

    assert page.op_go_back({}) == {"response": None}


def test_wait_for_new_navigation_HANDS_BACK_the_id_it_waited_for():
    """History gives no navigationId, so this function is the only thing that
    knows which navigation happened - and `reload` needs it to answer with a
    Response. Reading the frame afterwards instead would race a navigation the
    page starts on its own."""
    from invisible_core.juggler.connection import EventListeners
    from invisible_core.juggler.lifecycle import Lifecycle

    conn = EventListeners()
    lifecycle = Lifecycle(conn, "S1")
    conn.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    conn.dispatch_event("Page.navigationStarted",
                        {"frameId": "F1", "navigationId": "OLD"}, "S1")
    since = lifecycle.mark("F1")

    def navigate_later():
        conn.dispatch_event("Page.navigationStarted",
                            {"frameId": "F1", "navigationId": "NEW"}, "S1")
        conn.dispatch_event("Page.navigationCommitted",
                            {"frameId": "F1", "navigationId": "NEW",
                             "url": "http://127.0.0.1/"}, "S1")
        conn.dispatch_event("Page.eventFired",
                            {"frameId": "F1", "name": "load"}, "S1")

    threading.Timer(0.05, navigate_later).start()
    assert lifecycle.wait_for_new_navigation("F1", since, "load",
                                             timeout=10) == "NEW"


def _respond(page, **extra):
    """One request and its response, the response carrying ``extra``."""
    page._on_juggler_event("Network.requestWillBeSent", {
        "requestId": "R1", "url": "https://127.0.0.1/", "method": "GET",
        "frameId": "F1", "isIntercepted": False, "headers": []})
    page._on_juggler_event("Network.responseReceived", dict(
        {"requestId": "R1", "status": 200, "statusText": "OK", "headers": []},
        **extra))
    return page._requests["R1"].response


def test_a_response_hands_on_the_certificate_the_engine_sent():
    """The same shape as the goto answer above: a value nobody read. Until
    this, `response.security_details()` answered None for every response,
    HTTPS included, which a script reads as "not over TLS" (B303 E15). Known
    bad: the previous `return {"value": None}` turns this red."""
    details = {"protocol": "TLS 1.3", "subjectName": "probe.local",
               "issuer": "probe-ca", "validFrom": 1760000000,
               "validTo": 1762592000}
    response = _respond(_bare_page(), securityDetails=details)
    assert response.op_security_details({}) == {"value": details}


@pytest.mark.parametrize("sent", [{}, {"securityDetails": None}])
def test_a_response_with_no_certificate_answers_None(sent):
    """Plain HTTP, and every engine through firefox-48, which sends null."""
    response = _respond(_bare_page(), **sent)
    assert response.op_security_details({}) == {"value": None}


class _Pipe(EventListeners):
    """The subscriber registry the session's lifecycle listens on, and the
    `post` a held request is released with."""

    def __init__(self, sent):
        super().__init__()
        self._sent = sent

    def post(self, method, params=None, session=None):
        self._sent.append((method, params, session))


def _favicon_page(intercepting):
    """A bare page with a REAL lifecycle on its session, recording what reaches
    the client and what is posted to the engine."""
    page = _bare_page()
    told, sent = [], []
    pipe = _Pipe(sent)
    page.lifecycle = Lifecycle(pipe, "S1")
    page.context = SimpleNamespace(
        emit=lambda event, params: told.append(event), intercepting=intercepting,
        browser=SimpleNamespace(conn=pipe))
    page.session = "S1"
    page.intercepting = False

    def send(*a, **k):
        # These events are handled on the reader thread, where a `send`
        # waits for a reply only that thread could read.
        pytest.fail("a held request was released with send, from the reader thread")

    page.send = send
    return page, told, sent


def _chain(*, intercepted):
    """The browser's favicon request, redirected once, in the engine's order:
    a hop is finished BEFORE the next one is sent."""
    out = []
    for rid, url, came_from in (("FAV", "http://127.0.0.1/favicon.ico", None),
                                ("FAV-redirect1", "http://127.0.0.1/icon.png", "FAV")):
        params = {"requestId": rid, "url": url, "method": "GET", "frameId": "F1",
                  "isIntercepted": intercepted, "headers": []}
        if came_from:
            params["redirectedFrom"] = came_from
        out += [("Network.requestWillBeSent", params),
                ("Network.responseReceived", {
                    "requestId": rid, "status": 302 if came_from is None else 200,
                    "statusText": "", "headers": []}),
                ("Network.requestFinished", {"requestId": rid})]
    return out


def _deliver(page, events, *, held=False):
    """As the connection delivers: the session's lifecycle first, the page
    after it. `held`: the page replays them only once the lifecycle has seen
    the whole chain, as for the events a page holds at its birth."""
    if held:
        for method, params in events:
            page.lifecycle.c.dispatch_event(method, params, "S1")
        for method, params in events:
            page._on_juggler_event(method, params)
        return
    for method, params in events:
        page.lifecycle.c.dispatch_event(method, params, "S1")
        page._on_juggler_event(method, params)


def _favicon_chain(page, *, intercepted, held=False):
    _deliver(page, _chain(intercepted=intercepted), held=held)


def test_the_favicon_reaches_the_client_with_no_event():
    """B321: Playwright shows a client none of the favicon's events. Known bad:
    without the favicon branch the client was told request, response and
    requestFinished for both hops."""
    page, told, _ = _favicon_page(intercepting=False)
    _favicon_chain(page, intercepted=False)
    assert told == []
    assert page._requests == {} and page._request_log == []


def test_a_held_favicon_goes_to_the_network_and_to_no_route():
    """Upstream aborts it; a real Firefox asks the site for it, so it is let
    through, unseen, instead (`invisible_core.juggler.favicon`)."""
    page, told, sent = _favicon_page(intercepting=True)
    _favicon_chain(page, intercepted=True)
    assert "route" not in told and told == []
    assert sent == [("Network.resumeInterceptedRequest", {"requestId": "FAV"}, "S1"),
                    ("Network.resumeInterceptedRequest", {"requestId": "FAV-redirect1"}, "S1")]


def test_a_request_held_after_interception_went_off_is_released_without_blocking():
    """The other caller of the release: the engine held a request, and by its
    event neither the page nor the context intercepts any more. It used to
    `send` the release from the reader thread, the deadlock the favicon found."""
    page, told, sent = _favicon_page(intercepting=False)
    _deliver(page, [("Network.requestWillBeSent", {
        "requestId": "R", "url": "http://127.0.0.1/data.json", "method": "GET",
        "frameId": "F1", "isIntercepted": True, "headers": []})])
    assert sent == [("Network.resumeInterceptedRequest", {"requestId": "R"}, "S1")]
    assert told == ["request"]


def test_a_page_replaying_held_events_agrees_with_the_lifecycle():
    """The page replays the events it held at birth after the lifecycle has
    seen the whole chain, finishes included. One memory, so the same answer."""
    page, told, _ = _favicon_page(intercepting=False)
    _favicon_chain(page, intercepted=False, held=True)
    assert told == []
    assert page.lifecycle.inflight == 0


def test_the_page_answers_with_the_lifecycles_memory_and_keeps_none():
    """Known bad: a page that classified requests on its own could disagree with
    the lifecycle about one. Here the lifecycle and the page are handed the same
    request id with different URLs, and the page must answer as the lifecycle
    did, whatever its own copy of the event says."""
    page, told, _ = _favicon_page(intercepting=False)
    page.lifecycle.c.dispatch_event("Network.requestWillBeSent", {
        "requestId": "X", "url": "http://127.0.0.1/favicon.ico"}, "S1")
    page._on_juggler_event("Network.requestWillBeSent", {
        "requestId": "X", "url": "http://127.0.0.1/data.json", "method": "GET",
        "frameId": "F1", "isIntercepted": False, "headers": []})
    assert told == []
    # And a favicon URL the lifecycle has not seen is not hidden by the page.
    page._on_juggler_event("Network.requestWillBeSent", {
        "requestId": "Y", "url": "http://127.0.0.1/favicon.ico", "method": "GET",
        "frameId": "F1", "isIntercepted": False, "headers": []})
    assert told == ["request"]


def test_a_page_request_beside_the_favicon_is_still_reported():
    page, told, _ = _favicon_page(intercepting=False)
    _favicon_chain(page, intercepted=False)
    _deliver(page, [("Network.requestWillBeSent", {
        "requestId": "R", "url": "http://127.0.0.1/data.json", "method": "GET",
        "frameId": "F1", "isIntercepted": False, "headers": []})])
    assert told == ["request"]


def test_a_route_that_sends_a_navigation_on_tells_where_it_went():
    """B326: `route.fallback(url=...)` on a navigation. The engine applies the
    URL as an internal redirect and says nothing, so the route tells the
    request: its response is that URL's, and the lifecycle judges the commit
    there. Known bad: the response kept the URL the request was sent with, and
    `goto` refused the page as landed elsewhere."""
    from invisible_playwright._juggler.server import RouteDispatcher

    page, _told, _sent = _favicon_page(intercepting=True)
    page.send = lambda command, params=None, timeout=None: {}
    routed = "http://127.0.0.1/global-var.html"
    _deliver(page, [("Network.requestWillBeSent", {
        "requestId": "D", "url": "http://127.0.0.1/foo", "method": "GET",
        "frameId": "F1", "navigationId": "NAV1", "isIntercepted": True,
        "headers": []})])
    request = page._requests["D"]
    told_lifecycle = []
    went_to = page.lifecycle.went_to
    page.lifecycle.went_to = lambda nav, url: (told_lifecycle.append((nav, url)),
                                               went_to(nav, url))
    route = object.__new__(RouteDispatcher)
    route.answered = False
    route.request = request
    route.dispose = lambda: None
    route.op_continue({"url": routed})
    assert request.url == routed
    # What the lifecycle does with it is the core's own test
    # (`test_a_goto_lands_where_it_went.py`); here, that it is told.
    assert told_lifecycle == [("NAV1", routed)]
    _deliver(page, [("Network.responseReceived", {
        "requestId": "D", "status": 200, "statusText": "OK", "headers": []})])
    assert request.response.initializer["url"] == routed
    # The request keeps the URL it was sent with: the client applies the
    # route's override to it on its side, as Playwright's does.
    assert request.initializer["url"] == "http://127.0.0.1/foo"


# -- the e2e half: a real browser, and `.status` read off the answer ---------

PAGE = (b"<!doctype html><html><head><title>local</title></head>"
        b"<body><h1>hi</h1><a id='anchor' href='#deeper'>go</a></body></html>")


MISSING = b"<!doctype html><html><body>not here</body></html>"


def _serve():
    """A local server with a page, a redirect to it and a 404.

    ⛔ THREADING, and it is not a nicety. A single-threaded `HTTPServer` served
    the first navigations of this test and then answered the fourth with
    `NS_ERROR_NET_EMPTY_RESPONSE`: Firefox opens speculative connections, and a
    socket the server accepts but never gets round to reading is closed with
    nothing written on it. The failure looks like a defect in the navigation
    under test, which is the worst kind of bench defect - it accuses the
    product. `test_cross_origin_iframe.py` reached the same conclusion.
    """
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body, status = ((MISSING, 404) if self.path.startswith("/missing")
                            else (PAGE, 200))
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port


@pytest.mark.wall_clock
@pytest.mark.e2e
def test_navigation_answers_with_a_real_response(firefox_binary):
    """One browser, every navigating method, and each answer is READ.

    This is the assertion whose absence let [B200] live for a month: the page
    loaded correctly in every one of those runs, and the only broken thing was
    the value nobody looked at.
    """
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()

            response = page.goto(url, wait_until="load", timeout=30_000)
            assert response is not None, (
                "goto answered None on a page that loaded: [B200]")
            assert response.status == 200, response.status
            assert response.url == url, response.url
            assert response.ok is True
            assert response.request.is_navigation_request() is True
            assert page.title() == "local", "the page did not actually load"

            reloaded = page.reload(wait_until="load", timeout=30_000)
            assert reloaded is not None, "reload answered None: [B200]"
            assert reloaded.status == 200, reloaded.status

            # a redirect answers with the FINAL response, not the 302
            redirected = page.goto(url + "redirect", wait_until="load",
                                   timeout=30_000)
            assert redirected is not None
            assert redirected.status == 200, (
                "the 302 was answered instead of the page it points at: %s"
                % redirected.status)
            assert redirected.url == url, redirected.url

            # an error page is a response like any other, not a None
            missing = page.goto(url + "missing", wait_until="load",
                                timeout=30_000)
            assert missing is not None
            assert missing.status == 404, missing.status
            assert missing.ok is False

            # The None THAT IS CORRECT. A same-document navigation creates no
            # document, so Playwright answers null - fixing [B200] must not
            # turn this into an invented object.
            page.goto(url, wait_until="load", timeout=30_000)
            assert page.goto(url + "#deeper", timeout=30_000) is None, (
                "a same-document navigation must answer None")

            # ⛔ AND HISTORY TO A SAME-DOCUMENT ENTRY ANSWERS None AT ONCE
            # ([B290]). This arm was left out on the word of [B185], which said
            # the engine answered `Page.goBack` and then did nothing; [B185]
            # was closed on 2026-09-17 (the engine does go back) and the
            # comment stayed, so nobody asked what `go_back` did to a hash
            # entry: it waited for a navigation id that a same-document
            # navigation never has, and raised at the timeout. Measured on
            # 0.35.2: 8.0 s and a TimeoutError for `go_back(timeout=8000)`.
            for go, where in ((page.go_back, url), (page.go_forward, url + "#deeper")):
                began = time.monotonic()
                assert go(timeout=8_000) is None
                assert time.monotonic() - began < 2.0, "it waited out the timeout"
                assert page.url == where, page.url
    finally:
        srv.shutdown()
