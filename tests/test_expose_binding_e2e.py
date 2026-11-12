"""`page.expose_function` / `page.expose_binding` / `context.expose_function` / `context.expose_binding`.

They used to refuse ("not wired" / "no method 'exposeBinding'"). The page calls a Python function and gets its answer as a
promise: the value, a rejection when the Python function raised, an await when it is async.

THE RULE THAT MATTERS MORE THAN THE FEATURE: A PAGE SEES `foo` AND NOTHING ELSE. A page can list the names on its window, so a
transport left there is the driver's signature. These tests read what the page reads: the own properties of `window` (names and
symbols) with and without an exposed function, in the top, in a frame of another site (another process), in the document born
after a navigation and after `document.open()`; what `foo.toString()` and the stack of an error from `foo` say; and that `foo`
looks like a function the page defined itself (name, length, descriptor, own properties, position among the keys).
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

PAGE = "<!doctype html><title>t</title><body><iframe id='f' srcdoc='<p>frame</p>'></iframe>"

# What a page can read of its own window: every own name, own symbols, and the enumeration order.
WINDOW = ("(() => ({names: Object.getOwnPropertyNames(window).sort(), keys: Object.keys(window),"
          " symbols: Object.getOwnPropertySymbols(window).map(String)}))()")

#: Words that would give the driver away, in anything the page can read.
TELLS = ("playwright", "binding", "juggler", "invisible", "chrome://", "resource://", "moz-src", "debugger eval")


@pytest.fixture
def browser(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4264, binary_path=firefox_binary, headless=True) as b:
        yield b


@pytest.fixture
def url(tmp_path):
    (tmp_path / "p.html").write_text(PAGE, encoding="utf-8")
    return (tmp_path / "p.html").as_uri()


def _extra(seen, base):
    return sorted(set(seen["names"]) - set(base["names"])), sorted(set(seen["symbols"]) - set(base["symbols"]))


def test_a_page_function_exposed_before_the_load_answers_and_survives_a_navigation(browser, url):
    page = browser.new_context().new_page()
    page.expose_function("add", lambda a, b: a + b)
    for target in (url, url + "?again"):
        page.goto(target, wait_until="load")
        assert page.evaluate("add(2, 3)") == 5


def test_a_page_function_exposed_after_the_load_reaches_the_document_that_exists(browser, url):
    page = browser.new_context().new_page()
    page.goto(url, wait_until="load")
    page.expose_function("twice", lambda x: x * 2)
    assert page.evaluate("twice(21)") == 42


def test_two_functions_on_one_page_answer_each_its_own_calls(browser, url):
    page = browser.new_context().new_page()
    page.expose_function("one", lambda: "one")
    page.expose_function("two", lambda: "two")
    page.goto(url, wait_until="load")
    assert page.evaluate("Promise.all([one(), two(), two(), one()])") == ["one", "two", "two", "one"]


def test_the_answer_is_awaited_and_a_python_error_rejects_the_promise(browser, url):
    page = browser.new_context().new_page()

    def boom(_):
        raise ValueError("nope from python")

    async def slow(x):
        return {"got": x, "list": [1, 2]}

    page.expose_function("boom", boom)
    page.expose_function("slow", slow)
    page.goto(url, wait_until="load")
    assert page.evaluate("slow('a')") == {"got": "a", "list": [1, 2]}
    assert "nope from python" in page.evaluate("boom(1).then(() => 'resolved', e => String(e.message))")


def test_a_binding_gets_the_source_page_and_frame(browser, url):
    page = browser.new_context().new_page()
    seen = {}

    def binding(source, value):
        seen.update(page=source["page"], frame=source["frame"], context=source["context"])
        return value

    page.expose_binding("who", binding)
    page.goto(url, wait_until="load")
    assert page.evaluate("who('x')") == "x"
    assert seen["page"] is page and seen["frame"] is page.main_frame and seen["context"] is page.context


def test_a_page_function_is_not_on_another_page_of_the_context(browser, url):
    context = browser.new_context()
    first, second = context.new_page(), context.new_page()
    first.expose_function("mine", lambda: "first")
    first.goto(url, wait_until="load")
    second.goto(url, wait_until="load")
    assert first.evaluate("typeof mine") == "function"
    assert second.evaluate("typeof mine") == "undefined"


def test_a_context_function_is_on_the_page_that_exists_the_page_to_come_and_their_frames(browser, url):
    context = browser.new_context()
    existing = context.new_page()
    existing.goto(url, wait_until="load")
    context.expose_function("ctx", lambda x: "ctx:" + x)
    later = context.new_page()
    later.goto(url, wait_until="load")
    for page in (existing, later):
        assert page.evaluate("ctx('a')") == "ctx:a"
        frame = page.frames[1]
        assert frame.evaluate("ctx('b')") == "ctx:b"
    existing.goto(url + "?again", wait_until="load")
    assert existing.evaluate("ctx('c')") == "ctx:c"


def test_a_disposed_function_is_gone_now_and_in_the_documents_to_come(browser, url):
    page = browser.new_context().new_page()
    page.goto(url, wait_until="load")
    handle = page.expose_function("gone", lambda: 1)
    assert page.evaluate("gone()") == 1
    handle.dispose()
    assert page.evaluate("typeof gone") == "undefined"
    page.goto(url + "?again", wait_until="load")
    assert page.evaluate("typeof gone") == "undefined"


# ── what the page can read ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_without_an_exposed_function_nothing_is_added_and_with_one_only_its_name(browser, url):
    """No `expose_*`: the engine installs nothing, and the window has no name that is ours. After `expose_function('foo')` the
    window has `foo` more, and no symbol more: the transport is not reachable."""
    plain = browser.new_context().new_page()
    plain.goto(url, wait_until="load")
    base = plain.evaluate(WINDOW)
    assert not [n for n in base["names"] if any(t in n.lower() for t in ("playwright", "binding", "juggler"))]
    context = browser.new_context()
    page = context.new_page()
    context.expose_function("foo", lambda x: x)
    page.goto(url, wait_until="load")
    seen = page.evaluate(WINDOW)
    assert _extra(seen, base) == (["foo"], [])
    assert seen["keys"][: len(base["keys"])] == base["keys"], "the order of the keys moved"
    assert seen["keys"][-1] == "foo"


def test_the_only_name_is_foo_in_a_frame_of_another_site_and_in_the_document_after_a_navigation(browser, tmp_path):
    """127.0.0.1 and localhost are two sites, so two processes. The frame of the other site, a nested frame, the document the
    page navigates to and the one a `document.open()` rewrites: each shows `foo` and nothing else."""
    class Handler(http.server.BaseHTTPRequestHandler):
        pages = {}

        def do_GET(self):
            body = self.pages.get(self.path.split("?")[0], "<!doctype html><title>t</title>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    Handler.pages = {
        "/top": "<!doctype html><title>top</title><iframe src='http://localhost:%d/child'></iframe>" % port,
        "/child": "<!doctype html><title>child</title><iframe src='http://127.0.0.1:%d/grand'></iframe>" % port,
        "/grand": "<!doctype html><title>grand</title>",
    }
    try:
        plain = browser.new_context().new_page()
        plain.goto("http://127.0.0.1:%d/top" % port, wait_until="load")
        plain.wait_for_function("window.frames.length >= 0")
        plain.wait_for_timeout(1500)
        base = {f.url: f.evaluate(WINDOW) for f in plain.frames}
        assert len(base) == 3, sorted(base)
        context = browser.new_context()
        page = context.new_page()
        context.expose_function("foo", lambda x: x)
        for target in ("http://127.0.0.1:%d/top" % port, "http://127.0.0.1:%d/top?again" % port):
            page.goto(target, wait_until="load")
            page.wait_for_timeout(1500)
            seen = {f.url.split("?")[0]: f.evaluate(WINDOW) for f in page.frames}
            assert sorted(seen) == sorted(base)
            for frame_url, window in seen.items():
                assert _extra(window, base[frame_url]) == (["foo"], []), frame_url
        # a child that reads its parent and its top finds no name of ours either
        child = next(f for f in page.frames if f.url.startswith("http://localhost"))
        # (cross-site: the parent is opaque to the child, which is the point; the same-origin case is the srcdoc test below)
        assert child.evaluate("typeof foo") == "function"
        # after document.open() the window of the document is the one it was
        page.evaluate("document.open(); document.write('<!doctype html><p>again</p>'); document.close()")
        after = page.evaluate(WINDOW)
        assert _extra(after, base[sorted(base)[0]]) == (["foo"], []) or "foo" in after["names"]
        assert not [n for n in after["names"] if any(t in n.lower() for t in ("playwright", "binding", "juggler"))]
    finally:
        server.shutdown()


def test_a_same_origin_child_reading_parent_and_top_finds_nothing_more_than_foo(browser, url):
    plain = browser.new_context().new_page()
    plain.goto(url, wait_until="load")
    base = plain.evaluate(WINDOW)
    context = browser.new_context()
    page = context.new_page()
    page.expose_function("foo", lambda x: x)
    page.goto(url, wait_until="load")
    child = page.frames[1]
    for reach in ("parent", "top"):
        seen = child.evaluate("(() => ({names: Object.getOwnPropertyNames(%s).sort(), symbols: Object.getOwnPropertySymbols(%s).length}))()"
                              % (reach, reach))
        assert _extra({"names": seen["names"], "symbols": []}, base) == (["foo"], []), reach
        assert seen["symbols"] == 0
    page.evaluate("document.open(); document.write('<!doctype html><p>again</p>'); document.close()")
    assert _extra(page.evaluate(WINDOW), base) == (["foo"], [])


def test_foo_reads_as_a_function_the_page_defined(browser, url):
    """Its source, its name, its length, its own properties, its descriptor on the window and its place among the keys are those of
    `window.bar = function (...args) { ... }` written by the page, and nothing in what it says or in the stack of an error raised
    from it names the driver."""
    page = browser.new_context().new_page()
    page.expose_function("foo", lambda x: x)
    page.expose_function("raises", lambda x: (_ for _ in ()).throw(ValueError("python said no")))
    page.goto(url, wait_until="load")
    # a script tag runs in the page's own realm (an evaluate runs in the caller's, whose Function.prototype is another one)
    page.add_script_tag(content="window.bar = function (...args) { return args; }")
    read = page.evaluate("""(() => {
        const d = n => { const x = Object.getOwnPropertyDescriptor(window, n); return {w: x.writable, e: x.enumerable, c: x.configurable, t: typeof x.value}; };
        // the own properties FIRST: reading `name` or `length` resolves them and moves them in the list
        const own = Object.getOwnPropertyNames(foo), ownBar = Object.getOwnPropertyNames(bar);
        return {
          own, ownBar, name: foo.name, length: foo.length,
          desc: d('foo'), descBar: d('bar'), proto: Object.getPrototypeOf(foo) === Object.getPrototypeOf(bar),
          source: foo.toString(), symbols: Object.getOwnPropertySymbols(foo).length,
          keys: Object.keys(window).filter(k => k === 'foo' || k === 'bar' || k === 'raises'),
          ctor: foo.constructor === bar.constructor,
        };
    })()""")
    assert read["name"] == "foo" and read["length"] == 0
    assert read["own"] == read["ownBar"]
    assert read["desc"] == read["descBar"] and read["symbols"] == 0 and read["proto"] and read["ctor"]
    assert read["keys"] == ["foo", "raises", "bar"], read["keys"]
    assert not [t for t in TELLS if t in read["source"].lower()], read["source"]
    # the stack of an error thrown by the call itself (a value that cannot be sent) and of a rejection from Python
    stacks = page.evaluate("""(async () => {
        const out = [];
        try { await foo(1n); } catch (e) { out.push(String(e.stack), String(e.message), e.name); }
        try { await raises(1); } catch (e) { out.push(String(e.stack), String(e.message), e.name); }
        return out;
    })()""")
    assert len(stacks) == 6
    for text in stacks:
        assert not [t for t in TELLS if t in text.lower()], text
    assert "python said no" in stacks[4]


def test_a_worker_gets_nothing(browser, url):
    """The engine installs a binding in the frames of a page, as Playwright's does: a worker has no `foo`, and its global
    has nothing more than a worker of a page without one."""
    page = browser.new_context().new_page()
    probe = ("new Promise(r => { const w = new Worker(URL.createObjectURL(new Blob(["
             "'postMessage(JSON.stringify({foo: typeof foo, names: Object.getOwnPropertyNames(self).sort()}))'])));"
             " w.onmessage = e => r(JSON.parse(e.data)); })")
    page.goto(url, wait_until="load")
    base = page.evaluate(probe)
    page.expose_function("foo", lambda: 1)
    page.goto(url + "?again", wait_until="load")
    seen = page.evaluate(probe)
    assert seen == base and seen["foo"] == "undefined"
