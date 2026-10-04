"""Popups (window.open, target=_blank) must be real Pages.

Regression: pages opened by ``window.open`` / ``target=_blank`` appear in
Firefox but are never exposed to Playwright - no ``page`` event on the
context, no ``popup`` event on the opener, ``expect_page()`` /
``expect_popup()`` time out, and ``context.pages`` only ever holds the
opener. Only pages created by ``new_page()`` are tracked.

Cause: the Juggler server (``_juggler/server.py``) builds a
``PageDispatcher`` solely inside ``BrowserContextDispatcher.op_new_page``
and ``BrowserDispatcher._route_browser_event`` merely records the session
for ``Browser.attachedToTarget`` without creating a page for targets it
did not request - even though the engine DOES emit the event for popups
(verified at the protocol level: ``targetInfo`` carries ``targetId``,
``openerId``, ``browserContextId`` and ``type == 'page'``).

Layers:
  * ``e2e`` - launch the real binary against a LOCAL HTTP harness on
    127.0.0.1 (fully offline). Every popup is opened by a REAL click, so
    the opener holds a user gesture: ``evaluate(() => window.open(...))``
    without a gesture is blocked by Firefox (returns null) and would test
    the popup blocker instead of page tracking.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.e2e


class _SilentHandler(BaseHTTPRequestHandler):
    timeout = 5
    PAYLOAD = b""

    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self.PAYLOAD)


def _serve(payload: bytes):
    handler_cls = type("_H", (_SilentHandler,), {"PAYLOAD": payload})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


POPUP_HTML = b"""<!doctype html><html><head><title>popup</title></head><body>
<h1>popup page</h1>
<button id="ping">ping</button>
<script>document.getElementById('ping').addEventListener('click', () => document.title = 'pong')
if (location.search.indexOf('close=1') >= 0) setTimeout(() => window.close(), 300);</script>
</body></html>"""


@pytest.fixture(scope="module")
def harness():
    """One popup target + one opener page with four openers (3 buttons, 1 link)."""
    sp, pp = _serve(POPUP_HTML)
    popup_url = f"http://127.0.0.1:{pp}/popup"
    opener_html = f"""<!doctype html><html><head><title>opener</title></head><body>
<button id="open-simple" onclick="window.open('{popup_url}', '_blank')">simple</button>
<button id="open-features" onclick="window.open('{popup_url}', 'featwin', 'width=600,height=500')">feat</button>
<button id="open-noopener" onclick="window.open('{popup_url}', '_blank', 'noopener')">noopen</button>
<a id="open-link" href="{popup_url}" target="_blank">link</a>
<button id="open-closing" onclick="window.open('{popup_url}?close=1', '_blank')">closing</button>
</body></html>""".encode("utf-8")
    so, po = _serve(opener_html)
    try:
        yield {"opener_url": f"http://127.0.0.1:{po}/",
               "popup_url": popup_url}
    finally:
        so.shutdown()
        sp.shutdown()


@pytest.fixture
def browser(firefox_binary):
    """One browser per test: the sync client cannot nest two sessions in one
    thread, so the persistent-context test must not share a browser with the
    other arms."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True, humanize=False) as b:
        yield b


def _open_context_page(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    return ctx, page


def _goto_opener(page, harness):
    page.goto(harness["opener_url"], wait_until="domcontentloaded",
              timeout=30_000)
    page.wait_for_selector("#open-simple", timeout=10_000)


def test_window_open_plain_is_a_page(browser, harness):
    """``window.open(url, '_blank')`` in a click gesture: page event, popup
    event, opener(), presence in context.pages, interaction, clean close."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        seen = []
        page.on("popup", lambda p: seen.append(p))

        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-simple")
        popup = info.value
        popup.wait_for_url(harness["popup_url"], timeout=10_000)

        assert popup.url == harness["popup_url"], (
            f"popup landed on the wrong URL: {popup.url!r}")
        assert len(ctx.pages) == 2, (
            f"expected opener + popup in context.pages, got {len(ctx.pages)}")
        assert popup in ctx.pages
        assert seen == [popup], (
            "the opener never emitted 'popup' for its own window.open")
        assert popup.opener() == page, (
            "popup.opener() did not resolve to the opening page")

        # A popup must behave like any other page.
        popup.wait_for_selector("#ping", timeout=10_000)
        popup.click("#ping")
        popup.wait_for_function("document.title === 'pong'", timeout=10_000)
        assert popup.title() == "pong"

        popup.close()
        assert popup.is_closed()
        assert len(ctx.pages) == 1, (
            f"closed popup still listed in context.pages: "
            f"{[p.url for p in ctx.pages]}")
    finally:
        ctx.close()


def test_window_open_with_features_is_a_page(browser, harness):
    """``window.open`` WITH a feature string (width/height: an OS-level window
    on a headed browser) must still surface as a Page, not vanish."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-features")
        popup = info.value
        popup.wait_for_url(harness["popup_url"], timeout=10_000)

        assert popup.url == harness["popup_url"]
        assert len(ctx.pages) == 2
        assert popup.opener() == page
        popup.wait_for_selector("#ping", timeout=10_000)
        popup.close()
        assert popup.is_closed()
    finally:
        ctx.close()


def test_window_open_with_a_size_keeps_that_size(browser, harness):
    """A popup opened with ``width=600,height=500`` reports that size, the
    way every Firefox does; a tab opened from the same page keeps the
    context's viewport.

    Up to firefox-35 Juggler gave every popup the context's default viewport,
    so this popup reported the tab's 1920x947 (whatever the profile's screen
    minus its chrome is). Retail 151 reports the requested size: 499x400 for
    a 500x400 request at 1.5 DPR, which is why one CSS pixel of slack is
    allowed - the profile's DPR is not always 1. The fix is in the engine
    (the explicit-size chrome flag Juggler tests for, and the appWindow it
    reads it from), so this fails on any engine without it."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        tab_size = page.evaluate("[innerWidth, innerHeight]")
        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-features")
        popup = info.value
        popup.wait_for_url(harness["popup_url"], timeout=10_000)
        popup.wait_for_load_state("load", timeout=10_000)
        # The size is final when the window is shown; reading it once more
        # after a beat catches a late resize to the context viewport.
        first = popup.evaluate("[innerWidth, innerHeight]")
        popup.wait_for_timeout(1000)
        size = popup.evaluate("[innerWidth, innerHeight]")
        assert first == size, (
            f"the popup changed size after load: {first} then {size}")
        assert abs(size[0] - 600) <= 1 and abs(size[1] - 500) <= 1, (
            f"window.open(..., 'width=600,height=500') reported {size}; the "
            f"opener's tab is {tab_size}. A popup that asked for a size must "
            f"keep it.")
        popup.close()

        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-simple")
        tab = info.value
        tab.wait_for_url(harness["popup_url"], timeout=10_000)
        assert tab.evaluate("[innerWidth, innerHeight]") == tab_size, (
            "a window.open without features is a tab of the same window and "
            "must have the opener's size")
        tab.close()
    finally:
        ctx.close()


def test_window_open_noopener_is_a_tracked_page(browser, harness):
    """``noopener`` severs in-page ``window.opener``, but the ENGINE still
    reports the opening target in ``openerId`` - verified at the protocol
    level - so the automation-level opener stays informative (same as the
    upstream passthrough). What ``noopener`` must NOT do is lose the page:
    url, presence in context.pages, interaction, close all work."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-noopener")
        popup = info.value
        popup.wait_for_url(harness["popup_url"], timeout=10_000)

        assert popup.url == harness["popup_url"]
        assert popup in ctx.pages
        # Engine truth: Firefox sends openerId even for noopener popups.
        assert popup.opener() == page
        assert popup.evaluate("window.opener === null"), (
            "the test's noopener shape is broken: in-page window.opener "
            "should be null")
        popup.wait_for_selector("#ping", timeout=10_000)
        popup.click("#ping")
        popup.wait_for_function("document.title === 'pong'", timeout=10_000)
        assert popup.title() == "pong"
        popup.close()
        assert popup.is_closed()
    finally:
        ctx.close()


def test_target_blank_link_expect_popup(browser, harness):
    """A plain ``<a target=_blank>`` click: ``page.expect_popup()`` around the
    click must yield the new page with the right URL."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        with page.expect_popup(timeout=15_000) as info:
            page.click("#open-link")
        popup = info.value
        popup.wait_for_url(harness["popup_url"], timeout=10_000)

        assert popup.url == harness["popup_url"]
        assert popup in ctx.pages
        assert popup.opener() == page
        popup.close()
        assert popup.is_closed()
    finally:
        ctx.close()


def test_popup_in_persistent_context(firefox_binary, harness, tmp_path):
    """Same tracking inside the DEFAULT persistent context (``profile_dir=``),
    whose ``browserContextId`` is absent rather than a fresh container."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True, humanize=False,
                             profile_dir=str(tmp_path / "profile")) as ctx:
        page = ctx.new_page()
        _goto_opener(page, harness)
        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-simple")
        popup = info.value
        popup.wait_for_url(harness["popup_url"], timeout=10_000)

        assert popup.url == harness["popup_url"]
        assert popup in ctx.pages
        assert popup.opener() == page
        popup.wait_for_selector("#ping", timeout=10_000)
        popup.close()
        assert popup.is_closed()


def test_no_phantom_page_at_startup(firefox_binary, tmp_path):
    """Targets predating the browser (the initial tab at `Browser.enable`)
    are recorded, never adopted: 0 pages before `new_page()`, exactly 1
    after - in a fresh context AND in the persistent default context.

    Sequential launches (never nested): two sync sessions cannot share one
    thread."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True, humanize=False) as browser:
        ctx = browser.new_context()
        try:
            assert len(ctx.pages) == 0
            ctx.new_page()
            assert len(ctx.pages) == 1
        finally:
            ctx.close()

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True, humanize=False,
                             profile_dir=str(tmp_path / "profile2")) as pctx:
        assert len(pctx.pages) == 0
        pctx.new_page()
        assert len(pctx.pages) == 1, (
            f"phantom page adopted at startup: "
            f"{[p.url for p in pctx.pages]}")


def test_context_close_closes_each_page_exactly_once(browser):
    """3 pages, one `ctx.close()`: all `is_closed()`, exactly one `close`
    event each - the context loop iterates a copy, so discards during the
    loop cannot skip or double a page."""
    ctx = browser.new_context()
    counts: dict = {}
    pages = []
    for i in range(3):
        p = ctx.new_page()
        counts[i] = 0
        p.on("close", lambda _p, i=i: counts.__setitem__(i, counts[i] + 1))
        pages.append(p)
    ctx.close()
    for i, p in enumerate(pages):
        assert p.is_closed(), f"page {i} not closed"
        assert counts[i] == 1, f"page {i} got {counts[i]} close events"


def test_failed_adoption_warns_and_raises(browser, monkeypatch):
    """A build that blows up surfaces LOUDLY: a `RuntimeWarning` naming the
    target (not just a `handler_errors` entry nobody reads) plus the
    original error re-raised to the `new_page()` caller."""
    import pytest

    import invisible_playwright._juggler.server as srv

    def _boom(*_a, **_k):
        raise RuntimeError("boom-adoption")

    monkeypatch.setattr(srv, "PageDispatcher", _boom)
    ctx = browser.new_context()
    try:
        # NOTE: the client rewrites server errors (`BrowserContext.new_page:
        # ...`), so match the message, not the server-side class.
        with pytest.warns(RuntimeWarning, match="adoption of target"):
            with pytest.raises(Exception, match="boom-adoption"):
                ctx.new_page()
    finally:
        ctx.close()


def test_window_open_features_headed_humanized(firefox_binary, harness):
    """Headed + `humanize=True` (the real-user shape): a `window.open` WITH a
    feature string is captured, usable and closable like any page."""
    import os

    import pytest

    if not os.environ.get("DISPLAY"):
        pytest.skip("headed test needs a display")
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=False, humanize=True) as browser:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            _goto_opener(page, harness)
            with ctx.expect_page(timeout=15_000) as info:
                page.click("#open-features")
            popup = info.value
            popup.wait_for_url(harness["popup_url"], timeout=10_000)
            assert popup.url == harness["popup_url"]
            assert popup in ctx.pages
            popup.wait_for_selector("#ping", timeout=10_000)
            popup.close()
            assert popup.is_closed()
        finally:
            ctx.close()


def test_popup_document_reaches_the_context(browser, harness):
    """The popup's OWN document request and response reach
    `context.on("request")` / `("response")`, once each, with a readable body.

    They are sent before the popup's Page exists, so without the page holding
    its events from the start of its construction only the subresources
    arrived: the document - the only copy of a PDF opened in a new tab - was
    out of reach."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        requests, responses = [], []
        ctx.on("request", lambda r: requests.append(r.url))
        ctx.on("response", lambda r: responses.append(r))
        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-simple")
        popup = info.value
        popup.wait_for_load_state("load", timeout=10_000)
        popup.wait_for_timeout(500)
        url = harness["popup_url"]
        assert requests.count(url) == 1, (url, requests)
        documents = [r for r in responses if r.url == url]
        assert len(documents) == 1, (url, [r.url for r in responses])
        assert b"popup page" in documents[0].body()
        popup.close()
    finally:
        ctx.close()


def test_a_popup_the_site_closes_is_closed(browser, harness):
    """`window.close()` from the popup ends its Page: `close` fires once,
    `is_closed()` is true and `context.pages` drops it, with no
    `page.close()` from the caller."""
    ctx, page = _open_context_page(browser)
    try:
        _goto_opener(page, harness)
        with ctx.expect_page(timeout=15_000) as info:
            page.click("#open-closing")
        popup = info.value
        closes = []
        popup.on("close", lambda _p: closes.append(1))
        deadline = 50
        while not popup.is_closed() and deadline:
            page.wait_for_timeout(100)
            deadline -= 1
        page.wait_for_timeout(300)
        assert popup.is_closed()
        assert len(closes) <= 1
        assert ctx.pages == [page], [p.url for p in ctx.pages]
    finally:
        ctx.close()
