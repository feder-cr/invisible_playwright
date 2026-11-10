"""The <iframe> element of a frame, and the frame of an <iframe> element, with the
frame in another process (Fission, `fission.webContentIsolationStrategy` = 1).

Three local origins, three sites: the top page (127.0.0.1) holds `#payment`
(localhost, `name="pay"`), which holds `#widget` (127.0.0.2, only an `id`). With
sites isolated each is a process of its own. In the process of the embedder an
out-of-process child has no frame of its own, only a browsing context, so the
element behind a frame and the frame behind an element have to be found through
it. Before, `content_frame()` answered None, `frame_element()` refused, and a
frame_locator found nothing.

Needs the patched binary of the Fission build (the same one the step is made
in); the parts that read the engine's answer in isolation are node tests in
automation/juggler/tests (oop_frame_element, content_mux_frame_routes).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

STRATEGY_1 = {"fission.webContentIsolationStrategy": 1}


@pytest.fixture
def oop_origins():
    servers, routes = [], {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = routes.get(self.path, "").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        for address in ("127.0.0.1", "127.0.0.1", "127.0.0.2"):
            server = ThreadingHTTPServer((address, 0), Handler)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        a, b, c = [
            f"http://{host}:{server.server_port}"
            for host, server in zip(("127.0.0.1", "localhost", "127.0.0.2"), servers)
        ]
        routes["/top"] = f"""<!doctype html>
            <iframe id="payment" name="pay" src="{b}/payment"
                    style="margin:60px;border:5px solid;width:600px;height:500px"></iframe>
            <object id="obj" data="{b}/payment" type="text/html" width="200" height="100"></object>
            <embed id="emb" src="{b}/payment" type="text/html" width="200" height="100">
            <object id="empty" width="50" height="50"></object>
            <div id="plain">not a frame</div>"""
        routes["/payment"] = f"""<!doctype html>
            <button id="submit">Submit</button>
            <iframe id="widget" src="{c}/widget"
                    style="margin:35px;border:3px solid;width:400px;height:250px"></iframe>"""
        routes["/widget"] = """<!doctype html>
            <div id="checkbox" role="checkbox" style="width:120px;height:40px">Verify</div>"""
        yield {"top": a + "/top", "b": b, "c": c}
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def oop_page(firefox_binary, oop_origins):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False,
                             headless=True, extra_prefs=STRATEGY_1) as browser:
        page = browser.new_context().new_page()
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(20000)
        page.goto(oop_origins["top"])
        page.wait_for_function("document.querySelector('#payment').contentWindow !== null")
        yield page


def _frames(page):
    owner = page.query_selector("#payment")
    try:
        payment = owner.content_frame()
    finally:
        owner.dispose()
    assert payment is not None, "content_frame() of an out-of-process <iframe> returned None"
    owner = payment.query_selector("#widget")
    try:
        widget = owner.content_frame()
    finally:
        owner.dispose()
    assert widget is not None
    return payment, widget


@pytest.mark.e2e
@pytest.mark.parametrize("selector,has_frame", [
    ("#payment", True), ("#obj", True), ("#emb", True), ("#empty", False), ("#plain", False),
])
def test_content_frame_of_each_embedding_element(oop_page, selector, has_frame, engine_at_least):
    """Step 0 of the step 4 spec: `browsingContext` of the element reads in the
    engine for <iframe>, <object> and <embed>, and is empty for the rest."""
    if selector == "#emb":  # an <embed> of another process has no frame on firefox-61
        engine_at_least("firefox-62")
    handle = oop_page.query_selector(selector)
    try:
        frame = handle.content_frame()
    finally:
        handle.dispose()
    assert (frame is not None) == has_frame


@pytest.mark.e2e
def test_frame_element_and_back(oop_page):
    payment, widget = _frames(oop_page)
    for frame, owner, parent in ((payment, "payment", oop_page.main_frame), (widget, "widget", payment)):
        element = frame.frame_element()
        try:
            assert element.get_attribute("id") == owner
            assert element.owner_frame() == parent
            assert element.content_frame() == frame
        finally:
            element.dispose()


@pytest.mark.e2e
def test_main_frame_has_no_element(oop_page):
    with pytest.raises(Exception, match="no parent"):
        oop_page.main_frame.frame_element()


@pytest.mark.e2e
def test_a_removed_iframe_is_detached(oop_page, engine_at_least):
    engine_at_least("firefox-62")
    payment, _ = _frames(oop_page)
    oop_page.evaluate("document.querySelector('#payment').remove()")
    with pytest.raises(Exception, match="detached"):
        payment.frame_element()


@pytest.mark.e2e
def test_frame_locator_reaches_the_frame(oop_page):
    assert oop_page.frame_locator("#payment").locator("#submit").count() == 1
    assert oop_page.frame_locator("#payment").frame_locator("#widget").locator("#checkbox").count() == 1


@pytest.mark.e2e
def test_a_frame_is_found_by_the_name_or_the_id_of_its_iframe(oop_page):
    _frames(oop_page)
    assert oop_page.frame(name="pay") is not None
    assert oop_page.frame(name="widget") is not None


@pytest.mark.e2e
def test_an_iframe_read_the_moment_it_is_made(oop_page, oop_origins, request):
    """The race of the announce: the browsing context shows up in the embedder
    before the parent has told the client about the frame. The answer may be
    None (reported, not asserted to be 0), never an error."""
    nones = 0
    for _ in range(100):
        found = oop_page.evaluate_handle(
            """src => { const f = document.createElement('iframe'); f.src = src;
                        document.body.append(f); return f; }""",
            oop_origins["b"] + "/payment").as_element()
        try:
            if found.content_frame() is None:
                nones += 1
        finally:
            oop_page.evaluate("document.body.lastElementChild.remove()")
            found.dispose()
    request.node.user_properties.append(("content_frame_none_of_100", nones))
