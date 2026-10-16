"""Handles of values the CALLER'S code produced in its world.

Two defects of 0.36.0, one family. [B323]: `wait_for_function("() =>
document.querySelector('#x')")` - the most common wait there is - never ended
although the element existed, because the predicate ran by value and Python
judged the serialized result, where a node is nothing truthy. The judgement is
now invisible-core's `js_truthy`, from one run of the predicate. [B324]: every
handle `evaluate_handle` returned was unusable - `text_content`, `evaluate` and
`json_value` all failed with "Cannot find object" - because an objectId of the
caller's code was made into a utility-world handle. A node is now adopted into
the utility world, and any other value's handle runs its operations in the
caller's world (`FrameDispatcher._handle_from_caller`), which since firefox-49
is a world of its own and no longer the page's realm ([B322]).
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


@pytest.mark.e2e
def test_an_evaluate_handle_node_reads_and_evaluates(page):
    element = page.evaluate_handle("() => document.querySelector('#x')").as_element()
    assert element is not None
    assert element.text_content() == "here"
    assert element.evaluate("(e, suffix) => e.id + suffix", "!") == "x!"


@pytest.mark.e2e
def test_an_evaluate_handle_object_lives_in_the_page_world(page):
    page.evaluate("window.__state = {a: 1, b: [2, 3]}")
    handle = page.evaluate_handle("() => window.__state")
    assert handle.json_value() == {"a": 1, "b": [2, 3]}
    assert handle.get_property("a").json_value() == 1
    assert handle.evaluate("o => o.b.length") == 2
    assert sorted(handle.get_properties()) == ["a", "b"]
    handle.dispose()


@pytest.mark.e2e
@pytest.mark.parametrize("expression, value", [("() => 7", 7), ("() => 'x'", "x"),
                                               ("() => true", True)])
def test_an_evaluate_handle_primitive_has_its_value(page, expression, value):
    assert page.evaluate_handle(expression).json_value() == value


@pytest.mark.e2e
def test_an_evaluate_handle_nan_is_nan(page):
    import math
    assert math.isnan(page.evaluate_handle("() => NaN").json_value())


@pytest.mark.e2e
def test_a_wait_for_function_object_handle_reads_in_the_page_world(page):
    page.evaluate("setTimeout(() => { window.__ready = {n: 3}; }, 200)")
    handle = page.wait_for_function("() => window.__ready")
    assert handle.json_value() == {"n": 3}
