"""`wait_for_function` ends when the predicate is truthy AS JAVASCRIPT JUDGES IT.

Until 0.36.0 the predicate ran by value and Python judged the serialized
result, so `wait_for_function("() => document.querySelector('#x')")` - the
most common wait there is - never ended although the element existed: a node
serializes to nothing truthy ([B323], found by the workbench's crossings gate
on firefox-48, 96 turns and none truthy). The judgement is now invisible-core's
`js_truthy` over the engine's RemoteObject, from one run of the predicate.
"""
from __future__ import annotations

import pytest

PAGE = """<!doctype html><div id="x">here</div>"""


@pytest.fixture
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_context().new_page()
        page.set_default_timeout(5000)
        page.set_content(PAGE)
        yield page


@pytest.mark.e2e
def test_a_predicate_that_returns_a_node_ends_the_wait_with_that_node(page):
    handle = page.wait_for_function("() => document.querySelector('#x')")
    element = handle.as_element()
    assert element is not None
    assert element.text_content() == "here"


@pytest.mark.e2e
def test_a_node_that_appears_later_ends_the_wait(page):
    page.evaluate("setTimeout(() => { const d = document.createElement('div');"
                  " d.id = 'late'; d.textContent = 'late'; document.body.append(d); }, 300)")
    handle = page.wait_for_function("() => document.querySelector('#late')")
    assert handle.as_element().text_content() == "late"


@pytest.mark.e2e
@pytest.mark.parametrize("predicate", ["() => NaN", "() => 0", "() => ''", "() => null"])
def test_a_falsy_value_never_ends_the_wait(page, predicate):
    # NaN is the case the serialized judgement could get wrong both ways
    from invisible_playwright.sync_api import TimeoutError as PlaywrightTimeout
    with pytest.raises(PlaywrightTimeout):
        page.wait_for_function(predicate, timeout=500)
