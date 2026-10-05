"""`add_script_tag` and `add_style_tag`, on the page and on a child frame.

The client sends both on the FRAME channel - `page.add_script_tag` is
`page.main_frame.add_script_tag` - and the server once registered them on the
Page alone, so every call answered `Frame has no method 'addScriptTag'`
(bbdtx-accounting run 37309985845, 2026-10-05). These go through the real
client API so the channel the call travels on is the one under test.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.server import FrameDispatcher, PageDispatcher

PAGE = """<!doctype html>
<p id="para">text</p>
<iframe id="child" srcdoc="<p id='inner'>inner</p>"></iframe>"""


@pytest.mark.unit
def test_both_tags_are_served_on_the_frame_channel():
    for method in ("addScriptTag", "addStyleTag"):
        assert method in FrameDispatcher.METHODS
        assert method in PageDispatcher.METHODS


@pytest.fixture
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_context().new_page()
        page.set_default_timeout(5000)
        page.set_content(PAGE)
        page.wait_for_function(
            "document.getElementById('child').contentDocument"
            "?.getElementById('inner') != null")
        yield page


def _child(page):
    return next(f for f in page.frames if f is not page.main_frame)


@pytest.mark.e2e
def test_page_script_tag_content_runs_in_the_main_world(page):
    handle = page.add_script_tag(content="window.fromTag = 41 + 1;")
    assert handle.evaluate("el => el.tagName") == "SCRIPT"
    assert page.evaluate("window.fromTag") == 42


@pytest.mark.e2e
def test_page_script_tag_url_has_run_when_it_returns(page):
    page.add_script_tag(url="data:text/javascript,window.fromUrl%20%3D%20'loaded'%3B")
    assert page.evaluate("window.fromUrl") == "loaded"


@pytest.mark.e2e
def test_script_tag_type_is_the_callers(page):
    handle = page.add_script_tag(
        content="export const x = 1; window.fromModule = import.meta.url;",
        type="module")
    assert handle.evaluate("el => el.type") == "module"
    page.wait_for_function("window.fromModule !== undefined")


@pytest.mark.e2e
def test_page_script_tag_url_that_fails_to_load_raises(page):
    with pytest.raises(Exception, match="failed to load"):
        page.add_script_tag(url="http://127.0.0.1:9/missing.js")


@pytest.mark.e2e
def test_child_frame_script_tag_runs_in_that_frame_only(page):
    frame = _child(page)
    frame.add_script_tag(content="window.inChild = true;")
    assert frame.evaluate("window.inChild") is True
    assert page.evaluate("window.inChild === undefined") is True


@pytest.mark.e2e
def test_style_tag_applies_on_page_and_child_frame(page):
    page.add_style_tag(content="#para { color: rgb(1, 2, 3); }")
    assert page.eval_on_selector(
        "#para", "el => getComputedStyle(el).color") == "rgb(1, 2, 3)"
    frame = _child(page)
    frame.add_style_tag(url="data:text/css,%23inner%20%7B%20color%3A%20rgb(4%2C%205%2C%206)%3B%20%7D")
    assert frame.eval_on_selector(
        "#inner", "el => getComputedStyle(el).color") == "rgb(4, 5, 6)"
