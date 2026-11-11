"""What a frame of another site starts reaches the page, and the answer reaches back.

A page of site A holds a frame of site B, which holds a frame of site C. From inside each of those two frames a
script opens a dialog (`alert`, `confirm`, `prompt`), a link starts a download, and `window.open` or a
`target=_blank` link opens a popup. In Fission the frame's process is not the page's, and each of these crosses
back to the browser process: the dialog is raised by the frame's process and answered by the driver, the download
belongs to the frame's channel, the popup is created by the frame's window with the frame's origin as its opener.

Asserted, and the same as stock Playwright Firefox does it:

* the `dialog` event of the PAGE fires for a dialog of a frame, with its type, message and default value, and what
  the handler decides is what the frame's script gets back (`confirm` true or false, `prompt` the text or null);
* `page.expect_download()` fires for a link of the frame, with the file name, the url and the bytes served;
* `page.expect_popup()` fires for `window.open` and for a `target=_blank` link of the frame; the popup is at the
  url asked for and `popup.opener()` is the page whose frame opened it; its `window.opener` is there for
  `window.open` and absent for a `target=_blank` link, with or without `rel=noopener` (a link to a new tab is
  `noopener` by default).

The first test proves the frames are in other processes (`_fission_frames.assert_other_process`), so the file
cannot turn into a same-process suite.
"""
from __future__ import annotations

import pytest

from _fission_frames import Sites, assert_other_process, launch, new_page, wait_frame

pytestmark = pytest.mark.e2e

PAYLOAD = b"fission-frame-download\x00\x01\x02" * 50

#: The controls of a document that starts things; `%s` is the url of the popup it opens.
CONTROLS = ("<button id='open' onclick=\"window.__win = window.open(%(popup)s)\">open</button>"
            "<a id='blank' href=%(popup)s target='_blank'>blank</a>"
            "<a id='noopener' href=%(popup)s target='_blank' rel='noopener'>noopener</a>"
            "<a id='dl' href='/file.bin' download>download</a>"
            "<a id='dl-header' href='/attachment.bin'>download by header</a>")


@pytest.fixture
def sites():
    with Sites() as s:
        popup = "'%s'" % s.url("C", "/popup")
        s.route("A", "/top", "<!doctype html><title>top</title><iframe id='b' src='%s' width='600' height='300'>"
                "</iframe>" % s.url("B", "/frame"))
        s.route("B", "/frame", "<!doctype html><title>frame</title>" + CONTROLS % {"popup": popup}
                + "<iframe id='c' src='%s' width='450' height='150'></iframe>" % s.url("C", "/nested"))
        s.route("C", "/nested", "<!doctype html><title>nested</title>" + CONTROLS % {"popup": popup})
        s.route("C", "/popup", "<!doctype html><title>popup</title><body>popup at C"
                "<script>window.__opener = window.opener !== null;</script>")
        for site in ("B", "C"):
            s.route(site, "/file.bin", PAYLOAD, ctype="application/octet-stream")
            s.route(site, "/attachment.bin", PAYLOAD, ctype="application/octet-stream",
                    headers=[("Content-Disposition", 'attachment; filename="by-header.bin"')])
        yield s


@pytest.fixture(scope="module")
def browser(firefox_binary):
    with launch(firefox_binary) as browser:
        yield browser


@pytest.fixture
def page(browser):
    context, page = new_page(browser)
    yield page
    context.close()


@pytest.fixture
def opened(page, sites):
    """The page loaded, and the frame of B and the frame of C nested in it."""
    page.goto(sites.url("A", "/top"), wait_until="load")
    b = wait_frame(page, sites.url("B"), "/frame")
    c = wait_frame(page, sites.url("C"), "/nested")
    return page, {"frame": b, "nested": c}


def test_the_frames_are_in_other_processes(opened):
    page, frames = opened
    assert_other_process(page.main_frame, frames["frame"])
    assert_other_process(frames["frame"], frames["nested"])


# ---------------------------------------------------------------------------
# dialogs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["frame", "nested"])
@pytest.mark.parametrize("kind,accept,text,expected", [
    ("alert", True, None, "returned"),
    ("confirm", True, None, True),
    ("confirm", False, None, False),
    ("prompt", True, "typed answer", "typed answer"),
    ("prompt", False, None, None),
])
def test_a_dialog_of_a_frame_reaches_the_page_and_the_answer_goes_back(opened, target, kind, accept, text,
                                                                       expected):
    page, frames = opened
    frame = frames[target]
    seen = []

    def on_dialog(dialog):
        seen.append((dialog.type, dialog.message, dialog.default_value))
        if accept:
            dialog.accept(text) if text is not None else dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", on_dialog)
    call = {"alert": "alert('hello from the frame'); window.__r = {v: 'returned'}",
            "confirm": "window.__r = {v: confirm('sure?')}",
            "prompt": "window.__r = {v: prompt('name?', 'default text')}"}[kind]
    frame.evaluate("() => { window.__r = null; setTimeout(() => { %s }, 0); }" % call)
    frame.wait_for_function("window.__r !== null")
    assert frame.evaluate("window.__r.v") == expected
    message, default = {"alert": ("hello from the frame", ""), "confirm": ("sure?", ""),
                        "prompt": ("name?", "default text")}[kind]
    assert seen == [(kind, message, default)]


def test_two_dialogs_one_after_the_other_from_different_frames(opened):
    """The dialog of the nested frame, then the one of its parent: each answered in turn by the same handler."""
    page, frames = opened
    answers = iter([True, False])
    page.on("dialog", lambda dialog: dialog.accept() if next(answers) else dialog.dismiss())
    nested, frame = frames["nested"], frames["frame"]
    nested.evaluate("() => { window.__r = null; setTimeout(() => { window.__r = confirm('one'); }, 0); }")
    nested.wait_for_function("window.__r !== null")
    frame.evaluate("() => { window.__r = null; setTimeout(() => { window.__r = confirm('two'); }, 0); }")
    frame.wait_for_function("window.__r !== null")
    assert nested.evaluate("window.__r") is True
    assert frame.evaluate("window.__r") is False


# ---------------------------------------------------------------------------
# downloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["frame", "nested"])
@pytest.mark.parametrize("link,filename", [("#dl", "file.bin"), ("#dl-header", "by-header.bin")])
def test_a_download_started_in_a_frame_reaches_expect_download(opened, sites, target, link, filename):
    page, frames = opened
    frame = frames[target]
    site, base = ("B", "/frame") if target == "frame" else ("C", "/nested")
    with page.expect_download() as info:
        frame.locator(link).click()
    download = info.value
    assert download.suggested_filename == filename
    assert download.url.startswith(sites.url(site) + "/")
    assert download.failure() is None
    with open(download.path(), "rb") as saved:
        assert saved.read() == PAYLOAD
    assert frame.url == sites.url(site, base), "the frame stayed where it was"


# ---------------------------------------------------------------------------
# popups
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["frame", "nested"])
@pytest.mark.parametrize("opener_control,has_opener", [("#open", True), ("#blank", False), ("#noopener", False)])
def test_a_popup_opened_from_a_frame_reaches_expect_popup(opened, sites, target, opener_control, has_opener):
    page, frames = opened
    with page.expect_popup() as info:
        frames[target].locator(opener_control).click()
    popup = info.value
    popup.wait_for_load_state("load")
    assert popup.url == sites.url("C", "/popup")
    assert popup.evaluate("document.title") == "popup"
    assert popup.evaluate("window.__opener") is has_opener
    assert popup.opener() == page, "the popup's opener is the page whose frame opened it, noopener or not"
    assert popup in page.context.pages
    popup.close()
