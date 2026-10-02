"""Locators, handles and trusted input through two nested cross-origin frames.

Three local origins: the top page (127.0.0.1) holds `#payment` (localhost),
which holds `#widget` (127.0.0.2). Every test here was red on 0.25.7 with
firefox-34 unless its docstring says it guards something that already worked:

* reads through a nested locator ran in the wrong frame - `inner_text`
  answered "Cannot find object", `bounding_box`, `select_option` and
  `scroll_into_view_if_needed` matched nothing - and a handle from
  `wait_for_selector` belonged to the top frame;
* `frame.parent_frame` was None and `frame.frame_element()` refused;
* `frame.url` stayed where `goto` left it;
* a humanised click in a nested frame, or on a control inside a shadow root,
  always landed on the exact geometric centre: the cursor's own hit test asked
  `document.elementFromPoint` at main-frame coordinates and rejected every
  off-centre point. One number for every click, readable from one event.

The matrices are covering arrays, not cartesian products: every PAIR of values
of the factors appears in some case, which is what a two-factor interaction
needs, at a fraction of the browsers. Each case is one browser, headless.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def nested_origins():
    servers = []
    threads = []
    routes = {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = routes[self.path].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        for address in ("127.0.0.1", "127.0.0.1", "127.0.0.2"):
            server = ThreadingHTTPServer((address, 0), Handler)
            servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        a, b, c = [
            f"http://{host}:{server.server_port}"
            for host, server in zip(("127.0.0.1", "localhost", "127.0.0.2"), servers)
        ]
        routes["/top"] = f"""<!doctype html>
            <iframe id="payment" name="payment" src="{b}/payment"
                    style="margin:60px;border:5px solid;width:600px;height:500px"></iframe>"""
        for mode in ("open", "closed"):
            routes[f"/shadow-{mode}"] = (
                "<!doctype html><div id='host'></div><script>"
                f"host.attachShadow({{mode: '{mode}'}}).innerHTML = "
                + json.dumps(routes["/top"]) + ";</script>"
            )
        routes["/payment"] = f"""<!doctype html>
            <button id="submit">Submit</button><input id="name">
            <select id="choice"><option>a</option><option>b</option></select>
            <iframe id="widget" name="widget" src="{c}/widget"
                    style="margin:35px;border:3px solid;width:400px;height:250px"></iframe>
            <script>
            window.submissions = [];
            submit.addEventListener('click', e => submissions.push(e.isTrusted));
            </script>"""
        routes["/widget"] = """<!doctype html>
            <div id="checkbox" role="checkbox" aria-checked="false"
                 style="width:120px;height:40px">Verify</div>
            <input id="text"><div style="height:1400px"></div><input id="below">
            <script>
            window.clicks = [];
            window.positions = [];
            checkbox.addEventListener('click', e => {
                clicks.push(e.isTrusted);
                positions.push([e.clientX, e.clientY]);
                checkbox.setAttribute('aria-checked',
                    checkbox.getAttribute('aria-checked') === 'false' ? 'true' : 'false');
            });
            </script>"""
        routes["/landed"] = "<!doctype html><title>landed</title><input id='arrived'>"
        routes["/redirect"] = (
            "<!doctype html><script>location.replace("
            + json.dumps(b + "/landed") + ")</script>"
        )
        routes["/favicon.ico"] = ""
        for mode in ("open", "closed"):
            routes[f"/number-{mode}"] = _number_component(mode)
            routes[f"/number-middle-{mode}"] = (
                f'<iframe id="widget" src="{c}/number-{mode}" '
                'style="margin:25px;width:500px;height:300px"></iframe>'
            )
            routes[f"/number-top-{mode}"] = (
                f'<iframe id="payment" src="{b}/number-middle-{mode}" '
                'style="margin:35px;width:650px;height:450px"></iframe>'
            )
        for mode in ("document", "open", "closed"):
            routes[f"/dpr-{mode}"] = _offset_number_component(mode)
            routes[f"/dpr-middle-{mode}"] = (
                f'<iframe id="widget" src="{c}/dpr-{mode}" '
                'style="margin:20px;width:1650px;height:650px"></iframe>'
            )
            routes[f"/dpr-top-{mode}"] = (
                f'<iframe id="payment" src="{b}/dpr-middle-{mode}" '
                'style="margin:20px;width:1740px;height:750px"></iframe>'
            )
        yield {"top": a + "/top", "redirect": a + "/redirect",
               "landed": b + "/landed", "origin": a}
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


def _timed(page):
    """A page whose actions give up fast and whose navigations do not.

    The actions are what these tests judge, so a missing element should fail
    in seconds. Loading three origins is not judged, and with four browsers
    starting at once (`run_e2e.py`'s default) a first navigation took longer
    than 3 s, which read as a failure of whatever test it happened to set up.
    """
    page.set_default_timeout(3000)
    page.set_default_navigation_timeout(20000)
    return page


@pytest.fixture
def nested_page(firefox_binary, nested_origins):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = _timed(browser.new_context().new_page())
        page.goto(nested_origins["top"])
        yield page


@pytest.fixture
def wire(monkeypatch):
    """The pointer's conversation with the engine, kept for the failure
    report: a click that went to the wrong place is diagnosed from the quads it
    was aimed at and the landings the engine reported, not from the page."""
    from invisible_playwright._juggler.connection import Connection

    seen = []
    send = Connection.send

    def record(self, method, params=None, **kwargs):
        answer = send(self, method, params, **kwargs)
        if method in ("Page.getContentQuads", "Page.pointerLanded") or (
            method == "Page.dispatchMouseEvent" and params["type"] != "mousemove"
        ):
            seen.append({"method": method, "params": params, "answer": answer})
        return answer

    monkeypatch.setattr(Connection, "send", record)
    return seen


def frames(page):
    owner = page.query_selector("#payment")
    try:
        payment = owner.content_frame()
    finally:
        owner.dispose()
    owner = payment.query_selector("#widget")
    try:
        widget = owner.content_frame()
    finally:
        owner.dispose()
    return payment, widget


def target(page, via, selector):
    if via == "frame":
        return frames(page)[1].locator(selector)
    return page.frame_locator("#payment").frame_locator("#widget").locator(selector)


@pytest.mark.e2e
def test_nested_parent_chain(nested_page):
    payment, widget = frames(nested_page)
    assert payment.parent_frame == nested_page.main_frame
    assert widget.parent_frame == payment
    assert nested_page.main_frame.child_frames == [payment]
    assert payment.child_frames == [widget]


@pytest.mark.e2e
def test_nested_frame_elements(nested_page):
    payment, widget = frames(nested_page)
    for frame, owner, parent in (
        (payment, "payment", nested_page.main_frame), (widget, "widget", payment)
    ):
        element = frame.frame_element()
        try:
            assert element.get_attribute("id") == owner
            assert element.owner_frame() == parent
            assert element.content_frame() == frame
        finally:
            element.dispose()


@pytest.mark.e2e
@pytest.mark.parametrize("via", ["frame", "frame_locator"])
def test_nested_locator_reads_and_geometry(nested_page, via):
    locator = target(nested_page, via, "#checkbox")
    assert locator.count() == 1
    assert locator.get_attribute("aria-checked") == "false"
    assert locator.inner_text() == "Verify"
    box = locator.bounding_box()
    assert box is not None and box["width"] == 120 and box["height"] == 40
    assert box["x"] > 60 and box["y"] > 60
    locator.scroll_into_view_if_needed()
    handle = locator.element_handle()
    try:
        assert handle.owner_frame() == frames(nested_page)[1]
    finally:
        handle.dispose()


@pytest.mark.e2e
@pytest.mark.parametrize("via", ["frame", "frame_locator"])
def test_nested_fill_and_scroll(nested_page, via):
    locator = target(nested_page, via, "#below")
    assert locator.count() == 1
    locator.scroll_into_view_if_needed()
    locator.fill("nested value")
    assert locator.input_value() == "nested value"
    assert frames(nested_page)[1].evaluate("scrollY") > 0


@pytest.mark.e2e
@pytest.mark.parametrize("humanize,via,action", [
    (False, "frame", "click"),
    (False, "frame_locator", "check"),
    (True, "frame", "check"),
    (True, "frame_locator", "click"),
])
def test_nested_trusted_pointer(firefox_binary, nested_origins, humanize, via, action):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=humanize, headless=True) as browser:
        page = _timed(browser.new_context().new_page())
        page.goto(nested_origins["top"])
        payment, widget = frames(page)
        locator = target(page, via, "#checkbox")
        assert locator.count() == 1
        try:
            getattr(locator, action)()
        except Exception:
            # Evidence distinguishes missed delivery from a false landing report.
            print("checkbox events:", widget.evaluate("clicks"))
            print("checkbox state:", widget.evaluate("checkbox.getAttribute('aria-checked')"))
            raise
        assert widget.evaluate("clicks") == [True]
        if humanize:
            # The centre of the 120x40 checkbox in its own document.
            assert widget.evaluate("positions") != [[68, 28]], (
                "the nested hit test discarded the humanized landing point")
        assert locator.get_attribute("aria-checked") == "true"
        submit = (payment.locator("#submit") if via == "frame"
                  else page.frame_locator("#payment").locator("#submit"))
        submit.click()
        assert payment.evaluate("submissions") == [True]


@pytest.mark.e2e
def test_nested_navigation_updates_url(nested_page, nested_origins):
    _, widget = frames(nested_page)
    destination = nested_origins["landed"]
    with nested_page.expect_event("framenavigated", predicate=lambda f: f.url == destination):
        widget.evaluate("url => location.href = url", destination)
    assert widget.url == widget.evaluate("location.href") == destination
    widget.locator("#arrived").fill("new document")
    assert widget.locator("#arrived").input_value() == "new document"


@pytest.mark.e2e
def test_cross_site_goto_url(nested_page, nested_origins):
    nested_page.goto(nested_origins["redirect"])
    assert nested_page.url == nested_page.evaluate("location.href") == nested_origins["landed"]


@pytest.mark.e2e
def test_same_document_url(nested_page):
    nested_page.evaluate("history.pushState({}, '', '#updated')")
    assert nested_page.url == nested_page.evaluate("location.href")
    payment, widget = frames(nested_page)
    for frame in (payment, widget):
        name = frame.name
        frame.evaluate("history.pushState({}, '', '#updated')")
        assert frame.url == frame.evaluate("location.href")
        assert frame.name == name


@pytest.mark.e2e
def test_frame_locator_additional_readers(nested_page):
    payment = nested_page.frame_locator("#payment")
    field = payment.locator("#name")
    field.fill("payment name")
    assert field.input_value() == "payment name"
    assert field.is_editable()
    assert field.is_visible()
    assert field.evaluate("el => el.value") == "payment name"
    assert field.evaluate_all("els => els.map(el => el.value)") == ["payment name"]
    assert payment.locator("#choice").select_option("b") == ["b"]
    assert payment.locator("#choice").input_value() == "b"
    assert payment.locator("#submit").text_content() == "Submit"
    assert payment.locator("#submit").inner_html() == "Submit"


@pytest.mark.e2e
@pytest.mark.parametrize("mode", ["open", "closed"])
def test_trusted_pointer_with_shadow_frame_owner(nested_page, nested_origins, mode):
    nested_page.goto(nested_origins["origin"] + "/shadow-" + mode)
    payment, widget = frames(nested_page)
    checkbox = target(nested_page, "frame_locator", "#checkbox")
    try:
        checkbox.click()
    except Exception:
        print("checkbox events:", widget.evaluate("clicks"))
        raise
    assert widget.evaluate("clicks") == [True]
    assert checkbox.get_attribute("aria-checked") == "true"
    payment.locator("#submit").click()
    assert payment.evaluate("submissions") == [True]


def _number_component(mode):
    return """<!doctype html><order-cell></order-cell><script>
    window.recorded = [];
    class OrderCell extends HTMLElement {
        connectedCallback() {
            const root = this.attachShadow({mode: MODE});
            root.innerHTML = `
                <style>
                :host { display:block; margin:35px; }
                .ob-row-cell__container { padding:40px; width:280px;
                    background:#ddd; display:flex; align-items:center; gap:12px; }
                input { box-sizing:border-box; width:48px; height:28px; }
                button { width:32px; height:28px; }
                </style>
                <div class="ob-row-cell__container">
                    <button id="minus">-</button>
                    <input id="quantity" type="number" value="1">
                    <button id="plus">+</button>
                </div>`;
            for (const type of ['mousedown', 'mouseup', 'click']) {
                root.addEventListener(type, e => {
                    const target = e.composedPath()[0];
                    recorded.push({type, trusted:e.isTrusted, target:target.id,
                        tag:target.nodeName, x:e.clientX, y:e.clientY});
                }, true);
            }
        }
    }
    customElements.define('order-cell', OrderCell);
    </script>""".replace("MODE", json.dumps(mode))


@pytest.mark.e2e
@pytest.mark.parametrize("mode,nested,layout,humanize", [
    ("open", False, "plain", False),
    ("closed", True, "plain", True),
    ("open", True, "zoom", True),
    ("closed", False, "zoom", False),
    ("open", True, "transform", False),
    ("closed", False, "transform", True),
])
def test_shadow_number_input_click(firefox_binary, nested_origins, wire,
                                   humanize, mode, nested, layout):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=humanize, headless=True) as browser:
        page = _timed(browser.new_context().new_page())
        path = "/number-top-" if nested else "/number-"
        page.goto(nested_origins["origin"] + path + mode)
        frame = frames(page)[1] if nested else page.main_frame
        if layout != "plain":
            frame.locator("order-cell").evaluate(
                "(el, css) => el.style.cssText = css",
                "zoom:1.25" if layout == "zoom"
                else "transform:scale(1.25);transform-origin:0 0")
        locator = frame.locator("input[type=number]")
        assert locator.count() == 1
        assert frame.evaluate("document.querySelectorAll('input').length") == 0
        geometry = locator.evaluate("""el => {
            const r = el.getBoundingClientRect();
            const root = el.getRootNode();
            const hit = root.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
            return {rect:r.toJSON(), hit:hit?.id, tag:hit?.nodeName};
        }""")
        try:
            locator.click()
        finally:
            print(json.dumps({"mode": mode, "nested": nested, "humanize": humanize,
                              "layout": layout, "geometry": geometry, "wire": wire,
                              "events": frame.evaluate("recorded")}, indent=2))
        clicks = frame.evaluate("recorded.filter(e => e.type === 'click')")
        assert len(clicks) == 1
        assert clicks[0]["trusted"] is True
        assert clicks[0]["target"] == "quantity"
        assert clicks[0]["tag"] == "INPUT"
        rect = geometry["rect"]
        assert rect["left"] <= clicks[0]["x"] <= rect["right"]
        assert rect["top"] <= clicks[0]["y"] <= rect["bottom"]
        if humanize:
            center = [round(rect["x"] + rect["width"]/2),
                      round(rect["y"] + rect["height"]/2)]
            assert [clicks[0]["x"], clicks[0]["y"]] != center


@pytest.mark.e2e
@pytest.mark.parametrize("mode", ["open", "closed"])
def test_shadow_landing_rejects_siblings_and_overlays(nested_page, nested_origins, mode):
    """The cursor's landing check, asked directly: the input's own centre is
    accepted, and the padded container, a sibling laid over it and an overlay
    in the frame's document are not the input."""
    from invisible_playwright import _cursor

    nested_page.goto(nested_origins["origin"] + "/number-top-" + mode)
    frame = frames(nested_page)[1]
    handle = frame.locator("input[type=number]").element_handle()
    try:
        # Main-frame coordinates, the space `bounding_box` answers in.
        box = handle.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        def hits():
            return handle._sync(_cursor._hits(handle._impl_obj, x, y))

        assert hits()
        handle.evaluate("""el => {
            el.style.pointerEvents = 'none';
        }""")
        assert not hits(), "the padded ancestor is not the input"
        handle.evaluate("""el => {
            el.style.pointerEvents = '';
            const r = el.getBoundingClientRect();
            const sibling = el.parentElement.querySelector('#plus');
            sibling.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;
                width:${r.width}px;height:${r.height}px;z-index:10;`;
        }""")
        assert not hits(), "the sibling button is not the input"
        handle.evaluate("el => el.parentElement.querySelector('#plus').style.cssText = ''")
        assert hits()
        frame.evaluate("""() => {
            const cover = document.createElement('div');
            cover.style.cssText = 'position:fixed;inset:0;z-index:100;background:white';
            document.body.appendChild(cover);
        }""")
        assert not hits(), "an outer-document overlay must not be ignored"
    finally:
        handle.dispose()


def _offset_number_component(mode):
    return """<!doctype html>
    <div class="ob-product-quantity-cell__container"
         style="position:absolute;left:900px;top:250px;width:330px;height:90px"></div>
    <div id="host"></div><script>
    window.recorded = [];
    const root = MODE === 'document' ? host : host.attachShadow({mode: MODE});
    root.innerHTML = `
        <div class="ob-row-cell__container"
             style="position:absolute;left:1200.17px;top:359.22px;
                    width:330px;height:70px;background:#ddd">
            <button style="position:absolute;left:0;top:20px">-</button>
            <input id="quantity" type="number" value="1"
                   style="position:absolute;box-sizing:border-box;left:40px;top:20px;
                          width:193px;height:30px">
            <button style="position:absolute;left:240px;top:20px">+</button>
        </div>`;
    for (const type of ['mousedown', 'mouseup', 'click']) {
        document.addEventListener(type, e => recorded.push({
            type, trusted:e.isTrusted, target:e.composedPath()[0].nodeName,
            x:e.clientX, y:e.clientY
        }), true);
        root.querySelector('input').addEventListener(type, e => recorded.push({
            type, trusted:e.isTrusted, target:'input', x:e.clientX, y:e.clientY
        }));
    }
    </script>""".replace("MODE", json.dumps(mode))


@pytest.mark.e2e
@pytest.mark.parametrize("dpr,mode,nested,humanize", [
    (1, "document", False, False),
    (1.2, "closed", True, True),
    (1.25, "open", True, True),
    (1.25, "document", False, True),
    (1.5, "document", True, True),
    (1.5, "closed", False, False),
    (2, "open", True, False),
    (2, "closed", True, True),
])
def test_pointer_at_large_offset_with_dpr(firefox_binary, nested_origins, wire,
                                          dpr, mode, nested, humanize):
    """A device scale (`screen.dpr`) is not a page zoom: Playwright's
    coordinates stay CSS pixels and nothing here multiplies them. A fractional
    scale at an x past 1200 is where a rounding in the frame shift would show."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(
        seed=20260929, binary_path=firefox_binary, humanize=humanize, headless=True,
        timezone="America/Chicago", locale="en-US",
        pin={"screen.dpr": dpr, "screen.width": 1920, "screen.height": 1080},
    ) as browser:
        page = _timed(browser.new_context().new_page())
        path = "/dpr-top-" if nested else "/dpr-"
        page.goto(nested_origins["origin"] + path + mode)
        frame = frames(page)[1] if nested else page.main_frame
        actual_dpr = frame.evaluate("devicePixelRatio")
        assert actual_dpr == pytest.approx(dpr)
        locator = frame.locator("input[type=number]")
        rect = locator.evaluate("el => el.getBoundingClientRect().toJSON()")
        assert rect["x"] == pytest.approx(1240.17, abs=0.02)
        assert locator.evaluate("""el => {
            const r = el.getBoundingClientRect();
            return el.getRootNode().elementFromPoint(
                r.x + r.width/2, r.y + r.height/2) === el;
        }""")
        try:
            locator.click()
        finally:
            print(json.dumps({
                "dpr": actual_dpr, "humanize": humanize, "mode": mode, "nested": nested,
                "rect": rect, "wire": wire, "events": frame.evaluate("recorded"),
            }, indent=2))
        clicks = frame.evaluate(
            "recorded.filter(e => e.type === 'click' && e.target === 'input')")
        assert len(clicks) == 1
        assert clicks[0]["trusted"]
        assert rect["left"] <= clicks[0]["x"] <= rect["right"]
        assert rect["top"] <= clicks[0]["y"] <= rect["bottom"]
        if humanize:
            center = [round(rect["x"] + rect["width"]/2),
                      round(rect["y"] + rect["height"]/2)]
            assert [round(clicks[0]["x"]), round(clicks[0]["y"])] != center
