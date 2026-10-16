"""The guid registry is bounded, and a dispose costs its own subtree.

⛔ BOTH HALVES WERE FOUND TOGETHER, ON 2026-10-16, and they are two defects.
Nothing disposed a Request or a Response, so a page that fetched in a loop kept
every one for the life of the page - in this server and in the client's mirror
of it. And `descendants_of` scanned the whole registry, so every answered Route
paid for every request the page had ever made. The shared MCP browser grew
3.5 GB in an hour under a Bill Pay session, and the scan held its loop at 95%
CPU. The unit tests below pin each half without a browser; the e2e test at the
end proves the client side is released too, which is the half a registry count
alone cannot see.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from invisible_playwright._juggler.dispatcher import Dispatcher, Server


class _Transport:
    def __init__(self):
        self.sent = []

    def emit_message(self, message):
        self.sent.append(message)


class _Root(Dispatcher):
    TYPE = "Page"


class _Leaf(Dispatcher):
    TYPE = "Request"


class _Held(Dispatcher):
    TYPE = "Request"

    @property
    def collectable(self):
        return False


def _server(monkeypatch, limit):
    monkeypatch.setattr(Server, "GC_DEFAULT_LIMIT", limit, raising=False)
    server = Server()
    transport = _Transport()
    server.attach(transport)
    return server, transport


def _gc_disposals(transport):
    return [m["guid"] for m in transport.sent
            if m["method"] == "__dispose__" and m["params"].get("reason") == "gc"]


@pytest.mark.unit
def test_a_type_past_its_limit_disposes_its_oldest_tenth(monkeypatch):
    server, transport = _server(monkeypatch, 100)
    page = _Root(server, None)
    leaves = [_Leaf(server, page) for _ in range(1000)]

    alive = [leaf for leaf in leaves if not leaf.disposed]
    assert len(alive) <= 100
    # Oldest first, and announced to the client as "gc" - the reason the
    # client turns into its "has been collected" error.
    assert all(leaf.disposed for leaf in leaves[:900])
    assert _gc_disposals(transport)[:10] == [leaf.guid for leaf in leaves[:10]]
    assert len(server._objects) == len(alive) + 1
    assert len(server._children[page.guid]) == len(alive)
    assert not page.disposed, "the cap is per type: the page is not a Request"


@pytest.mark.unit
def test_what_may_not_be_collected_is_skipped_not_dropped(monkeypatch):
    server, _ = _server(monkeypatch, 10)
    page = _Root(server, None)
    held = [_Held(server, page) for _ in range(5)]
    for _ in range(100):
        _Leaf(server, page)
    assert not any(h.disposed for h in held)
    assert all(h.guid in server._objects for h in held)


@pytest.mark.unit
def test_disposing_takes_the_subtree_and_leaves_no_index_behind(monkeypatch):
    server, transport = _server(monkeypatch, 10000)
    page = _Root(server, None)
    request = _Leaf(server, page)
    response = _Leaf(server, request)
    grandchild = _Leaf(server, response)
    sibling = _Leaf(server, page)

    request.dispose()

    assert request.disposed and response.disposed and grandchild.disposed
    assert not sibling.disposed
    assert set(server._objects) == {page.guid, sibling.guid}
    assert server._children == {server.root_guid: {page.guid: page},
                                page.guid: {sibling.guid: sibling}}
    assert set(server._buckets["Request"]) == {sibling.guid}
    # One message for the whole subtree, as before.
    disposals = [m["guid"] for m in transport.sent if m["method"] == "__dispose__"]
    assert disposals == [request.guid]


@pytest.mark.unit
def test_an_adopted_child_is_disposed_with_its_new_parent(monkeypatch):
    """⛔ A page's main frame is registered under the CONTEXT and adopted by the
    page once the page exists. Indexed under its first parent, it survived the
    page's close: `test_a_long_session_does_not_accumulate_subscribers` caught
    exactly that, one frame per closed page."""
    server, _ = _server(monkeypatch, 10000)
    context = _Root(server, None)
    frame = _Leaf(server, context)
    page = _Root(server, context)
    frame.parent = page

    page.dispose()

    assert frame.disposed
    assert set(server._objects) == {context.guid}
    assert server._children == {server.root_guid: {context.guid: context}}


@pytest.mark.unit
def test_descendants_are_read_from_the_index_not_the_registry(monkeypatch):
    """⛔ The defect was a scan. A registry full of unrelated objects must not
    be read to answer for one leaf: replace `_objects` with something that
    fails when iterated and the answer must still come back."""
    server, _ = _server(monkeypatch, 10000)
    page = _Root(server, None)
    for _ in range(50):
        _Leaf(server, page)
    leaf = _Leaf(server, page)

    class _NoScan(dict):
        def values(self):
            raise AssertionError("descendants_of scanned the whole registry")

    server._objects = _NoScan(server._objects)
    assert server.descendants_of(leaf) == []
    assert len(server.descendants_of(page)) == 51


class _Echo(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        # The document is HTML: served as JSON, Firefox opens its JSON viewer,
        # and every fetch from that page fails with NetworkError.
        document = self.path == "/"
        body = b"<!doctype html><title>gc</title>" if document else b'{"ok": true}'
        self.send_response(200)
        self.send_header("content-type", "text/html" if document else "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer


@pytest.fixture
def echo_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()


@pytest.mark.e2e
def test_a_page_that_fetches_in_a_loop_stays_bounded(firefox_binary, monkeypatch,
                                                      echo_server):
    """⛔ BOTH SIDES OF THE TRANSPORT. The server's registry is what this
    package owns, but the memory that was swapped out lived equally in the
    client's `Connection._objects`, which only a `__dispose__` releases. With
    a route on, which is what makes the engine send post bodies and what
    disposes a Route per request, the pre-fix build kept every request on
    both sides."""
    from invisible_playwright._pw.sync_api import sync_playwright

    monkeypatch.setattr(Server, "GC_DEFAULT_LIMIT", 50, raising=False)
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True, executable_path=firefox_binary)
        connection = browser._impl_obj._connection
        server = connection._transport._server
        context = browser.new_context()
        context.route("**/*", lambda route: route.fallback())
        page = context.new_page()
        seen = []
        page.on("request", lambda request: seen.append(request))
        page.goto(echo_server + "/")
        page.evaluate("""async () => {
            for (let i = 0; i < 400; i++)
                await fetch('/api?n=' + i, {method: 'POST', body: 'x'.repeat(2000)});
        }""")
        server_requests = sum(1 for o in list(server._objects.values())
                              if o.TYPE == "Request")
        client_requests = sum(1 for o in list(connection._objects.values())
                              if type(o).__name__ == "Request")
        oldest = seen[0]
        try:
            oldest.response()
            collected = None
        except Exception as exc:  # the client's own refusal, raised locally
            collected = str(exc)
        newest = seen[-1].response()
        context.close()
        browser.close()

    assert len(seen) >= 400
    assert server_requests <= 50, "the server kept %d requests" % server_requests
    assert client_requests <= 50, "the client kept %d requests" % client_requests
    assert collected and "collected" in collected, collected
    assert newest is not None and newest.status == 200
