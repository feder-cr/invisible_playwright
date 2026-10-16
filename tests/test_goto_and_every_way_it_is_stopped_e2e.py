"""A goto that something stops or replaces on its way raises at once, or lands
on its page; it never answers OK on another page and never waits out its
timeout ([B302], the class).

Until firefox-46 the engine dropped every abort of the main frame's
navigation, a workaround for the spurious one a Fission process swap fires,
and a navigation the page being left stopped stayed pending: the
document.write() after a document.open() was taken for its commit, and
`goto` answered OK on the OLD page (7 times in 40, firefox-45), or waited out
its timeout (45 s in a Dot's MCP server). No test covered it, because no test
stopped a navigation that was on its way. This one asks every way a page or
the caller can, and checks each answer against upstream Playwright on its own
Firefox (1.63, Firefox 155, `C:/tmp/b230/interrupt_class_probe.py upstream`
and `second_goto_probe.py upstream`, measured 2026-10-14):

- document.open, window.stop: NS_BINDING_ABORTED at once (0.05 s), the old
  page stays;
- location.replace, location.assign, a form submit, an iframe navigating the
  top, history.back: NS_BINDING_ABORTED at once, the page goes where the
  page sent it;
- history.pushState, a meta refresh the page adds, nothing: the goto lands
  on its page;
- a second goto from the caller: the first is "interrupted by another
  navigation", the second lands.

One answer is ours and not upstream's word for word: `history.back()`. Our
build keeps the back-forward cache on, as retail Firefox does, and upstream's
turns it off; going back restores the previous page from the cache, the page
being left enters it, and the engine cannot tell that stop from a process
swap's, so the goto learns of it from the restored page's commit and raises
"is interrupted by another navigation" to it, at once. Same outcome, other
words; both are accepted for `back`, and only for it. The same cache is why,
after a meta refresh, upstream's page then follows the refresh once the goto's
page has loaded while ours stays, as retail's does under geckodriver (the
refresh goes into the cache with the page that set it); the goto's answer is
the same.

One case is a race the engine owns, not an answer of ours: for `meta` the refresh
the page adds has delay 0 and the docShell is busy, so it is queued rather than
timed (`nsDocShell.cpp`, `mRefreshURIList`) and the queue outlives the document
that made it long enough to be replayed on the page the goto landed on. Measured
on firefox-46: our Windows stays on the goto's page 2 of 2 and retail under
geckodriver 4 of 4, our Linux leaves it 1 of 2 ([B305], a declared partial
remedy: no retail Linux of this major exists on the bench to judge which is
stock). So `meta` admits either destination and still asserts what [B302] is
about - the goto does not raise, it waited for its own page, and it never answers
on the page being left.

Each kind runs with the goto's page on the same site (one process) and on
another (`localhost` against `127.0.0.1`, a Fission process swap, the case the
dropped abort was written for), twice, back to back. The page being left acts
once the server has the goto's request, and the server holds the goto's page
for 1.5 s, so the goto is on its way when the page acts on any machine. (It
acted on a timer 300 ms after its load until a slow CI runner started the goto
later than that: nothing was left to stop, and the goto landed.)
"""
from __future__ import annotations

import asyncio
import http.server
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

HELD_S = 1.5
ACT = {
    "docopen": "document.open(); document.write('<html><body><h1>rewritten</h1></body></html>'); document.close();",
    "stop": "window.stop();",
    "replace": "location.replace('/other?from=replace');",
    "assign": "location.assign('/other?from=assign');",
    "pushstate": "history.pushState({}, '', '/pushed');",
    "back": "history.back();",
    "submit": "document.getElementById('f').submit();",
    "meta": "const m = document.createElement('meta'); m.httpEquiv = 'refresh'; m.content = '0;url=/other?from=meta'; document.head.appendChild(m);",
    "iframe_top": None,
    "none": "",
}
#: Upstream Playwright's answer to each kind, and where the page then is.
EXPECT = {
    "docopen": ("stopped", "/old"),
    "stop": ("stopped", "/old"),
    "replace": ("stopped", "/other?from=replace"),
    "assign": ("stopped", "/other?from=assign"),
    "back": ("stopped", "/start"),
    "submit": ("stopped", "/other?from=submit"),
    "iframe_top": ("stopped", "/other?from=iframe"),
    "pushstate": ("landed", None),
    #: The engine's race, not an answer: see the note above and [B305].
    "meta": ("landed-or-refreshed", "/other?from=meta"),
    "none": ("landed", None),
}


#: The queries of the goto requests the server has had since it served the
#: page being left with the same query.
ARRIVED = set()


def _when_the_goto_arrives(key, act):
    """Script that asks the server every 50 ms whether the goto's request has
    arrived, and runs `act` once it has."""
    return ("const w = setInterval(() => fetch('/act?%s', {cache: 'no-store'})"
            ".then(r => r.text()).then(t => { if (t === 'go') { clearInterval(w); %s } }),"
            " 50);" % (key, act))


def _old_page(kind, key):
    if kind == "iframe_top":
        inner = _when_the_goto_arrives(key, "top.location = '/other?from=iframe';")
        return ("<html><body><p>old</p><iframe srcdoc=\"<script>%s</script>\"></iframe>"
                "</body></html>" % inner.replace('"', "&quot;"))
    return ("<html><head></head><body><p>old</p><form id=f action='/other' method=get>"
            "<input name=from value=submit></form><script>%s</script></body></html>"
            % _when_the_goto_arrives(key, ACT[kind]))


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path in ("/new", "/held"):
                ARRIVED.add(query)
                time.sleep(HELD_S)
                body = "<html><body><p>new</p></body></html>"
            elif path == "/act":
                body = "go" if query in ARRIVED else "wait"
            elif path == "/old":
                # Both sites run with the same query: the page waits for a
                # goto made after it was served.
                ARRIVED.discard(query)
                body = _old_page(query.split("k=")[1].split("&")[0], query)
            else:
                body = "<html><body><p>%s</p></body></html>" % path
            body = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                pass  # the browser let go of a response it stopped

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.mark.wall_clock
@pytest.mark.parametrize("kind", list(ACT))
def test_a_goto_stopped_on_its_way_raises_or_lands_and_never_lies(firefox_binary, server, kind):
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.sync_api import Error

    same = "http://127.0.0.1:%d" % server
    cross = "http://localhost:%d" % server
    answer, end = EXPECT[kind]
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        for site, target_base in (("same", same), ("cross", cross)):
            for i in range(2):
                page.goto(same + "/start?k=%s&i=%d" % (kind, i))
                page.goto(same + "/old?k=%s&i=%d" % (kind, i), wait_until="load")
                target = target_base + "/new?k=%s&i=%d" % (kind, i)
                began = time.monotonic()
                error = None
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=10_000)
                except Error as e:
                    error = e.message
                took = time.monotonic() - began
                where = (kind, site, i, error, took)
                if answer == "stopped":
                    assert error is not None, where
                    assert ("NS_BINDING_ABORTED" in error
                            or (kind == "back"
                                and "is interrupted by another navigation" in error)), where
                    assert took < HELD_S, where
                    expected = same + end
                    if end in ("/old", "/start"):
                        expected = same + "%s?k=%s&i=%d" % (end, kind, i)
                    page.wait_for_url(expected, timeout=5_000)
                else:
                    assert error is None, where
                    # It waited for its page, which the server held.
                    assert took >= HELD_S - 0.1, where
                    left = same + "/old?k=%s&i=%d" % (kind, i)
                    if answer == "landed-or-refreshed":
                        assert page.url in (target, same + end), (where, page.url)
                    else:
                        assert page.url == target, (where, page.url)
                    # Whatever the page did next, the goto never answers on the
                    # document it was leaving: that is the lie [B302] was about.
                    assert page.url != left, (where, page.url)


@pytest.mark.wall_clock
def test_a_second_goto_interrupts_the_first_and_lands(firefox_binary, server):
    from invisible_playwright.async_api import InvisiblePlaywright

    base = "http://127.0.0.1:%d" % server

    async def run():
        async with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                       headless=True) as browser:
            page = await browser.new_page()
            for i in range(3):
                await page.goto(base + "/start?i=%d" % i)
                began = time.monotonic()

                async def go(url, after=None):
                    # The second goto starts once the server has the first's
                    # request, so the first is on its way on any machine.
                    while after is not None and after not in ARRIVED:
                        assert time.monotonic() - began < HELD_S, "the first goto never left"
                        await asyncio.sleep(0.02)
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                        return None
                    except Exception as e:
                        return str(e)
                ARRIVED.discard("first=%d" % i)
                first, second = await asyncio.gather(
                    go(base + "/held?first=%d" % i),
                    go(base + "/second?i=%d" % i, after="first=%d" % i))
                took = time.monotonic() - began
                assert first is not None and "is interrupted by another navigation" in first, first
                assert second is None, second
                assert took < HELD_S, took
                assert page.url == base + "/second?i=%d" % i

    asyncio.run(run())
