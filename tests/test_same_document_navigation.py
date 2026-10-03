"""A pushState reaches `page.url` and wakes `wait_for_url`.

**Why this file exists.** Until 2026-09-20 the server forwarded a frame's URL
to the client on `Page.navigationCommitted` only. A same-document navigation -
`history.pushState`, a hash change, the route change of every single-page
application - arrives from the engine as `Page.sameDocumentNavigation`, and
that event updated the lifecycle's own bookkeeping and nothing else. The
client never heard of it: `page.url` kept the URL of the last full load,
`wait_for_url` never resolved, and `framenavigated` never fired.

Measured against a page that routes in the client: a click on a navigation
tab fetched its page (200), replaced the content and changed
`location.href`, while `page.url` still named the previous document thirty
seconds later. Turbo, React Router and Next.js all behave this way, so a
script that waits for the URL after such a click times out on a click that
worked.

Upstream Playwright handles the event in `ffPage._onSameDocumentNavigation`
and reports it as a `navigated` event WITHOUT a `newDocument`, which is what
the client reads to tell the two apart; this server now does the same.

Known-bad inputs, each run against this file before it was trusted:

* removing the `sameDocument` branch from `PageDispatcher._on_lifecycle`
  (which is where the event goes up since 2026-10-04: the lifecycle announces
  it before it wakes a `goto`, see `test_same_document_goto_e2e.py`) -> the
  unit test goes red (no `navigated` is emitted) and the e2e test times out on
  `wait_for_url`;
* emitting the event WITH a `newDocument` -> the unit test goes red, because
  the client would then wait for a document request that does not exist.
"""
from __future__ import annotations

import http.server
import socket
import threading
from types import SimpleNamespace

import pytest

from invisible_playwright._juggler.connection import EventListeners
from invisible_playwright._juggler.lifecycle import Lifecycle


# -- the unit half: the shipped event handler, fed the engine's event ---------

def _page_with_one_frame():
    """A `PageDispatcher` built the way `test_navigation_response.py` builds
    one - `object.__new__` plus the attributes this branch touches - so the
    SHIPPED handler runs, not a re-description of it. The frame records what
    is emitted on it."""
    from invisible_playwright._juggler.server import JugglerServer, PageDispatcher

    server = JugglerServer()
    up: list = []
    server.attach(type("R", (), {"emit_message": lambda self, m: up.append(m)})())

    frame = SimpleNamespace(url="http://127.0.0.1/", name="", emitted=[])
    frame.emit = lambda method, params=None: frame.emitted.append((method, params))

    page = object.__new__(PageDispatcher)
    page.server = server
    page.guid = "page@1"
    page.disposed = False
    page.parent = None
    page.initializer = {}
    page._requests = {}
    page._request_log = []
    page._navigation_requests = {}
    page.context = SimpleNamespace(emit=lambda *a, **k: None, intercepting=False)
    page.frame = frame
    page.frame_for = lambda fid: frame
    page.lifecycle = Lifecycle(EventListeners(), "S1")
    page._hear_lifecycle()
    return page, frame


def _engine_reports(page, params):
    """The engine's event, delivered the way the browser delivers it: to the
    lifecycle, which announces it to the page."""
    page.lifecycle._on_event("Page.sameDocumentNavigation", params)


def test_a_same_document_navigation_is_reported_to_the_client():
    page, frame = _page_with_one_frame()

    _engine_reports(page, {"frameId": "F1", "url": "http://127.0.0.1/reports"})

    assert frame.url == "http://127.0.0.1/reports", (
        "the frame kept the old URL: the client's initializer snapshot is "
        "never refreshed and `page.url` would lie")
    assert frame.emitted == [("navigated", {
        "url": "http://127.0.0.1/reports", "name": "",
    })], frame.emitted


def test_the_event_carries_NO_newDocument():
    """The client tells a full load from a same-document navigation by the
    presence of `newDocument`; `wait_for_navigation` reads `newDocument.request`
    to answer with a Response. Announcing a document that was never requested
    would make it wait for a response that never comes."""
    page, frame = _page_with_one_frame()

    _engine_reports(page, {"frameId": "F1", "url": "http://127.0.0.1/#x"})

    (method, params), = frame.emitted
    assert method == "navigated"
    assert "newDocument" not in params, params
    assert "error" not in params, params


def test_a_full_navigation_still_announces_its_document():
    """The sibling branch must not have been folded into this one: a real
    navigation keeps announcing `newDocument`, or `wait_for_navigation`
    stops answering with a Response ([B200] all over again)."""
    page, frame = _page_with_one_frame()

    page._on_juggler_event("Page.navigationCommitted",
                           {"frameId": "F1", "navigationId": "N1",
                            "url": "http://127.0.0.1/other"})

    # A new document also resets the frame's load states around the event;
    # this test is about the `navigated` one.
    (method, params), = [e for e in frame.emitted if e[0] == "navigated"]
    assert method == "navigated"
    assert params["url"] == "http://127.0.0.1/other"
    assert "newDocument" in params, params


# -- the e2e half: a real pushState, and `page.url` read afterwards ----------

PAGE = (b"<!doctype html><html><head><title>spa</title></head><body>"
        b"<button id='route' onclick=\"history.pushState({}, '', '/reports');"
        b"document.title='reports'\">go</button>"
        b"</body></html>")


def _serve():
    """A local threaded server; the threading matters, see
    `test_navigation_response._serve`."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

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


@pytest.mark.e2e
def test_pushState_updates_page_url_and_wakes_wait_for_url(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto(url, wait_until="load", timeout=30_000)
            assert page.url == url

            seen = []
            page.on("framenavigated", lambda frame: seen.append(frame.url))

            page.click("#route", timeout=15_000)
            page.wait_for_url("**/reports", timeout=15_000)

            assert page.url == url + "reports", page.url
            assert page.evaluate("location.href") == page.url
            assert url + "reports" in seen, (
                "framenavigated did not fire for the pushState: %r" % seen)
    finally:
        srv.shutdown()
