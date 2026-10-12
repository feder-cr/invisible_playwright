"""Pages that replace themselves right after they load, on a real engine.

⛔ THE KNOWN-BAD INPUT, from invisible_dots (pages behind JavaScript challenges) and
reproduced here on 2026-10-12 against firefox-41 and 0.34.0:

- a read right after `goto(..., "domcontentloaded")` on a page that calls
  `location.replace` from its `<head>`, or 0 ms after `load`, was refused with
  the engine's raw `Failed to find execution context`, 5 times in 5;
- `goto(..., "networkidle")` on a page that replaces itself after `load`
  timed out, 12 times in 12: the move to another content process re-announces
  the main frame, and the lifecycle built a new Frame that had forgotten our
  navigation ([B228]). In the invisible_dots VMs, where the events arrive
  together, the same happened to `domcontentloaded` (45 s);
- `page.frames` grew by a stale iframe and a repeated main frame at every
  cross-process navigation.

[B228]'s own e2e did not see any of it: its page called `location.replace`
from the `<head>` and was waited for `load`, a case where no second process
is involved. These pages navigate AFTER `load`, same-site and cross-site
(127.0.0.1 to localhost, which Fission puts in another process), and each
case is repeated, because a race that is missed once in five is still a race.

The rounds run back to back, so a `goto` can be sent while the previous page's
own `location.replace` is in flight, and Firefox lets that one win: ours is
never heard of again, no commit and no abort. Until 41.36.0 that `goto` waited
for its timeout (2 and 3 rounds in 80, measured 2026-10-12); now the first
document committed after the command decides, and when it is not ours `goto`
raises at once with upstream Playwright's "is interrupted by another
navigation to". `_goto` accepts that answer, fast, and nothing else.
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

ROUNDS = 5


def _goto(page, url, **kwargs):
    """`page.goto`, which may answer that the previous page's own redirect won
    (module docstring). Either answer comes in seconds."""
    from invisible_playwright.sync_api import Error

    started = time.monotonic()
    try:
        page.goto(url, **kwargs)
    except Error as e:
        assert "is interrupted by another navigation to" in str(e), e
        assert "/landed" in str(e).split("another navigation to")[1], e
    assert time.monotonic() - started < 10, url


def _replace_after_load(ms: int, cross_site: bool) -> bytes:
    target = ("location.origin.replace('127.0.0.1','localhost')+'/landed'"
              if cross_site else "'/landed'")
    return (("<!doctype html><html><body><p id=p>first</p><script>"
             "addEventListener('load', () => setTimeout(() => "
             "location.replace(%s), %d))</script></body></html>")
            % (target, ms)).encode()


PAGES = {
    "/head": (b"<!doctype html><html><head><script>location.replace('/landed')"
              b"</script></head><body><p id=p>head</p></body></html>"),
    "/landed": b"<!doctype html><html><body><p id=p>landed</p></body></html>",
    "/withframe": (b"<!doctype html><html><body><iframe src='/landed'></iframe>"
                   b"</body></html>"),
}
for _ms in (0, 50, 300):
    PAGES["/r%d" % _ms] = _replace_after_load(_ms, False)
    PAGES["/x%d" % _ms] = _replace_after_load(_ms, True)


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path.split("?")[0], PAGES["/landed"])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    # Threading: Firefox's speculative connections starve a single-threaded
    # server (test_navigation_response.py).
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture(scope="module")
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4321, binary_path=firefox_binary,
                             headless=True) as browser:
        yield browser.new_page()


@pytest.mark.parametrize("path", ["/head", "/r0", "/r50", "/r300",
                                  "/x0", "/x50", "/x300"])
def test_a_read_right_after_goto_answers(page, site, path):
    for i in range(ROUNDS):
        _goto(page, "%s%s?i=%d" % (site, path, i), wait_until="domcontentloaded",
              timeout=15_000)
        # Each read is made at once, while the page may still be moving: none
        # of them may surface the engine's refusal of a vanished world. A count
        # is of the page as it is NOW, and that can be the replacing document
        # before its parser reached <body> (measured under -n 4 on Windows):
        # 0 is a true answer there, as in Playwright, an error is not.
        assert page.locator("body").count() in (0, 1)
        assert page.inner_text("#p", timeout=10_000) in ("first", "head",
                                                         "landed")
        # The caller's code comes after a read that waited, on the page that
        # stayed. Sent while the page was still moving it would raise
        # Playwright's navigation error rather than be sent again, which ran
        # a script twice until core 41.38.0 ([B285],
        # `tests/test_the_callers_code_runs_once_e2e.py`).
        assert page.evaluate("1 + 1") == 2


@pytest.mark.parametrize("path", ["/r0", "/r50", "/x0", "/x50"])
def test_networkidle_is_reached_on_the_page_that_replaced_ours(page, site, path):
    for i in range(ROUNDS):
        _goto(page, "%s%s?n=%d" % (site, path, i), wait_until="networkidle",
              timeout=15_000)
        assert page.url.endswith("/landed"), page.url


def test_the_frames_follow_the_documents(page, site):
    """Every navigation here moves the main frame to another process, and the
    engine reports no detach for the iframe left behind.

    Not asserted: that the iframe is always listed. When the engine puts it in
    another process it reports it detached (measured on firefox-41, once in
    three), and the list says what the engine can drive. What is asserted is
    that nothing of a document already left is listed, and the main frame
    once: the defect listed eight frames for two after six navigations."""
    for i in range(4):
        page.goto("%s/withframe?f=%d" % (site, i), wait_until="load")
        frames = page.frames
        assert frames[0] is page.main_frame
        assert [f for f in frames if f is page.main_frame] == [page.main_frame]
        assert all(f.parent_frame is page.main_frame for f in frames[1:])
        assert len(frames) <= 2, [f.url for f in frames]
        page.goto("%s/landed?f=%d" % (site.replace("127.0.0.1", "localhost"), i),
                  wait_until="load")
        assert page.frames == [page.main_frame], [f.url for f in page.frames]


def test_reads_wait_as_playwright_s_do(page, site):
    page.goto(site + "/landed")
    assert page.is_visible("#missing") is False
    assert page.is_hidden("#missing") is True
    page.evaluate("setTimeout(() => { const b = document.createElement('b');"
                  " b.id = 'late'; b.textContent = 'late';"
                  " document.body.appendChild(b); }, 500)")
    assert page.inner_text("#late", timeout=5_000) == "late"
    from invisible_playwright.sync_api import TimeoutError as PlaywrightTimeout
    with pytest.raises(PlaywrightTimeout, match="Timeout 300ms exceeded"):
        page.inner_text("#never", timeout=300)


def test_expect_reads_the_state_it_names(page, site):
    from invisible_playwright._pw.sync_api import expect

    page.goto(site + "/landed")
    page.set_content("<input type=checkbox id=c><p id=h hidden>x</p>")
    with pytest.raises(AssertionError):
        expect(page.locator("#c")).to_be_checked(timeout=500)
    expect(page.locator("#c")).not_to_be_checked(timeout=500)
    with pytest.raises(AssertionError):
        expect(page.locator("#h")).to_be_visible(timeout=500)
    expect(page.locator("#h")).to_be_hidden(timeout=500)
    expect(page.locator("#missing")).to_be_hidden(timeout=500)
