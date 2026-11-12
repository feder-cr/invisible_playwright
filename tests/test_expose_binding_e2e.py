"""`page.expose_function` / `page.expose_binding` / `context.expose_function` / `context.expose_binding`.

They used to refuse ("not wired" / "no method 'exposeBinding'"). The page calls a Python function and gets its answer as a
promise: the value, a rejection when the Python function raised, an await when it is async. The cases that go wrong with the
scope: a function exposed AFTER the page loaded (it has to reach the document that exists), one that must survive a navigation,
one exposed on the context (a page that exists, a page opened afterwards, a frame inside it), one exposed on a page that must not
appear on another page, and the one removed again.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PAGE = "<!doctype html><title>t</title><body><iframe id='f' srcdoc='<p>frame</p>'></iframe></body>"


@pytest.fixture
def browser(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4264, binary_path=firefox_binary, headless=True) as b:
        yield b


@pytest.fixture
def url(tmp_path):
    (tmp_path / "p.html").write_text(PAGE, encoding="utf-8")
    return (tmp_path / "p.html").as_uri()


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


def test_a_disposed_function_is_gone(browser, url):
    page = browser.new_context().new_page()
    page.goto(url, wait_until="load")
    handle = page.expose_function("gone", lambda: 1)
    assert page.evaluate("gone()") == 1
    handle.dispose()
    assert page.evaluate("typeof gone") == "undefined"
    page.goto(url + "?again", wait_until="load")
    assert page.evaluate("typeof gone") == "undefined"


def test_the_transport_is_not_enumerable_and_the_function_is(browser, url):
    page = browser.new_context().new_page()
    page.expose_function("visible", lambda: 1)
    page.goto(url, wait_until="load")
    keys = page.evaluate("Object.keys(window)")
    assert "visible" in keys
    assert "__playwright__binding__" not in keys and "__playwright__binding__controller__" not in keys
