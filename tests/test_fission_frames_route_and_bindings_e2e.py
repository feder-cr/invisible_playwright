"""Routes, exposed functions, init scripts and postMessage, for an iframe of another site and one nested in it.

Fission puts a frame of another site in a content process of its own. What a script written against the Playwright
API does with such a frame is the same as with any other, and this file holds the parts that go through the
frame's own process:

* `page.route` and `context.route` see the requests the frame makes, `request.frame` is that frame (the same
  object `page.frames` lists, with the url of its document), and fulfill, abort and continue each take effect in the
  frame; `wait_for_load_state("networkidle")` waits for the late requests of the frame;
* `expose_function` and `expose_binding` are callable from the frame and from a frame nested in it, and the
  binding's `source["frame"]` is the frame that called;
* an init script runs before the frame's own scripts, in the frame, in the nested frame, and again in the document
  the frame navigates to (a third site);
* `postMessage` crosses between the top and a frame of another site both ways, and between two sibling frames of
  other sites through the top: `event.origin` is the sender's, `event.source` answers.

The sites are told apart by host (see `_fission_frames.py`): the top is A (`127.0.0.1`), the frame is B
(`localhost`) and the frame nested in it is C (`127.0.0.2`).

The outcome asserted is the one of stock Playwright Firefox; none of it depends on the process model, and the
first test proves the frame is not in the process of the page, so the file cannot turn into a same-process suite.
"""
from __future__ import annotations

import json

import pytest

from _fission_frames import Sites, assert_other_process, launch, new_page, wait_frame

pytestmark = pytest.mark.e2e


@pytest.fixture
def sites():
    with Sites() as s:
        # A top page with a frame of B; the frame of B holds a frame of C. Each document reports through
        # window.__log what it did, so a test reads it with evaluate.
        s.route("A", "/top", '<!doctype html><title>top</title><iframe id="b" name="b" src="%s" '
                'width="500" height="300"></iframe>' % s.url("B", "/frame"))
        s.route("B", "/frame", '<!doctype html><title>frame</title><iframe id="c" name="c" src="%s" '
                'width="300" height="150"></iframe>'
                "<script>window.__got = [];"
                "fetch('/api/b?from=load').then(r => r.text()).then(t => __got.push(['load', t]))"
                ".catch(e => __got.push(['load-error', String(e)]));</script>" % s.url("C", "/nested"))
        s.route("C", "/nested", "<!doctype html><title>nested</title>"
                "<script>window.__got = [];"
                "fetch('/api/c?from=load').then(r => r.text()).then(t => __got.push(['load', t]))"
                ".catch(e => __got.push(['load-error', String(e)]));</script>")
        s.route("B", "/api/b", "from-server-b", ctype="text/plain")
        s.route("C", "/api/c", "from-server-c", ctype="text/plain")
        yield s


@pytest.fixture(scope="module")
def browser(firefox_binary):
    with launch(firefox_binary) as browser:
        yield browser


@pytest.fixture
def page(browser):
    context, page = new_page(browser)
    yield page
    context.close()


def _frames(page, sites):
    """The frame of B and the frame of C nested in it, once both have a document."""
    b = wait_frame(page, sites.url("B"), "/frame")
    c = wait_frame(page, sites.url("C"), "/nested")
    return b, c


def test_the_frame_is_in_another_process_and_the_nested_one_in_a_third(page, sites):
    """The precondition of this file: B is not in the process of A, and C is not in the one of B."""
    page.goto(sites.url("A", "/top"), wait_until="load")
    b, c = _frames(page, sites)
    assert b.parent_frame == page.main_frame and c.parent_frame == b
    assert_other_process(page.main_frame, b)
    assert_other_process(b, c)


# ---------------------------------------------------------------------------
# page.route and context.route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["page", "context"])
def test_a_route_sees_the_requests_of_a_frame_of_another_site_and_of_a_nested_one(page, sites, where):
    """The handler is called for the fetch of the frame of B and the one of the frame of C nested in it,
    `request.frame` is the frame that made it (the object of `page.frames`, with its url), the request is a
    sub-resource of that frame's document, and continuing it gives the page the server's answer."""
    context = page.context
    seen = []

    def handler(route):
        request = route.request
        seen.append((request.url, request.frame, request.resource_type))
        route.continue_()

    (page if where == "page" else context).route("**/api/**", handler)
    page.goto(sites.url("A", "/top"), wait_until="load")
    b, c = _frames(page, sites)
    for frame in (b, c):
        frame.wait_for_function("window.__got.length > 0")
    assert b.evaluate("window.__got") == [["load", "from-server-b"]]
    assert c.evaluate("window.__got") == [["load", "from-server-c"]]
    by_url = {url.split("?")[0]: (frame, kind) for url, frame, kind in seen}
    assert set(by_url) == {sites.url("B", "/api/b"), sites.url("C", "/api/c")}, seen
    frame_of_b, kind_b = by_url[sites.url("B", "/api/b")]
    frame_of_c, kind_c = by_url[sites.url("C", "/api/c")]
    assert frame_of_b is b and frame_of_b.url == sites.url("B", "/frame")
    assert frame_of_c is c and frame_of_c.url == sites.url("C", "/nested")
    assert frame_of_c.parent_frame is b
    assert kind_b == kind_c == "fetch"


@pytest.mark.parametrize("action", ["fulfill", "abort", "continue"])
@pytest.mark.parametrize("target", ["frame", "nested"])
def test_fulfill_abort_and_continue_take_effect_in_a_frame_of_another_site(page, sites, action, target):
    """What the route decides is what the frame's fetch gets: the body and status given to `fulfill`, a rejected
    fetch for `abort`, the server's answer for `continue`. A fetch the frame makes AFTER the load, to a url the
    route covers."""
    page.goto(sites.url("A", "/top"), wait_until="load")
    b, c = _frames(page, sites)
    frame, site = (b, "B") if target == "frame" else (c, "C")
    sites.route(site, "/api/late", "from-server", ctype="text/plain")
    routed = []

    def handler(route):
        routed.append(route.request.frame)
        if action == "fulfill":
            route.fulfill(status=201, body="from-route", content_type="text/plain",
                          headers={"access-control-allow-origin": "*"})
        elif action == "abort":
            route.abort()
        else:
            route.continue_()

    page.route("**/api/late", handler)
    result = frame.evaluate("""() => fetch('/api/late').then(
        r => r.text().then(t => ({ok: true, status: r.status, body: t})),
        e => ({ok: false, error: e.name}))""")
    assert routed == [frame], routed
    if action == "fulfill":
        assert result == {"ok": True, "status": 201, "body": "from-route"}
    elif action == "abort":
        assert result["ok"] is False and result["error"] == "TypeError", result
        assert not sites.seen(site, "/api/late"), "an aborted request reached the server"
    else:
        assert result == {"ok": True, "status": 200, "body": "from-server"}
        assert len(sites.seen(site, "/api/late")) == 1


def test_a_route_set_before_the_load_fulfills_what_the_frame_asks_while_loading(page, sites):
    """The first request of the frame of another site is covered: the route is in place before the page goes to
    its url, and the fetch the frame makes while loading is answered by the route."""
    page.route("**/api/b**", lambda route: route.fulfill(body="routed-b", content_type="text/plain"))
    page.goto(sites.url("A", "/top"), wait_until="load")
    b, _ = _frames(page, sites)
    b.wait_for_function("window.__got.length > 0")
    assert b.evaluate("window.__got") == [["load", "routed-b"]]
    assert not sites.seen("B", "/api/b"), "the request reached the server through the route"


def test_networkidle_waits_for_the_late_requests_of_a_frame_of_another_site(page, sites):
    """The frame makes a request 200 ms after it loads, and the next one as soon as that answers (the server holds
    each for 300 ms): there is never half a second without a request, so `networkidle` of the PAGE is not reached
    before both are done."""
    chain = ("<!doctype html><title>late</title><script>window.__done = [];"
             "addEventListener('load', () => setTimeout(() => fetch('/late/1').then(r => r.text())"
             ".then(t => { __done.push(t); return fetch('/late/2'); }).then(r => r.text())"
             ".then(t => __done.push(t)), 200));</script>")
    sites.route("B", "/late-frame", chain)
    sites.route("B", "/late/1", "one", ctype="text/plain", delay=0.3)
    sites.route("B", "/late/2", "two", ctype="text/plain", delay=0.3)
    sites.route("A", "/late-top", '<!doctype html><title>late top</title>'
                '<iframe src="%s" width="300" height="100"></iframe>' % sites.url("B", "/late-frame"))
    page.goto(sites.url("A", "/late-top"), wait_until="networkidle", timeout=30000)
    frame = wait_frame(page, sites.url("B"), "/late-frame")
    assert frame.evaluate("window.__done") == ["one", "two"]
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# expose_function, expose_binding and init scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["page", "context"])
def test_an_exposed_function_is_callable_from_a_frame_of_another_site_and_a_nested_one(page, sites, where):
    context = page.context
    (page if where == "page" else context).expose_function("add", lambda a, b: a + b)
    page.goto(sites.url("A", "/top"), wait_until="load")
    b, c = _frames(page, sites)
    assert b.evaluate("add(2, 3)") == 5
    assert c.evaluate("add(40, 2)") == 42
    assert c.evaluate("typeof window.add") == "function"


@pytest.mark.parametrize("where", ["page", "context"])
def test_a_binding_knows_which_frame_called_it(page, sites, where):
    """`source["frame"]` is the frame that called: the frame of B, then the frame of C nested in it, then the
    top; `source["page"]` is the page."""
    context = page.context
    calls = []

    def binding(source, value):
        calls.append((source["frame"], source["page"], value))
        return "ack:%s" % value

    (page if where == "page" else context).expose_binding("report", binding)
    page.goto(sites.url("A", "/top"), wait_until="load")
    b, c = _frames(page, sites)
    assert c.evaluate("report('from-c')") == "ack:from-c"
    assert b.evaluate("report('from-b')") == "ack:from-b"
    assert page.main_frame.evaluate("report('from-top')") == "ack:from-top"
    assert [(f, p is page, v) for f, p, v in calls] == [(c, True, "from-c"), (b, True, "from-b"),
                                                       (page.main_frame, True, "from-top")]
    assert calls[0][0].url == sites.url("C", "/nested")
    assert calls[1][0].url == sites.url("B", "/frame")


def test_a_binding_called_by_a_script_of_the_frame_while_it_loads(page, sites):
    """A page script of the frame of B calls the binding in its own first script, before any `evaluate`: the
    binding is already there and knows the frame."""
    sites.route("B", "/binds", "<!doctype html><title>binds</title><script>window.__ack = null;"
                "report('while-loading').then(v => window.__ack = v);</script>")
    sites.route("A", "/binds-top", '<!doctype html><iframe src="%s"></iframe>' % sites.url("B", "/binds"))
    calls = []

    def binding(source, value):
        calls.append((source["frame"].url, value))
        return "ack"

    page.expose_binding("report", binding)
    page.goto(sites.url("A", "/binds-top"), wait_until="load")
    frame = wait_frame(page, sites.url("B"), "/binds")
    frame.wait_for_function("window.__ack !== null")
    assert calls == [(sites.url("B", "/binds"), "while-loading")]


ORDER_PAGE = ("<!doctype html><title>%s</title><script>"
              "window.__seen = JSON.stringify(window.__o || []);"
              "(window.__o = window.__o || []).push('page');</script>")


@pytest.mark.parametrize("where", ["page", "context"])
def test_an_init_script_runs_before_the_scripts_of_a_frame_of_another_site(page, sites, where):
    """In the top, in the frame of B and in the frame of C: the init script has pushed 'init' before the
    document's own first script reads the list. The same, again, in the document the frame of B navigates to on a
    third site (C): the new document in the new process gets the script before its own."""
    context = page.context
    script = "(window.__o = window.__o || []).push('init');"
    (page if where == "page" else context).add_init_script(script)
    sites.route("A", "/order-top", ORDER_PAGE.replace("%s", "top")
                + '<iframe src="%s"></iframe>' % sites.url("B", "/order-b"))
    sites.route("B", "/order-b", ORDER_PAGE.replace("%s", "b")
                + '<iframe src="%s"></iframe>' % sites.url("C", "/order-c"))
    sites.route("C", "/order-c", ORDER_PAGE.replace("%s", "c"))
    sites.route("C", "/order-moved", ORDER_PAGE.replace("%s", "moved"))
    page.goto(sites.url("A", "/order-top"), wait_until="load")
    b = wait_frame(page, sites.url("B"), "/order-b")
    c = wait_frame(page, sites.url("C"), "/order-c")
    for frame in (page.main_frame, b, c):
        assert frame.evaluate("window.__seen") == '["init"]', frame.url
        assert frame.evaluate("window.__o") == ["init", "page"], frame.url
    b.goto(sites.url("C", "/order-moved"))
    assert b.url == sites.url("C", "/order-moved")
    assert b.evaluate("window.__seen") == '["init"]'
    assert b.evaluate("window.__o") == ["init", "page"]
    # and the process changed with the site: the moved frame is not in the top's process either
    assert_other_process(page.main_frame, b)


# ---------------------------------------------------------------------------
# postMessage
# ---------------------------------------------------------------------------

ECHO = ("<!doctype html><title>%(name)s</title><script>window.__in = [];"
        "addEventListener('message', e => { __in.push({origin: e.origin, data: e.data, "
        "fromParent: e.source === parent});"
        "if (e.data && e.data.ping) e.source.postMessage({pong: e.data.ping, by: %(name)s}, e.origin); });"
        "</script>")


def test_post_message_crosses_between_the_top_and_a_frame_of_another_site_both_ways(page, sites):
    """The top asks the frame of B, the frame answers on `event.source`, the top reads the answer with the origin
    of B and `event.source` equal to the frame's window; and the frame speaks first to its parent."""
    sites.route("B", "/echo", ECHO % {"name": '"b"'} +
                "<script>parent.postMessage({hello: 'from-b'}, '*');</script>")
    sites.route("A", "/msg-top", "<!doctype html><title>top</title><iframe id='f' src='%s'></iframe><script>"
                "window.__in = []; addEventListener('message', e => __in.push({origin: e.origin, data: e.data, "
                "fromFrame: e.source === document.getElementById('f').contentWindow}));</script>"
                % sites.url("B", "/echo"))
    page.goto(sites.url("A", "/msg-top"), wait_until="load")
    frame = wait_frame(page, sites.url("B"), "/echo")
    page.wait_for_function("window.__in.length >= 1")
    assert page.evaluate("window.__in[0]") == {"origin": sites.url("B"), "data": {"hello": "from-b"},
                                                "fromFrame": True}
    page.evaluate("document.getElementById('f').contentWindow.postMessage({ping: 7}, %s)" % json.dumps(sites.url("B")))
    page.wait_for_function("window.__in.length >= 2")
    assert page.evaluate("window.__in[1]") == {"origin": sites.url("B"), "data": {"pong": 7, "by": "b"},
                                                "fromFrame": True}
    assert frame.evaluate("window.__in") == [{"origin": sites.url("A"), "data": {"ping": 7}, "fromParent": True}]


def test_post_message_between_two_sibling_frames_of_other_sites_goes_through_the_top(page, sites):
    """Frames of B and C side by side, in a page of A. They cannot see each other, only the top: B writes to the
    top, the top relays to C naming the sender, C answers the top on `event.source`, the top relays back. Every
    hop shows the origin of the document that sent it."""
    sites.route("B", "/left", "<!doctype html><title>left</title><script>window.__in = [];"
                "addEventListener('message', e => __in.push({origin: e.origin, data: e.data}));"
                "parent.postMessage({to: 'right', text: 'hi from left'}, '*');</script>")
    sites.route("C", "/right", "<!doctype html><title>right</title><script>window.__in = [];"
                "addEventListener('message', e => { __in.push({origin: e.origin, data: e.data});"
                "e.source.postMessage({to: 'left', text: 'hi from right'}, e.origin); });</script>")
    # The top relays a message only once the frame it is for has loaded: the two frames load in two processes, in no
    # fixed order, and a message posted to a frame whose document is not there yet is lost in any browser. The first
    # version relayed at once, and one CI run in three never saw the answer.
    sites.route("A", "/relay", "<!doctype html><title>relay</title><script>"
                "window.__hops = []; window.__loaded = {}; window.__waiting = [];"
                "const win = id => document.getElementById(id).contentWindow;"
                "function deliver() { __waiting = __waiting.filter(m => {"
                "  if (!__loaded[m.to]) return true; win(m.to).postMessage(m.message, m.origin); return false; }); }"
                "function loaded(id) { __loaded[id] = true; deliver(); }"
                "addEventListener('message', e => {"
                "  const from = e.source === win('left') ? 'left' : e.source === win('right') ? 'right' : '?';"
                "  __hops.push({from, origin: e.origin, data: e.data});"
                "  if (e.data.to) { __waiting.push({to: e.data.to, message: {relayed: e.data.text, by: from},"
                "                                   origin: from === 'left' ? %s : %s}); deliver(); }"
                "});</script>"
                "<iframe id='left' onload=\"loaded('left')\" src='%s'></iframe>"
                "<iframe id='right' onload=\"loaded('right')\" src='%s'></iframe>"
                % (json.dumps(sites.url("C")), json.dumps(sites.url("B")),
                   sites.url("B", "/left"), sites.url("C", "/right")))
    page.goto(sites.url("A", "/relay"), wait_until="load")
    left = wait_frame(page, sites.url("B"), "/left")
    right = wait_frame(page, sites.url("C"), "/right")
    page.wait_for_function("window.__hops.length >= 2")
    left.wait_for_function("window.__in.length >= 1")
    assert page.evaluate("window.__hops") == [
        {"from": "left", "origin": sites.url("B"), "data": {"to": "right", "text": "hi from left"}},
        {"from": "right", "origin": sites.url("C"), "data": {"to": "left", "text": "hi from right"}},
    ]
    assert right.evaluate("window.__in") == [
        {"origin": sites.url("A"), "data": {"relayed": "hi from left", "by": "left"}}]
    assert left.evaluate("window.__in") == [
        {"origin": sites.url("A"), "data": {"relayed": "hi from right", "by": "right"}}]
    assert_other_process(left, right)
