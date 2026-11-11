"""The frame tree, typing, trusted clicks and screenshots, with frames in other processes.

Three sites (see `_fission_frames.py`): the top is A (`127.0.0.1`), the frame B (`localhost`), the frame C
(`127.0.0.2`). With Fission each site is a content process of its own, and the page the script drives is made of
documents that live in different ones. What a script written against the Playwright API does with such a page is
what it does with any other:

* the frame tree A > B > A > C (the second A is the site of the top again, below a frame of another site): every
  level is in `page.frames` with its url and name, and `parent_frame` / `child_frames` give the same chain;
* typing into an input of a frame of another site and of a frame nested in it (`press_sequentially`,
  `keyboard.type` after a focus, `press`): the keys are trusted and reach that input; `wait_for_selector` and
  `Locator.wait_for` see an element that appears there later;
* a click by selector on an element inside such a frame lands on THAT element when the iframe is under a CSS
  transform (scale and translate) and after the top page and the frame have both been scrolled (the page
  records which element got the trusted click);
* `page.screenshot()` of a page whose frame of another site paints a known colour shows that colour inside the
  frame's box, and `locator.screenshot()` of an element inside the frame, and inside a nested one, has the
  element's size and colour.

Every test with a frame of B is run for the frame nested in it (C) as well where the scenario has one. The first
test of the file proves the frames are in other processes, so it cannot turn into a same-process suite.
"""
from __future__ import annotations

import pytest

from _fission_frames import (Sites, assert_other_process, decode_png, launch, near, new_page, wait_for,
                             wait_frame)

pytestmark = pytest.mark.e2e


@pytest.fixture
def sites():
    with Sites() as s:
        yield s


@pytest.fixture
def browser(firefox_binary):
    with launch(firefox_binary) as browser:
        yield browser


@pytest.fixture
def page(browser):
    context, page = new_page(browser)
    yield page
    context.close()


# ---------------------------------------------------------------------------
# 1. the frame tree A > B > A > C
# ---------------------------------------------------------------------------


@pytest.fixture
def tree(sites):
    sites.route("A", "/tree", "<!doctype html><title>level 0</title><iframe id='one' name='one' src='%s' "
                "width='700' height='500'></iframe>" % sites.url("B", "/level1"))
    sites.route("B", "/level1", "<!doctype html><title>level 1</title><iframe id='two' name='two' src='%s' "
                "width='600' height='400'></iframe>" % sites.url("A", "/level2"))
    sites.route("A", "/level2", "<!doctype html><title>level 2</title><iframe id='three' name='three' src='%s' "
                "width='500' height='300'></iframe>" % sites.url("C", "/level3"))
    sites.route("C", "/level3", "<!doctype html><title>level 3</title><p id='leaf'>leaf of the tree")
    return sites


def test_the_tree_a_b_a_c_is_listed_at_every_level(page, tree):
    page.goto(tree.url("A", "/tree"), wait_until="load")
    wait_frame(page, tree.url("C"), "/level3")
    expected = [("", tree.url("A", "/tree")), ("one", tree.url("B", "/level1")),
                ("two", tree.url("A", "/level2")), ("three", tree.url("C", "/level3"))]
    assert wait_for(page, lambda: [(f.name, f.url) for f in page.frames] == expected), \
        [(f.name, f.url) for f in page.frames]
    main, one, two, three = page.frames
    assert main == page.main_frame
    assert [f.parent_frame for f in (main, one, two, three)] == [None, main, one, two]
    assert [f.child_frames for f in (main, one, two, three)] == [[one], [two], [three], []]
    assert all(f.page is page for f in page.frames)
    assert [f.is_detached() for f in page.frames] == [False] * 4
    # the lookups by name and by url find the same objects
    assert page.frame(name="three") is three and page.frame(url=tree.url("A", "/level2")) is two
    # and the document of each level answers for itself
    assert [f.evaluate("location.href") for f in page.frames] == [u for _, u in expected]
    assert [f.evaluate("document.title") for f in page.frames] == ["level 0", "level 1", "level 2", "level 3"]
    assert [f.evaluate("window.name") for f in page.frames] == ["", "one", "two", "three"]
    assert three.locator("#leaf").inner_text() == "leaf of the tree"
    # the frames are in other processes: B is not A, and C is neither B nor the second A
    assert_other_process(main, one)
    assert_other_process(two, three)


# ---------------------------------------------------------------------------
# 2. typing, and waiting for what appears later
# ---------------------------------------------------------------------------

TYPING = ("<input id='t' autocomplete='off'><script>window.keys = [];"
          "t.addEventListener('keydown', e => keys.push([e.key, e.isTrusted]));</script>")


@pytest.fixture
def typing(sites):
    sites.route("A", "/typing", "<!doctype html><title>top</title><iframe id='b' src='%s' width='600' height='400'>"
                "</iframe>" % sites.url("B", "/typing-b"))
    sites.route("B", "/typing-b", "<!doctype html><title>b</title>" + TYPING +
                "<iframe id='c' src='%s' width='400' height='150'></iframe>" % sites.url("C", "/typing-c"))
    sites.route("C", "/typing-c", "<!doctype html><title>c</title>" + TYPING)
    return sites


def _typing_frames(page, typing):
    page.goto(typing.url("A", "/typing"), wait_until="load")
    return {"frame": wait_frame(page, typing.url("B"), "/typing-b"),
            "nested": wait_frame(page, typing.url("C"), "/typing-c")}


@pytest.mark.parametrize("target", ["frame", "nested"])
@pytest.mark.parametrize("how", ["press_sequentially", "keyboard_type", "press_keys"])
def test_typing_into_an_input_of_a_frame_of_another_site(page, typing, target, how):
    frame = _typing_frames(page, typing)[target]
    field = frame.locator("#t")
    if how == "press_sequentially":
        field.press_sequentially("Hello 42")
        expected, keys = "Hello 42", list("Hello 42")
    elif how == "keyboard_type":
        field.focus()
        assert frame.evaluate("document.activeElement.id") == "t"
        page.keyboard.type("Typed text")
        expected, keys = "Typed text", list("Typed text")
    else:
        field.focus()
        for key in ("a", "b", "c", "Backspace", "d"):
            page.keyboard.press(key)
        field.press("e")
        expected, keys = "abde", ["a", "b", "c", "Backspace", "d", "e"]
    assert field.input_value() == expected
    seen = frame.evaluate("window.keys")
    # a capital letter is typed with the Shift key held, as on a keyboard: its own keydown comes first
    assert [k for k, _ in seen if k != "Shift"] == keys
    assert all(trusted for _, trusted in seen), seen


def test_the_other_frames_are_not_typed_into(page, typing):
    """The keys go to the focused input and to no other document: the input of the nested frame and the one of
    its parent each keep only what was typed in them."""
    frames = _typing_frames(page, typing)
    frames["frame"].locator("#t").press_sequentially("outer")
    frames["nested"].locator("#t").press_sequentially("inner")
    assert frames["frame"].locator("#t").input_value() == "outer"
    assert frames["nested"].locator("#t").input_value() == "inner"
    assert [k for k, _ in frames["frame"].evaluate("window.keys")] == list("outer")
    assert [k for k, _ in frames["nested"].evaluate("window.keys")] == list("inner")


LATE = ("<p id='anchor'>anchor</p><script>"
        "setTimeout(() => { const d = document.createElement('div'); d.id = 'late'; d.textContent = 'arrived';"
        "document.body.appendChild(d); }, 700);"
        "setTimeout(() => { const d = document.createElement('div'); d.id = 'shown'; d.textContent = 'shown';"
        "d.style.display = 'none'; document.body.appendChild(d); }, 300);"
        "setTimeout(() => { document.getElementById('shown').style.display = 'block'; }, 1000);</script>")


@pytest.fixture
def late(sites):
    sites.route("A", "/late", "<!doctype html><title>top</title><iframe id='b' src='%s' width='600' height='400'>"
                "</iframe>" % sites.url("B", "/late-b"))
    sites.route("B", "/late-b", "<!doctype html><title>b</title>" + LATE +
                "<iframe id='c' src='%s' width='400' height='150'></iframe>" % sites.url("C", "/late-c"))
    sites.route("C", "/late-c", "<!doctype html><title>c</title>" + LATE)
    return sites


@pytest.mark.parametrize("target", ["frame", "nested"])
def test_waiting_for_an_element_that_appears_later_in_a_frame_of_another_site(page, late, target):
    page.goto(late.url("A", "/late"), wait_until="load")
    frame = {"frame": wait_frame(page, late.url("B"), "/late-b"),
             "nested": wait_frame(page, late.url("C"), "/late-c")}[target]
    # the element is not there yet when the wait starts, or it is by now: either way the wait returns it
    handle = frame.wait_for_selector("#late", timeout=8000)
    try:
        assert handle.inner_text() == "arrived"
        assert handle.owner_frame() is frame
    finally:
        handle.dispose()
    # the same, by the locator of the frame and by the frame locator of the page
    frame.locator("#shown").wait_for(state="visible", timeout=8000)
    outer = page.frame_locator("#b")
    scoped = outer.locator("#late") if target == "frame" else outer.frame_locator("#c").locator("#late")
    scoped.wait_for(state="attached", timeout=8000)
    assert scoped.inner_text() == "arrived"


def test_waiting_in_a_frame_of_another_site_times_out_on_what_never_comes(page, late):
    """The wait is not satisfied by an element of another document: `#nothing` is in none, and the error is the
    wait's TimeoutError naming the selector, not a protocol error about the frame."""
    page.goto(late.url("A", "/late"), wait_until="load")
    frame = wait_frame(page, late.url("B"), "/late-b")
    with pytest.raises(Exception) as error:
        frame.wait_for_selector("#nothing", timeout=1500)
    assert type(error.value).__name__ == "TimeoutError" and "#nothing" in str(error.value), error.value


# ---------------------------------------------------------------------------
# 3. a click by selector under a transform and after scrolling
# ---------------------------------------------------------------------------

GRID = ("<style>html,body{margin:0}button{position:absolute;width:60px;height:40px;margin:0;padding:0}"
        "%s</style>%s<script>window.landed = [];"
        "addEventListener('click', e => landed.push({id: e.target.id || e.target.tagName, "
        "trusted: e.isTrusted}), true);</script>")


def _grid(rows=4, cols=4, tall=0):
    css = "".join("#r%dc%d{left:%dpx;top:%dpx}" % (r, c, 20 + c * 90, 20 + r * 70) for r in range(rows)
                  for c in range(cols))
    buttons = "".join("<button id='r%dc%d'>%d,%d</button>" % (r, c, r, c) for r in range(rows)
                      for c in range(cols))
    if tall:
        buttons += "<div style='position:absolute;left:0;top:%dpx;width:10px;height:10px'></div>" % tall
    return GRID % (css, buttons)


@pytest.fixture
def transformed(sites):
    """The frame of B inside a transformed box; the frame of C inside that one, transformed as well."""
    sites.route("B", "/grid", "<!doctype html>" + _grid() +
                "<iframe id='c' src='%s' style='position:absolute;left:400px;top:20px;width:420px;height:320px;"
                "border:0;transform:translate(10px, 15px) scale(0.75);transform-origin:0 0'></iframe>"
                % sites.url("C", "/grid"))
    sites.route("C", "/grid", "<!doctype html>" + _grid(3, 3))
    return sites


TRANSFORMS = [("scale", "translate(40px, 30px) scale(0.5)"), ("scale-up", "translate(10px, 10px) scale(1.4)"),
              ("rotate-ish", "translate(60px, 20px) scale(0.8) skewX(-8deg)")]


@pytest.fixture(params=[False, True], ids=["plain", "humanized"])
def pointer_browser(request, firefox_binary):
    """A browser per way of moving the pointer: a click of the humanized one lands off the centre of the element."""
    with launch(firefox_binary, humanize=request.param) as browser:
        yield browser


@pytest.fixture
def pointer_page(pointer_browser):
    context, page = new_page(pointer_browser)
    yield page
    context.close()


@pytest.mark.parametrize("name,transform", TRANSFORMS, ids=[t[0] for t in TRANSFORMS])
def test_a_click_by_selector_in_a_frame_under_a_transform_lands_on_that_element(pointer_page, transformed, name,
                                                                                transform):
    """The iframe sits in a box under `transform` (translate and scale, and a skew). One click per button, by
    selector, in the frame of B and in the frame of C nested in it (which is transformed as well): each is
    recorded by the document it landed in, as a trusted click on the button that was asked for, and by that
    document only."""
    page = pointer_page
    transformed.route("A", "/t", "<!doctype html><title>t</title><body style='margin:0;height:2000px'>"
                      "<div style='transform:%s;transform-origin:0 0;position:absolute;left:30px;top:20px'>"
                      "<iframe id='b' src='%s' width='900' height='450' style='border:0'></iframe></div>"
                      % (transform, transformed.url("B", "/grid")))
    page.goto(transformed.url("A", "/t"), wait_until="load")
    frames = {"frame": wait_frame(page, transformed.url("B"), "/grid"),
              "nested": wait_frame(page, transformed.url("C"), "/grid")}
    expected = {"frame": [], "nested": []}
    for target, button in (("frame", "r2c1"), ("nested", "r1c1"), ("frame", "r3c3"), ("nested", "r2c2"),
                           ("frame", "r0c0")):
        frames[target].locator("#" + button).click()
        expected[target].append({"id": button, "trusted": True})
        for name_, frame in frames.items():
            assert frame.evaluate("window.landed") == expected[name_], (target, button, name_)


def test_the_transformed_frames_are_in_other_processes(pointer_page, transformed):
    page = pointer_page
    transformed.route("A", "/t", "<!doctype html><title>t</title><div style='transform:scale(0.5)'>"
                      "<iframe id='b' src='%s' width='900' height='450'></iframe></div>"
                      % transformed.url("B", "/grid"))
    page.goto(transformed.url("A", "/t"), wait_until="load")
    b = wait_frame(page, transformed.url("B"), "/grid")
    assert_other_process(page.main_frame, b)
    assert_other_process(b, wait_frame(page, transformed.url("C"), "/grid"))


def test_a_click_by_selector_after_scrolling_the_page_and_the_frame(pointer_page, sites):
    """The page and the frame are both scrolled, so the element is where neither document's own coordinates say:
    the click still lands on it. Below the fold of the frame, which is below the fold of the page; once in the
    frame of B and once in the frame of C nested in it (scrolled too)."""
    page = pointer_page
    sites.route("B", "/tall", "<!doctype html>" + _grid(rows=14, cols=2) +
                "<iframe id='c' src='%s' style='position:absolute;left:300px;top:20px;width:400px;height:300px;"
                "border:0'></iframe><div style='position:absolute;top:1500px;width:1px;height:1px'></div>"
                % sites.url("C", "/tall-c"))
    sites.route("C", "/tall-c", "<!doctype html>" + _grid(rows=14, cols=2))
    sites.route("A", "/scrolled", "<!doctype html><title>scrolled</title><body style='margin:0'>"
                "<div style='height:900px'>spacer</div><iframe id='b' src='%s' width='900' height='400' "
                "style='border:0'></iframe><div style='height:1200px'></div>" % sites.url("B", "/tall"))
    page.goto(sites.url("A", "/scrolled"), wait_until="load")
    outer = wait_frame(page, sites.url("B"), "/tall")
    inner = wait_frame(page, sites.url("C"), "/tall-c")
    page.evaluate("window.scrollTo(0, 700)")
    outer.evaluate("window.scrollTo(0, 500)")
    assert page.evaluate("window.scrollY") == 700 and outer.evaluate("window.scrollY") == 500
    outer.locator("#r12c1").click()
    assert outer.evaluate("window.landed") == [{"id": "r12c1", "trusted": True}]
    inner.evaluate("window.scrollTo(0, 400)")
    inner.locator("#r12c0").click()
    assert inner.evaluate("window.landed") == [{"id": "r12c0", "trusted": True}]
    assert outer.evaluate("window.landed") == [{"id": "r12c1", "trusted": True}]


# ---------------------------------------------------------------------------
# 8. screenshots
# ---------------------------------------------------------------------------

PINK, GREEN, BLUE, GOLD = (255, 0, 170), (0, 204, 68), (34, 85, 238), (255, 204, 0)


@pytest.fixture
def painted(sites):
    sites.route("A", "/paint", "<!doctype html><title>paint</title><body style='margin:0;background:#fff'>"
                "<iframe id='b' src='%s' style='position:absolute;left:50px;top:40px;width:300px;height:200px;"
                "border:0'></iframe>" % sites.url("B", "/paint"))
    sites.route("B", "/paint", "<!doctype html><body style='margin:0;background:#ff00aa'>"
                "<div id='swatch' style='position:absolute;left:20px;top:30px;width:80px;height:60px;"
                "background:#00cc44'></div>"
                "<iframe id='c' src='%s' style='position:absolute;left:120px;top:20px;width:150px;height:120px;"
                "border:0'></iframe>" % sites.url("C", "/paint"))
    sites.route("C", "/paint", "<!doctype html><body style='margin:0;background:#2255ee'>"
                "<div id='swatch' style='position:absolute;left:10px;top:10px;width:50px;height:40px;"
                "background:#ffcc00'></div>")
    return sites


def test_a_page_screenshot_shows_the_content_of_the_frames_of_other_sites(page, painted):
    page.goto(painted.url("A", "/paint"), wait_until="load")
    wait_frame(page, painted.url("C"), "/paint")
    page.wait_for_timeout(500)
    assert_other_process(page.main_frame, wait_frame(page, painted.url("B"), "/paint"))
    _, _, pixel = decode_png(page.screenshot(scale="css"))
    # outside the frame the page's own white; inside it the frame's pink; its swatch; the nested frame's blue
    assert near(pixel(10, 10), (255, 255, 255)), pixel(10, 10)
    assert near(pixel(50 + 5, 40 + 5), PINK), pixel(55, 45)
    assert near(pixel(50 + 20 + 40, 40 + 30 + 30), GREEN), pixel(110, 100)
    assert near(pixel(50 + 120 + 5, 40 + 20 + 5), BLUE), pixel(175, 65)
    assert near(pixel(50 + 120 + 10 + 25, 40 + 20 + 10 + 20), GOLD), pixel(205, 90)
    assert near(pixel(50 + 300 + 10, 40 + 10), (255, 255, 255)), "right of the frame"


@pytest.mark.parametrize("target,size,color", [("frame", (80, 60), GREEN), ("nested", (50, 40), GOLD)])
def test_a_locator_screenshot_of_an_element_in_a_frame_of_another_site(page, painted, target, size, color):
    page.goto(painted.url("A", "/paint"), wait_until="load")
    frame = {"frame": wait_frame(page, painted.url("B"), "/paint"),
             "nested": wait_frame(page, painted.url("C"), "/paint")}[target]
    page.wait_for_timeout(500)
    width, height, pixel = decode_png(frame.locator("#swatch").screenshot(scale="css"))
    assert (width, height) == size
    for x, y in ((0, 0), (width - 1, height - 1), (width // 2, height // 2), (width - 1, 0)):
        assert near(pixel(x, y), color), ((x, y), pixel(x, y))


def test_a_locator_screenshot_of_the_iframe_element_shows_the_frame(page, painted):
    page.goto(painted.url("A", "/paint"), wait_until="load")
    wait_frame(page, painted.url("C"), "/paint")
    page.wait_for_timeout(500)
    width, height, pixel = decode_png(page.locator("#b").screenshot(scale="css"))
    assert (width, height) == (300, 200)
    assert near(pixel(5, 5), PINK) and near(pixel(60, 60), GREEN) and near(pixel(125, 25), BLUE)
