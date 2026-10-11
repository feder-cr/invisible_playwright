"""`expect()` passes when the page agrees and fails when it does not.

The client sends `expect` once and reads only how the call ended: a reply is a
pass, an error carrying `errorDetails` is a failed assertion. The server used
to answer `{"matches": ...}` from a single look, so every `to_be_*` on an
element that existed passed whatever its state, and every reply with
`received` beside `matches` tripped the client's one-key assert - which made
`to_have_count` fail on the right count and `to_be_hidden` fail on a missing
element. Nothing polled either, so an element that arrived late failed.
"""
from __future__ import annotations

import pytest

from invisible_playwright._pw.sync_api import Error, expect

PAGE = ("<title>expect</title><button>Go</button>"
        "<input type=checkbox><p>a</p><p>b</p>")


@pytest.fixture(scope="module")
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4245, binary_path=firefox_binary,
                             headless=True) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        yield page


HOLDS = {
    "count": lambda p, t: expect(p.locator("button")).to_have_count(1, timeout=t),
    "count of two": lambda p, t: expect(p.locator("p")).to_have_count(2, timeout=t),
    "visible": lambda p, t: expect(p.locator("button")).to_be_visible(timeout=t),
    "hidden when missing": lambda p, t: expect(p.locator("#missing")).to_be_hidden(timeout=t),
    "not checked": lambda p, t: expect(p.locator("input")).not_to_be_checked(timeout=t),
    "enabled": lambda p, t: expect(p.locator("button")).to_be_enabled(timeout=t),
    "text": lambda p, t: expect(p.locator("button")).to_have_text("Go", timeout=t),
    "title": lambda p, t: expect(p).to_have_title("expect", timeout=t),
}

FAILS = {
    "count": lambda p, t: expect(p.locator("button")).to_have_count(5, timeout=t),
    "hidden": lambda p, t: expect(p.locator("button")).to_be_hidden(timeout=t),
    "checked": lambda p, t: expect(p.locator("input")).to_be_checked(timeout=t),
    "disabled": lambda p, t: expect(p.locator("button")).to_be_disabled(timeout=t),
    "not visible": lambda p, t: expect(p.locator("button")).not_to_be_visible(timeout=t),
    "visible when missing": lambda p, t: expect(p.locator("#missing")).to_be_visible(timeout=t),
    "text": lambda p, t: expect(p.locator("button")).to_have_text("Stop", timeout=t),
}


@pytest.mark.e2e
@pytest.mark.parametrize("name", sorted(HOLDS))
def test_an_assertion_the_page_agrees_with_passes(page, name):
    HOLDS[name](page, 1000)


@pytest.mark.e2e
@pytest.mark.parametrize("name", sorted(FAILS))
def test_an_assertion_the_page_contradicts_fails(page, name):
    with pytest.raises(AssertionError) as failure:
        FAILS[name](page, 500)
    # The client's own message, built from the details the server sent, and
    # not a bare AssertionError from its protocol plumbing.
    assert "Actual value" in str(failure.value), str(failure.value)


@pytest.mark.e2e
def test_an_assertion_waits_for_an_element_that_arrives_late(page):
    page.evaluate("setTimeout(() => {"
                  "  const d = document.createElement('div');"
                  "  d.id = 'late'; d.textContent = 'here';"
                  "  document.body.append(d); }, 500)")
    expect(page.locator("#late")).to_be_visible(timeout=5000)


@pytest.mark.e2e
def test_more_than_one_match_is_a_strict_mode_violation(page):
    with pytest.raises(Error, match="strict mode violation"):
        expect(page.locator("p")).to_be_visible(timeout=500)
