"""The caller's code runs in a world of its own, where a page that replaced a
standard API cannot count its calls [B322].

Until firefox-49 `page.evaluate` ran in the page's own realm, so a call to a
standard API went through whatever the page had put in its place. A Cloudflare
challenge wraps `Document.prototype.querySelector` in a Proxy, and one lookup
of its token field from an evaluate got the clearance refused on the reload,
11 pairs out of 11. The caller's code now runs in the caller's world
(invisible-core's `evaluate_in_caller`): a name the standard defines is the
native one, every other name is the page's. These tests hold both halves on
a page that counts every call to the APIs it wrapped, and the one change the
rule declares.
"""
from __future__ import annotations

import pytest

PAGE = """<!doctype html><html><head><script>
window.__calls = 0;
function wrap(owner, name) {
  const real = owner[name];
  Object.defineProperty(owner, name, {configurable: true, writable: true,
    value: new Proxy(real, {apply(t, self, args) {
      window.__calls++; return Reflect.apply(t, self, args); }})});
}
wrap(Document.prototype, 'querySelector');
wrap(Document.prototype, 'querySelectorAll');
wrap(Element.prototype, 'querySelector');
wrap(Document.prototype, 'getElementById');
wrap(Array.prototype, 'map');
wrap(JSON, 'stringify');
window.__APP = {ready: true};
function pageFn(x) { return x * 2; }
class Point { constructor(x) { this.x = x; } toString() { return 'P' + this.x; } }
window.pt = new Point(3);
const pageConst = 'c';
window.pageRead = (n) => window[n];
</script></head><body>
<form><input name="cf-turnstile-response" value="tok"></form><div id="x">here</div>
<script>document.getElementById('x').__vue__ = {msg: 'hi'}; window.__calls = 0;</script>
</body></html>"""


@pytest.fixture
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_context().new_page()
        page.set_default_timeout(5000)
        page.set_content(PAGE)
        yield page


def _calls(page):
    # a data property of the page: reading it runs none of the page's code
    return page.evaluate("() => window.__calls")


@pytest.mark.e2e
def test_the_token_field_is_found_without_the_page_counting(page):
    assert page.evaluate(
        "() => document.querySelector('input[name=\"cf-turnstile-response\"]').value") == "tok"
    assert page.evaluate(
        "() => [...document.querySelectorAll('input')].map(i => i.name)") == [
            "cf-turnstile-response"]
    assert page.evaluate("() => JSON.stringify(document.getElementById('x').id)") == '"x"'
    assert _calls(page) == 0


@pytest.mark.e2e
def test_the_waits_and_the_handles_are_not_counted_either(page):
    page.wait_for_function("() => document.querySelector('#x')")
    handle = page.evaluate_handle("() => document.body.querySelector('#x')")
    assert handle.as_element().text_content() == "here"
    assert page.eval_on_selector("#x", "el => el.ownerDocument.querySelector('#x').id") == "x"
    assert _calls(page) == 0


@pytest.mark.e2e
def test_what_the_page_added_is_the_pages(page):
    assert page.evaluate("() => window.__APP.ready") is True
    assert page.evaluate("() => pageFn(21)") == 42
    assert page.evaluate("() => document.getElementById('x').__vue__.msg") == "hi"
    # a top-level class or const of a page script is a binding of the page's
    # global scope, not a property of its window: a bare name finds it, as in
    # the page, and `window.pageConst` does not
    assert page.evaluate("() => pt instanceof Point") is True
    assert page.evaluate("() => pageConst") == "c"
    assert page.evaluate("() => window.pageConst === undefined") is True
    page.evaluate("() => { window.written = 5; }")
    assert page.evaluate("() => pageRead('written')") == 5


@pytest.mark.e2e
def test_the_callers_code_on_an_element_sees_what_the_page_put_on_it(page):
    """`eval_on_selector`, `locator.evaluate`, `handle.evaluate` and
    `eval_on_selector_all` are the caller's code too: until firefox-49 they ran
    behind the utility world's plain Xray, where `el.__vue__` is undefined."""
    assert page.eval_on_selector("#x", "el => el.__vue__.msg") == "hi"
    assert page.locator("#x").evaluate("el => el.__vue__.msg") == "hi"
    assert page.query_selector("#x").evaluate("(el, k) => el.__vue__[k]", "msg") == "hi"
    assert page.eval_on_selector_all("div", "els => els.map(e => !!e.__vue__)") == [True]
    assert _calls(page) == 0


@pytest.mark.e2e
def test_the_rule_goes_level_by_level_of_the_pages_chain(page):
    """A standard name is the native one on the browser's own objects, even
    where the page replaced it on the object itself; on what the page created
    (a class, its instances) the page's definition wins, as it does in the
    page: `String(pt)` is the class's `toString`."""
    # the page's own code: a replacement on `document` itself
    page.add_script_tag(content="""
        const realQs = Document.prototype.querySelector;
        document.querySelector = new Proxy(realQs, {apply(t, self, args) {
            window.__calls++; return Reflect.apply(t, self, args); }});""")
    assert page.evaluate("() => document.querySelector('#x').id") == "x"
    assert _calls(page) == 0
    assert page.evaluate("() => String(pt)") == "P3"


@pytest.mark.e2e
def test_what_the_caller_hands_the_page_has_the_pages_own_prototypes(page):
    """An object the caller's code hands the page keeps its identity and its
    own properties, but its standard prototypes are the page's own: through a
    plain transparent wrapper the page saw an object of another realm, and a
    callback's constructor compiled code in the caller's world."""
    page.add_script_tag(content="""
        window.probe = (a, f) => [a instanceof Array, a.map === Array.prototype.map,
                                  f.constructor === Function,
                                  f.constructor('return globalThis')() === window];""")
    assert page.evaluate("() => probe([1, 2], () => 1)") == [True, True, True, True]
