"""The seam where Node stops being necessary, exercised through the PUBLIC API.

⛔ THE POINT OF THESE TESTS IS THAT THEY DO NOT KNOW ABOUT `_juggler`. They call
`InvisiblePlaywright` exactly the way a user does, and the only thing that
changes is one environment variable. A test written against the server directly
would prove the server works and say nothing about the thing that matters: that
823 generated API methods, the channel layer, the guid registry and the sync
facade all still function with a different object underneath them.
"""
from __future__ import annotations

import http.server
import os
import pathlib
import socketserver
import threading

import pytest

from invisible_playwright._juggler import transport as factory
from invisible_playwright._juggler.connection import EventListeners

PAGE = b"""<!doctype html><html><head><title>seam</title></head><body>
<button id=b onclick="this.dataset.n=(+(this.dataset.n||0)+1)">press</button>
<input id=f>
<div id=t>hello</div>
<ul><li class=x>one</li><li class=x>two</li><li class=x>three</li></ul>
<input id=c type=checkbox>
<select id=s><option value=a>A</option><option value=b>B</option></select>
<div id=late></div>
<a id=next href="/second">go</a>
<script>
  setTimeout(function () {
    document.getElementById('late').innerHTML = '<span id=slow>arrived</span>';
  }, 800);
</script>
</body></html>"""

SECOND = b"""<!doctype html><html><head><title>second</title></head>
<body><div id=t>page two</div></body></html>"""


def _serve(body, asked=None):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            page = SECOND if self.path.startswith("/second") else body
            if asked is not None:
                asked.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass

    return H


# ── without a browser ───────────────────────────────────────────────────────

def test_a_name_that_is_not_the_driver_gets_the_only_transport(monkeypatch):
    """⛔ THIS ASSERTED THE OPPOSITE UNTIL 2026-08-29, and the reason it
    changed is that its reason expired.

    The switch used to REFUSE an unknown value, arguing that a typo would
    otherwise run the driver while the caller believed they were measuring the
    Python path - a bench arm that is not what it says it is. That was true
    while there were two transports. There is one: any value that is not
    `driver` correctly yields the only thing there is, and a guard that
    protects against nothing is a concept for nothing.

    What still has to hold is the OTHER half, tested below: `driver` by name
    must still explain what happened to it, because 0.7.4 shipped with the
    driver as the default and somebody can have it set in a shell.
    """
    monkeypatch.setenv(factory.CHOICE_ENV, "jugler")
    assert factory.chosen() == "jugler"
    monkeypatch.setenv(factory.CHOICE_ENV, "anything-at-all")
    assert factory.chosen() != factory.DRIVER, (
        "only the exact name `driver` may reach the migration message")


def test_the_default_is_the_PYTHON_SERVER(monkeypatch):
    """⛔ IT FLIPPED ON 2026-08-28, AND ONLY ON EVIDENCE.

    While the server matured the default was the driver, on the reasoning that
    a half-finished server which silently became the default would turn every
    user session into an experiment. What flipped it is four measurements taken
    on one code state, not the code looking done: `run_e2e.py` 188 passed on
    BOTH transports; the transport judge 50 green on both with 0 red only on
    ours; `diff_protocol.py` at parity on methods, parameters, object types,
    initializer fields, events and parentage; and the realness gates green on
    this path for the first time.

    ⛔ The assertion is on the DEFAULT, not on the driver being gone. The
    driver stays reachable by name: it is the only second arm this project has,
    and two of its gates exist only while there are two.
    """
    monkeypatch.delenv(factory.CHOICE_ENV, raising=False)
    assert factory.chosen() == factory.JUGGLER


def test_the_driver_is_still_reachable_by_name(monkeypatch):
    """⛔ The way back has to keep working, or the flip above is one-way.

    A user whose session behaves differently on the new path must be able to
    say so and get the old one, and `judge_both_transports.py` needs it to have
    a second arm at all.
    """
    monkeypatch.setenv(factory.CHOICE_ENV, "driver")
    assert factory.chosen() == factory.DRIVER


def test_the_serializer_TAGS_values_instead_of_sending_them_bare():
    """⛔ Playwright does not send JSON, it sends a tagged union, and
    `parse_value` reads the tag. A bare `True` where `{"b": true}` is expected
    does not raise: it falls through to the object branch and comes back as
    something else entirely."""
    from invisible_playwright._juggler.server import _serialize
    assert _serialize(None) == {"v": "null"}
    assert _serialize(True) == {"b": True}
    assert _serialize(3) == {"n": 3}
    assert _serialize("x") == {"s": "x"}
    # ⛔ A CONTAINER MUST CARRY AN `id`. `parse_value` writes
    # `refs[value["id"]]` unconditionally, so its absence is a bare KeyError
    # from inside the client, several frames away from the cause. The ids are
    # what lets a cyclic structure point back at itself; we never emit a `ref`,
    # but the reader indexes on the id anyway.
    assert _serialize([1, "a"]) == {"a": [{"n": 1}, {"s": "a"}], "id": 1}
    assert _serialize({"k": 1}) == {"o": [{"k": "k", "v": {"n": 1}}], "id": 1}
    # ⛔ A bool is an int in Python, so the order of the checks is the whole
    # correctness of this function: reversed, `True` would leave as `{"n": 1}`.
    assert _serialize(False) == {"b": False}


def test_an_object_the_server_never_created_is_REFUSED_by_name():
    """A guid that is not in the registry must say so, not raise KeyError."""
    from invisible_playwright._juggler.dispatcher import ProtocolException
    from invisible_playwright._juggler.server import JugglerServer
    server = JugglerServer()
    with pytest.raises(ProtocolException) as failure:
        server.handle({"id": 1, "guid": "page@99", "method": "close",
                       "params": {}})
    assert "page@99" in str(failure.value)


def test_an_out_of_perimeter_object_refuses_with_a_REASON():
    """⛔ Section 5.4: out of perimeter fails LOUDLY. Never a no-op, never an
    AttributeError - a reason the caller can act on."""
    from invisible_playwright._juggler.dispatcher import ProtocolException
    from invisible_playwright._juggler.server import (JugglerServer,
                                                      TracingDispatcher)
    server = JugglerServer()
    tracing = TracingDispatcher(server, None)
    with pytest.raises(ProtocolException) as failure:
        tracing.call("tracingStart", {})
    message = str(failure.value)
    assert "Tracing" in message and "tracingStart" in message
    assert "deliberate refusal" in message, (
        "the refusal does not distinguish itself from a gap: %s" % message)


def test_create_is_announced_BEFORE_anything_can_name_the_object():
    """⛔ `Connection.dispatch` looks the guid up in a plain dict and raises
    `Cannot find object` when it is missing, so an out-of-order create is not a
    race that usually works - it is a hard failure."""
    from invisible_playwright._juggler.dispatcher import Dispatcher, Server

    sent: list = []

    class Recording:
        def emit_message(self, message):
            sent.append(message)

    class Thing(Dispatcher):
        TYPE = "Thing"

    server = Server()
    server.attach(Recording())
    thing = Thing(server, None, {"a": 1})
    assert sent, "nothing was announced at all"
    first = sent[0]
    assert first["method"] == "__create__"
    assert first["params"]["guid"] == thing.guid
    assert first["params"]["initializer"] == {"a": 1}
    assert first["guid"] == "", "a top-level object must be parented to Root"


# ── with a browser, through the public API ──────────────────────────────────

@pytest.mark.e2e
def test_the_public_API_drives_a_page_WITHOUT_node(firefox_binary):
    """⛔ THE TEST THIS WHOLE SUBSYSTEM EXISTS FOR.

    Nothing below mentions `_juggler`. It is the ordinary user-facing API, and
    the only difference from any other session is one environment variable. If
    this passes, the channel layer, the guid registry, the object factory, the
    823 generated methods and the sync facade are all working against a
    different object underneath - which is the entire claim of the detachment.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            assert page.title() == "seam"
            assert "hello" in page.content()
            page.click("#b")
            page.fill("#f", "typed")
            assert page.input_value("#f") == "typed"
            assert page.text_content("#t") == "hello"
            assert page.is_visible("#b")
            box = page.query_selector("#b").bounding_box()
            assert box and box["width"] > 0
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_the_public_API_reads_MANY_elements_without_node(firefox_binary):
    """`query_selector_all`, `count` and `eval_on_selector_all`.

    ⛔ Each handle holds a DOM node alive until it is disposed, so a page with a
    thousand matches leaks a thousand nodes. That is a property of the protocol,
    not of this test, and it is written down where the handles are made.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            found = page.query_selector_all("li.x")
            assert len(found) == 3, "query_selector_all returned %d" % len(found)
            assert [h.text_content() for h in found] == ["one", "two", "three"]
            assert page.eval_on_selector("#t", "el => el.textContent") == "hello"
            assert page.eval_on_selector_all(
                "li.x", "els => els.length") == 3
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_the_public_API_WAITS_for_an_element_that_arrives_late(firefox_binary):
    """⛔ THE POINT OF `wait_for_selector`, and the reason it cannot go through
    the retry loop: that loop disposes what it resolves on every turn, so it
    would hand back a handle it has just released - and a released handle does
    not raise, it answers wrong. The element below arrives after 800 ms."""
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            assert page.query_selector("#slow") is None, (
                "the element was already there: the test proves nothing")
            handle = page.wait_for_selector("#slow", timeout=8000)
            assert handle is not None
            assert handle.text_content() == "arrived"
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_going_BACK_then_FORWARD_leaves_the_page_readable(firefox_binary):
    """⛔ TWO MOVEMENTS, AND A READ FROM THE PAGE. One of each is what the two
    earlier tests did, and between them they missed the defect entirely.

    Going back once worked from firefox-31 and a locator read fine, so a bench
    that stops there is green. The second movement is where it broke: the client
    cached the injected script per FRAME, and the script is an object inside one
    CONTEXT, so after a restore it was asking the engine for an object in a world
    that no longer existed. `Cannot find object with id`, while `evaluate()` and
    `content()` kept answering from the right document, because those resolve the
    context afresh every time.

    So this walks back, forward, and back again, and READS THROUGH A LOCATOR each
    time. Asserting on `page.url` would pass against the defect; so would
    counting requests, which is what the restore test next door does for a
    different and still valid reason. The three ways of being green stop looking
    alike.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            page.goto(url + "second")
            # ⛔ A LOCATOR HERE, BEFORE MOVING. It is what mints the handle, and
            # without it the first restore builds a fresh one and the defect
            # hides one movement further along. The bench has to arrive at the
            # restore already holding something.
            assert page.locator("#t").inner_text() == "page two"

            page.go_back(timeout=5000)
            assert page.locator("#t").inner_text() == "hello"

            page.go_forward(timeout=5000)
            assert page.locator("#t").inner_text() == "page two"

            page.go_back(timeout=5000)
            assert page.locator("#t").inner_text() == "hello"

            page.reload()
            assert page.locator("#t").inner_text() == "hello"
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_set_content_runs_in_the_MAIN_world_without_node(firefox_binary):
    """⛔ THE KNOWN-BAD OF `set_content`. Run from the utility world it answers
    `The operation is insecure` EVERY time: that world has an extended
    principal, and Gecko requires `document.open()` to run under a principal
    equal to the document's. The fork already fixed this inside the driver; the
    Python server has to get it right for the same reason, not by luck.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.set_content("<html><head><title>written</title></head>"
                             "<body><p id=w>from set_content</p></body></html>")
            assert page.title() == "written"
            assert page.text_content("#w") == "from set_content"
            # And an apostrophe in the html must not close the JavaScript
            # literal it travels inside: that is the 2026-08-24 defect.
            page.set_content("<p id=q>it's fine</p>")
            assert page.text_content("#q") == "it's fine"
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)


@pytest.mark.e2e
def test_the_public_API_selects_and_checks_without_node(firefox_binary):
    """`select_option` and `check`, through the generated API."""
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            assert page.input_value("#s") == "a"
            page.select_option("#s", "b")
            assert page.input_value("#s") == "b", (
                "a bare string picked the first option instead of the asked one")
            assert not page.is_checked("#c")
            page.check("#c")
            assert page.is_checked("#c")
            page.uncheck("#c")
            assert not page.is_checked("#c")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


# ── closed shadow roots ─────────────────────────────────────────────────────

SHADOW = b"""<!doctype html><html><head><title>shadow</title></head><body>
<div id=host></div>
<div id=openhost></div>
<script>
  const closed = document.getElementById('host').attachShadow({mode: 'closed'});
  closed.innerHTML = '<p id=secret>hidden text</p><div id=inner></div>';
  const nested = closed.getElementById('inner')
      .attachShadow({mode: 'closed'});
  nested.innerHTML = '<p id=deep>two levels down</p>';
  const open_ = document.getElementById('openhost').attachShadow({mode: 'open'});
  open_.innerHTML = '<p id=visible>open text</p>';
</script>
</body></html>"""


@pytest.mark.e2e
def test_locators_reach_INSIDE_a_closed_shadow_root(firefox_binary):
    """⛔ Side A of the closed-shadow-root research, verified on the product.

    The engine patch lives in `Element::GetShadowRootForBindings` and is gated
    on the ExpandedPrincipal, so the utility world sees a closed root and the
    page does not. This asserts the automation half; the test below asserts
    the half that matters more - that the page gained nothing.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(SHADOW))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            assert page.text_content("#secret") == "hidden text"
            assert page.text_content("#visible") == "open text"
            assert page.text_content("#deep") == "two levels down", (
                "a root nested inside a closed root was not reached")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_the_PAGE_still_sees_nothing_of_a_closed_root(firefox_binary):
    """⛔ THE HALF THAT MATTERS MORE, and it is the known-bad of the engine
    patch. A real Firefox NEVER hands a closed root to content: if our build
    did, `!!el.shadowRoot` on a closed host would be a one-line detector that
    no fingerprint suite would even need to be clever to run.

    So the assertion is not "automation works" but "the page is unchanged".
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(SHADOW))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            # `evaluate` runs in the page's own world, which is the whole
            # point: this is what a site would see.
            assert page.evaluate(
                "() => document.getElementById('host').shadowRoot") is None, (
                "the PAGE can see a closed shadow root: that is a one-line "
                "detector, and a real Firefox never does this")
            assert page.evaluate(
                "() => !!document.getElementById('openhost').shadowRoot"), (
                "an OPEN root stopped being visible to the page: the patch "
                "moved something it should not have touched")
            assert page.evaluate(
                "() => document.getElementById('host').outerHTML"
            ) == '<div id="host"></div>', (
                "the page's own serialisation changed")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_content_SERIALISES_shadow_roots_including_closed_ones(firefox_binary):
    """⛔ Side B of the research, and what forking the client unlocked.

    Upstream serialises with `documentElement.outerHTML`, which walks the light
    DOM only: every shadow root comes back as an empty host. This asserts the
    divergence is real and complete - open, closed, and a root NESTED inside a
    closed one, which is where a depth-one walk would look right and be wrong.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(SHADOW))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            html = page.content()
            assert "hidden text" in html, (
                "the CLOSED root was not serialised: %s" % html[:400])
            assert "open text" in html, "the OPEN root was not serialised"
            assert "two levels down" in html, (
                "the nested root was not serialised: the walk stops at depth "
                "one, which looks right and is not")
            assert 'shadowrootmode="closed"' in html, (
                "the closed root was serialised without saying it was closed")
            # And the page's own serialisation is untouched: the difference
            # is ours, not the document's.
            # ⛔ ASSERT ON THE SERIALISED ROOT, NOT ON ITS TEXT. The first
            # version looked for "hidden text" and failed: that string is also
            # in the inline <script> that BUILDS the root, so it is present in
            # any serialisation of this page and proves nothing either way.
            own = page.evaluate("() => document.documentElement.outerHTML")
            assert "shadowrootmode" not in own, (
                "the page can serialise its own shadow roots: %s" % own[:200])
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


# ── events ──────────────────────────────────────────────────────────────────

NOISY = b"""<!doctype html><html><head><title>events</title></head><body>
<button id=alert onclick="window.alert('are you sure')">alert</button>
<button id=boom onclick="undefinedFunctionCall()">boom</button>
<script>
  console.log('first line', 42);
  console.warn('a warning');
</script>
</body></html>"""


@pytest.mark.e2e
def test_console_messages_reach_the_page_listener(firefox_binary):
    """⛔ `console` and `pageError` are emitted on the CONTEXT, not on the
    Page, and `_browser_context.py` re-emits them on the page it finds in
    `params["page"]`. Emitting them on the Page produces no error at all: the
    handler simply never runs, and the user concludes their page prints
    nothing."""
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(NOISY))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            seen = []
            page.on("console", lambda m: seen.append((m.type, m.text)))
            page.goto(url)
            page.wait_for_timeout(500)
            assert seen, "no console message arrived at all"
            kinds = {t for t, _ in seen}
            texts = " | ".join(text for _, text in seen)
            assert "first line" in texts, texts
            assert "42" in texts, (
                "the numeric argument was dropped: %s" % texts)
            assert "warning" in kinds or "warn" in kinds, (
                "the message type was lost: %s" % kinds)
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_an_uncaught_error_reaches_the_pageerror_listener(firefox_binary):
    """A page that throws must reach `page.on("pageerror")`, with the message
    intact - that is the only way a caller learns the site broke."""
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(NOISY))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.click("#boom")
            page.wait_for_timeout(600)
            assert errors, "the uncaught error never arrived"
            assert "undefinedFunctionCall" in " ".join(errors), (
                "the message was lost: %s" % errors)
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_a_dialog_can_be_ANSWERED_and_not_answering_hangs_the_page(
        firefox_binary):
    """⛔ THE ONE OBJECT WHERE FORGETTING IS A HANG, NOT A LEAK. A dialog
    blocks the content process inside `window.alert`, so an unanswered one
    makes every later command time out with no hint about the cause.

    Playwright's client answers automatically when nobody is listening, and
    that safety net only works if the event ARRIVES - which is what this
    asserts. The second half then checks the explicit path.
    """
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(NOISY))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            seen = []

            def answer(dialog):
                seen.append((dialog.type, dialog.message))
                dialog.accept()

            page.on("dialog", answer)
            page.goto(url)
            page.click("#alert")
            page.wait_for_timeout(600)
            assert seen == [("alert", "are you sure")], seen
            # And the page is still alive: if the dialog had not been
            # answered, this would time out instead of returning.
            assert page.title() == "events"
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_an_unanswered_dialog_is_dismissed_by_the_client(firefox_binary):
    """⛔ The known-bad of the wiring above: with NO listener the client
    dismisses on its own, and the page keeps running. If the event never
    reached the client, this would hang - so a green here proves the event
    arrives even when nobody visibly consumes it."""
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(NOISY))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            page.click("#alert")
            assert page.title() == "events", (
                "the page is stuck: the dialog event never reached the client")
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


# ── context and page surfaces ───────────────────────────────────────────────

def test_a_cookie_domain_with_a_LEADING_DOT_matches_subdomains():
    """⛔ A leading dot means "and every subdomain". Comparing the two strings
    directly is the version that looks right and returns an empty list, so
    `context.cookies(urls=[...])` would answer nothing for a site-wide
    cookie."""
    from invisible_playwright._juggler.server import _domain_matches, _host_of
    assert _host_of("https://shop.example.com:8443/a/b") == "shop.example.com"
    assert _domain_matches(".example.com", "shop.example.com")
    assert _domain_matches("example.com", "example.com")
    assert not _domain_matches(".example.com", "notexample.com"), (
        "a suffix match without the dot boundary: badexample.com would pass")
    assert not _domain_matches("", "example.com")


def test_clearing_cookies_with_a_FILTER_is_refused_not_widened():
    """⛔ The engine command clears the WHOLE context and takes no filter.
    Honouring a filtered request by clearing everything is worse than
    refusing: the caller asked to remove one cookie and would lose the
    session."""
    from invisible_playwright._juggler.dispatcher import ProtocolException
    from invisible_playwright._juggler.server import BrowserContextDispatcher
    method = BrowserContextDispatcher.op_clear_cookies
    with pytest.raises(ProtocolException) as failure:
        method(object(), {"name": "session"})
    assert "whole context" in str(failure.value)


@pytest.mark.e2e
def test_cookies_round_trip_through_the_public_API(firefox_binary):
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            context = page.context
            context.add_cookies([{"name": "a", "value": "1",
                                  "domain": "127.0.0.1", "path": "/"}])
            names = {c["name"]: c["value"] for c in context.cookies()}
            assert names.get("a") == "1", names
            context.clear_cookies()
            assert not [c for c in context.cookies() if c["name"] == "a"]
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_a_screenshot_comes_back_as_real_png_bytes(firefox_binary):
    """⛔ THE MAGIC NUMBER, not the length. A screenshot that is the string
    "None" or a base64 blob nobody decoded is still bytes and still non-empty:
    the only assertion that separates a real image from a plausible one is the
    PNG signature."""
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            shot = page.screenshot()
            assert shot[:8] == b"\x89PNG\r\n\x1a\n", (
                "not a PNG: first bytes are %r" % shot[:12])
            assert len(shot) > 1000, len(shot)
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


@pytest.mark.e2e
def test_the_viewport_can_be_resized(firefox_binary):
    os.environ[factory.CHOICE_ENV] = factory.JUGGLER
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(PAGE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            page.set_viewport_size({"width": 640, "height": 480})
            assert page.evaluate("() => window.innerWidth") == 640
            assert page.evaluate("() => window.innerHeight") == 480
    finally:
        os.environ.pop(factory.CHOICE_ENV, None)
        srv.shutdown()


# ── the refusal layer ───────────────────────────────────────────────────────

def test_an_out_of_perimeter_call_NAMES_THE_FEATURE():
    """⛔ THE WHOLE POINT OF THE REFUSAL LAYER, and the known-bad is what it
    used to say. Before `perimeter.py` an out-of-perimeter call landed on a
    guid that was never created and came back as
    `no object 'artifact@3' to answer 'read'` - technically true, unreadable,
    and indistinguishable from a bug in this package.

    The mutation: empty `perimeter.OUTSIDE`, and this test goes red.
    """
    from invisible_playwright._juggler.dispatcher import ProtocolException
    from invisible_playwright._juggler.server import JugglerServer
    server = JugglerServer()
    with pytest.raises(ProtocolException) as failure:
        server.handle({"id": 1, "guid": "artifact@3", "method": "read",
                       "params": {}})
    message = str(failure.value)
    assert "artifact stream" in message, (
        "the refusal does not name the feature: %s" % message)
    assert "does not implement" in message
    assert "section 5.4" in message, "no pointer to where the decision lives"


def test_a_GAP_and_a_DECISION_do_not_read_the_same():
    """⛔ Two genuinely different failures, and the message has to say which.
    An in-perimeter method that is missing is OUR bug; an out-of-perimeter one
    is a decision. Collapsing them sends the reader to the wrong place."""
    from invisible_playwright._juggler.dispatcher import (Dispatcher,
                                                          ProtocolException,
                                                          Server)

    class Thing(Dispatcher):
        TYPE = "Thing"
        METHODS = {}

    server = Server()
    server.attach(type("R", (), {"emit_message": lambda self, m: None})())
    thing = Thing(server, None, {})

    with pytest.raises(ProtocolException) as decision:
        thing.call("harExport", {})
    assert "HAR" in str(decision.value)
    assert "by decision" in str(decision.value)

    with pytest.raises(ProtocolException) as gap:
        thing.call("click", {})
    assert "INSIDE the perimeter" in str(gap.value), str(gap.value)
    assert "gap, not a decision" in str(gap.value)


def test_the_perimeter_and_the_courtesy_list_do_not_OVERLAP_by_accident():
    """⛔ Every courtesy name must ALSO be out of perimeter - it is an
    exception to the refusal, not a category of its own. A courtesy entry for
    an in-perimeter operation would silently make it a no-op, which is exactly
    the "no-op instead of a refusal" that section 5.4 forbids."""
    from invisible_playwright._juggler import perimeter
    stray = sorted(set(perimeter.COURTESY) - set(perimeter.OUTSIDE))
    assert not stray, (
        "these answer as a courtesy but are INSIDE the perimeter, so they are "
        "silently no-ops: %s" % stray)


def test_every_courtesy_entry_says_WHO_calls_it():
    """⛔ An entry here is a piece of perimeter coming back in through the
    window. The list stays short and each line names its caller, or it grows
    into a second perimeter nobody reviews."""
    from invisible_playwright._juggler import perimeter
    for name, reason in perimeter.COURTESY.items():
        assert "client" in reason, (
            "%s does not say who calls it: %r" % (name, reason))


def test_the_workbench_inventory_and_the_package_cannot_DRIFT():
    """⛔ There is one perimeter and it lives in the shipped package. The
    workbench tool that counts remaining work reads it from there; if it kept
    its own copy the two would disagree the first time one was edited - and the
    one that matters, the one that REFUSES, would be the one nobody updated."""
    from invisible_playwright._juggler import perimeter
    tool = pathlib.Path(__file__).resolve().parents[3] / "scripts" \
        / "inventario_voce6.py"
    if not tool.exists():
        pytest.skip("the workbench is not next to this checkout")
    source = tool.read_text(encoding="utf-8", errors="replace")
    assert "_fuori_dal_pacchetto" in source, (
        "the inventory no longer reads the package: it has grown its own copy "
        "of the perimeter")
    assert "perimeter.py" in source
    assert len(perimeter.OUTSIDE) > 50


# ── the temporary profile ───────────────────────────────────────────────────

def test_a_profile_WE_made_is_removed_and_the_caller_s_is_not():
    """⛔ THE KNOWN-BAD IS FIVE GIGABYTES. Measured on 2026-08-28 after one day
    of development: 136 leftover `invisible_profile_*` directories, 5,0 GB.
    Nothing failed and nothing warned - a Firefox profile is a few dozen
    megabytes and the disk simply goes. The project has the same defect
    recorded for Playwright's own throwaway profiles, 7.308 directories over
    seven months; this reproduced it in hours.

    The distinction is the whole test: a directory we invented is ours to
    remove, a `userDataDir` the caller named is theirs and must survive.
    """
    import shutil
    import tempfile
    from invisible_playwright._juggler.server import (BrowserTypeDispatcher,
                                                      JugglerServer)

    server = JugglerServer()
    server.attach(type("R", (), {"emit_message": lambda self, m: None})())
    launched: list = []

    class FakeConnection(EventListeners):
        """⛔ It has to look enough like the real one for BrowserDispatcher to
        build: that constructor SUBSCRIBES and sends `Browser.enable`. The
        first version of this fake had neither and failed with an
        AttributeError that read like a defect in the server."""

        def __init__(self):
            EventListeners.__init__(self)

        def send(self, method, params=None, session=None, timeout=30):
            return {"browserContextId": "ctx-1", "targetId": "t-1"}

        def close(self):
            pass

    def fake_launch(executable, profile_dir, **kwargs):
        launched.append(profile_dir)
        pathlib.Path(profile_dir, "places.sqlite").write_bytes(b"x" * 32)
        return FakeConnection()

    from invisible_playwright._juggler import server as module
    real = module.juggler.launch
    module.juggler.launch = fake_launch
    try:
        kind = BrowserTypeDispatcher(server)

        # ours: no userDataDir, so the server invents one
        kind.op_launch({"executablePath": "x", "firefoxUserPrefs": {"a": True}})
        ours = pathlib.Path(launched[-1])
        assert ours.exists() and (ours / "user.js").exists()

        # theirs: named by the caller, and it must survive
        theirs = pathlib.Path(tempfile.mkdtemp(prefix="caller_owns_"))
        kind.op_launch({"executablePath": "x", "userDataDir": str(theirs),
                        "firefoxUserPrefs": {"a": True}})

        server.shutdown()

        assert not ours.exists(), (
            "the profile the server invented was left behind: this is the 5 GB")
        assert theirs.exists(), (
            "the CALLER's profile was deleted - a persistent profile is the "
            "one thing that must survive the session")
        shutil.rmtree(theirs, ignore_errors=True)
    finally:
        module.juggler.launch = real


def test_a_named_launch_environment_is_the_whole_environment(monkeypatch):
    """⛔ A variable the caller REMOVED must not come back from this process.

    `_session.build_env` composes the browser's full environment and drops
    what the session's hidden surface names with `None` (an Xvfb session
    drops the Wayland variables). Until 0.25.1 the server ADDED the named
    variables onto `dict(os.environ)`, so the removal never reached the
    browser: measured on 0.25.0 from the index, the hidden Firefox was born
    with `WAYLAND_DISPLAY=wayland-0` in `/proc/<pid>/environ`. The known-bad
    input is that merge. The other half stays: a launch that names nothing
    inherits, because a browser with no `PATH` and no `SYSTEMROOT` starts and
    then cannot reach the network."""
    from invisible_playwright._juggler.server import _launch_environment

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("SYSTEMROOT_OR_PATH_LIKE", "inherited")

    named = _launch_environment([{"name": "DISPLAY", "value": ":123"},
                                 {"name": "GDK_BACKEND", "value": "x11"}])
    assert named == {"DISPLAY": ":123", "GDK_BACKEND": "x11"}
    assert "WAYLAND_DISPLAY" not in named, "the removed variable came back"

    for nothing in (None, []):
        inherited = _launch_environment(nothing)
        assert inherited["SYSTEMROOT_OR_PATH_LIKE"] == "inherited"
        assert inherited["WAYLAND_DISPLAY"] == "wayland-0"
        assert inherited is not os.environ


def test_the_launch_handler_hands_the_named_environment_to_the_engine(monkeypatch):
    """And the handler uses it: what `juggler.launch` receives is exactly what
    the caller named, not a patch on top of the server's process."""
    import shutil
    from invisible_playwright._juggler.server import (BrowserTypeDispatcher,
                                                      JugglerServer)
    from invisible_playwright._juggler import server as module

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    server = JugglerServer()
    server.attach(type("R", (), {"emit_message": lambda self, m: None})())
    seen: list = []

    class FakeConnection(EventListeners):
        def __init__(self):
            EventListeners.__init__(self)

        def send(self, method, params=None, session=None, timeout=30):
            return {"browserContextId": "ctx-1", "targetId": "t-1"}

        def close(self):
            pass

    def fake_launch(executable, profile_dir, **kwargs):
        seen.append(kwargs["env"])
        return FakeConnection()

    monkeypatch.setattr(module.juggler, "launch", fake_launch)
    kind = BrowserTypeDispatcher(server)
    kind.op_launch({"executablePath": "x", "firefoxUserPrefs": {},
                    "env": [{"name": "DISPLAY", "value": ":123"}]})
    try:
        assert seen[-1] == {"DISPLAY": ":123"}
    finally:
        server.shutdown()


def test_removing_a_profile_NEVER_raises():
    """⛔ It runs while the session is already going away, and on Windows a
    file can still be held for a moment after the process that owned it exits.
    A profile left behind costs megabytes; an exception here would be a
    shutdown that fails for a reason nobody cares about."""
    from invisible_playwright._juggler.server import _remove_profile
    _remove_profile("C:/this/path/does/not/exist/at/all")
    _remove_profile("")
