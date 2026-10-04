"""A page's number in the session follows the script, not the engine's timing.

⛔ WHAT IT REPLACES ([B237]). Every rhythm a page draws - the pause before a
field, the keys, the clicks, the drag, the cursor's paths - is drawn under the
page's number in the session, so that two tabs never replay each other. The
server counted those numbers as it built each page, after the engine had
answered; the cursor counted again, at each page's first movement. Two
counters for one fact, and the server's followed the engine:

* `asyncio.gather(browser.new_page(), browser.new_page())`: the client sends
  each `newPage` only after its own `newContext` is answered, so the first
  call became page 1 in 4 runs out of 10 (firefox-35, measured on Windows);
* a popup took the next number too, so a `new_page()` gathered with the click
  that opens one became page 2 in 2 runs out of 10;
* a script that moved on its second page first gave it 0 in the cursor and 1
  in the server.

The pages kept differing from each other, but the seed no longer reproduced
WHICH tab had which rhythm.

Now the client reserves the number at the call (`Browser._reserve_page_number`
in the vendored client, the only counter), the server takes it from the
request, a popup is numbered from its opener in a space of its own
(`_behaviour.popup_number`), and the cursor reads the number from the page.
"""
from __future__ import annotations

import asyncio
import http.server
import itertools
import threading
from types import SimpleNamespace

import pytest

from invisible_playwright._behaviour import popup_number
from invisible_playwright._juggler import server as srv
from invisible_playwright._juggler.connection import EventListeners
from invisible_playwright._juggler.server import (
    BrowserContextDispatcher, BrowserDispatcher, Server,
)
from invisible_playwright._pw._impl._browser import Browser
from invisible_playwright._pw._impl._browser_context import (
    BrowserContext, page_number_for_call,
)


# -- the client: the number is reserved at the call --------------------------

def _impl_browser():
    browser = object.__new__(Browser)
    browser._page_numbers = itertools.count()
    browser._connection = SimpleNamespace(
        wrap_api_call=lambda cb, title=None: cb())
    return browser


@pytest.mark.unit
def test_browser_new_page_numbers_by_call_when_the_engine_answers_backwards():
    """Known-bad, before: the number was given where `newPage` arrived, and
    `browser.new_page()` sends `newPage` only once its `newContext` is
    answered. Here the second call's context is answered first."""
    browser = _impl_browser()
    gates = [asyncio.Event(), asyncio.Event()]
    reached = []

    class _Context:
        def __init__(self, call):
            self.call = call

        async def _new_page(self, number):
            reached.append(self.call)
            return SimpleNamespace(call=self.call, number=number)

    calls = itertools.count()

    async def new_context(**_kw):
        call = next(calls)
        await gates[call].wait()
        return _Context(call)

    browser.new_context = new_context

    async def drive():
        both = asyncio.gather(browser.new_page(), browser.new_page())
        await asyncio.sleep(0)
        gates[1].set()
        await asyncio.sleep(0.01)
        gates[0].set()
        return await both

    pages = asyncio.run(drive())
    assert reached == [1, 0], "the engine did not answer backwards"
    assert [(p.call, p.number) for p in pages] == [(0, 0), (1, 1)]


@pytest.mark.unit
def test_context_new_page_sends_the_number_with_the_request():
    browser = _impl_browser()
    context = object.__new__(BrowserContext)
    context._owner_page = None
    context._browser = browser
    sent = []

    async def send(method, timeout, params=None):
        sent.append((method, params))
        return SimpleNamespace(_object="page %d" % len(sent))

    context._channel = SimpleNamespace(send=send)

    async def drive():
        return [await context.new_page(), await context.new_page()]

    assert asyncio.run(drive()) == ["page 1", "page 2"]
    assert sent == [("newPage", {"pageNumber": 0}),
                    ("newPage", {"pageNumber": 1})]


class _Impl:
    """A browser's implementation object, as far as numbering goes: its
    `new_page` takes the number on its first step, like the vendored one."""

    def __init__(self):
        self._page_numbers = itertools.count()
        self._impl_obj = self
        self.taken = []

    def _reserve_page_number(self):
        return next(self._page_numbers)

    async def new_context(self, **_kw):
        browser = self

        class _Ctx:
            _impl_obj = SimpleNamespace(_browser=browser)

            async def new_page(self, **_kw2):
                browser.taken.append(page_number_for_call(browser))
                return SimpleNamespace(context=self)

            async def add_cookies(self, cookies):
                pass

        return _Ctx()

    async def new_page(self, **_kw):
        number = page_number_for_call(self)
        self.taken.append(number)
        return SimpleNamespace(context=None, number=number)


@pytest.mark.unit
@pytest.mark.parametrize("route", ["browser.new_page", "context.new_page"])
def test_the_wrapper_reserves_before_the_egress_check_it_waits_for(route):
    """With a proxy, the wrapper checks the egress before a page, and only
    the first call of a burst actually waits for that check: the second one
    reached the client first and took the first call's number."""
    from invisible_playwright.async_api import InvisiblePlaywright

    ip = InvisiblePlaywright(seed=42)
    burst = {"armed": False, "checks": 0}

    async def check():
        # Due once per interval: the first check of the burst probes the
        # proxy, the next one finds it fresh and returns at once.
        if burst["armed"]:
            burst["checks"] += 1
            if burst["checks"] == 1:
                await asyncio.sleep(0.05)

    ip._assert_uscita_invariata = check
    browser = _Impl()
    ip._patch_new_context_defaults(browser)

    async def drive():
        if route == "browser.new_page":
            burst["armed"] = True
            await asyncio.gather(browser.new_page(), browser.new_page())
        else:
            ctx = await browser.new_context()
            burst["armed"] = True
            await asyncio.gather(ctx.new_page(), ctx.new_page())

    asyncio.run(drive())
    # The second call reached the implementation first; it still holds the
    # second number, because the number was taken when it was called.
    assert browser.taken == [1, 0]


# -- the server: it takes the number, and numbers popups apart ---------------

class _Engine(EventListeners):
    """Juggler as far as `Browser.newPage` goes: the target attaches BEFORE
    the answer (`TargetRegistry.newPage` waits for the target to exist), and
    each answer waits until the test releases it."""

    def __init__(self):
        super().__init__()
        self.browser = None
        self.arrived = threading.Semaphore(0)
        self.release = [threading.Event() for _ in range(8)]
        self._calls = itertools.count()

    def send(self, method, params=None, session=None, timeout=30):
        if method == "Browser.createBrowserContext":
            return {"browserContextId": "C1"}
        if method != "Browser.newPage":
            return {}
        call = next(self._calls)
        target = "T%d" % call
        self.attach(target, (params or {}).get("browserContextId"))
        self.arrived.release()
        assert self.release[call].wait(10)
        return {"targetId": target}

    def attach(self, target, context_id, opener=None):
        info = {"targetId": target, "type": "page",
                "browserContextId": context_id}
        if opener:
            info["openerId"] = opener
        self.browser._route_browser_event(
            "Browser.attachedToTarget",
            {"sessionId": "S-" + target, "targetInfo": info}, None)


class _Page:
    """The `PageDispatcher` seen from the numbering: what it was given."""

    def __init__(self, server, context, session, target_id, number,
                 opener=None):
        self.target_id = target_id
        self.number = number
        self.opener = opener
        self.popups_opened = 0
        self.channel = {"guid": "page-" + target_id}

    def announce_closed(self):
        pass


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setattr(srv, "PageDispatcher", _Page)
    e = _Engine()
    browser = BrowserDispatcher(Server(), None, e, "151.0", session_seed=1)
    e.browser = browser
    context = BrowserContextDispatcher(browser.server, browser, {}, "C1")
    browser.contexts.append(context)
    e.context = context
    return e


def _new_page_in_thread(engine, number, out):
    def run():
        out[number] = engine.context.op_new_page({"pageNumber": number})
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert engine.arrived.acquire(timeout=10)
    return t


def _page_of(engine, answer):
    guid = answer["page"]["guid"]
    return next(p for p in engine.browser._page_targets.values()
                if p.channel["guid"] == guid)


@pytest.mark.unit
def test_the_server_takes_the_number_the_request_carries(engine):
    """Known-bad, before: the server numbered pages in the order it built
    them, after the engine answered. Two requests in flight, answered
    backwards: each page keeps the number its request carried."""
    out = {}
    first = _new_page_in_thread(engine, 0, out)
    second = _new_page_in_thread(engine, 1, out)
    engine.release[1].set()
    second.join(10)
    engine.release[0].set()
    first.join(10)
    assert _page_of(engine, out[0]).number == 0
    assert _page_of(engine, out[1]).number == 1
    assert engine.browser._answered_numbers == {}
    assert engine.browser._unanswered == {}


@pytest.mark.unit
def test_a_request_without_a_number_is_refused(engine):
    with pytest.raises(srv.ProtocolException, match="pageNumber"):
        engine.context.op_new_page({})


@pytest.mark.unit
def test_a_popup_is_numbered_from_its_opener_and_shifts_no_new_page(engine):
    """Known-bad, before: a popup took the session's next number, so one
    landing while a `new_page()` was in flight pushed that page one further.
    Now the k-th popup of page N is `popup_number(N, k)` whenever it lands,
    and a page the site opened with no opener we know is a stray."""
    out = {}
    engine.release[0].set()
    _new_page_in_thread(engine, 0, out).join(10)
    opener = _page_of(engine, out[0])

    pending = _new_page_in_thread(engine, 1, out)
    engine.attach("P1", "C1", opener="T0")      # lands mid-request
    engine.attach("P2", "C1", opener="T0")
    engine.release[1].set()
    pending.join(10)
    engine.attach("X1", "C1")                   # no opener: a stray

    def number(target):
        for _ in range(200):
            page = engine.browser._page_targets.get(target)
            if page is not None:
                return page.number
            threading.Event().wait(0.01)
        raise AssertionError("target %s was never adopted" % target)

    assert _page_of(engine, out[1]).number == 1
    assert number("P1") == popup_number(0, 1)
    assert number("P2") == popup_number(0, 2)
    assert number("X1") == popup_number(None, 1)
    assert engine.browser._page_targets["P1"].opener is opener


@pytest.mark.unit
def test_popup_numbers_never_meet_the_numbers_of_asked_pages():
    numbers = {popup_number(o, k) for o in (None, 0, 1, 2) for k in range(1, 50)}
    assert len(numbers) == 4 * 49
    assert min(numbers) >= 1 << 62


# -- against a real engine ---------------------------------------------------

def _server_number(page):
    impl = page._impl_obj
    server = impl._connection._transport._server
    return server.object(impl._guid).actions.acts.page


def _client_number(page):
    return page._impl_obj._initializer.get("pageNumber")


OPENER = (b"<!doctype html><html><body><button id=o "
          b"onclick=\"window.open('/popup', '_blank')\">open</button>"
          b"</body></html>")


@pytest.fixture(scope="module")
def opener_url():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<!doctype html><p>popup" if
                             self.path.startswith("/popup") else OPENER)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % server.server_port
    server.shutdown()


#: ⛔ TWELVE ROUNDS, because the defect is a race: on main the first call of a
#: gather took number 1 in 4 runs out of 10, and a `new_page()` gathered with
#: a popup took the popup's number in 2 out of 10. Twelve rounds miss the
#: first with odds of 0.6**12 (0.2%).
ROUNDS = 12


def _gathered_new_pages(firefox_binary):
    from invisible_playwright.async_api import InvisiblePlaywright

    async def drive():
        seen = []
        async with InvisiblePlaywright(seed=106, binary_path=firefox_binary,
                                       headless=True) as browser:
            for _ in range(ROUNDS):
                pages = await asyncio.gather(browser.new_page(),
                                             browser.new_page())
                seen.extend((_server_number(p), _client_number(p))
                            for p in pages)
                for p in pages:
                    await p.close()
        return seen

    return asyncio.run(drive())


def _popups_beside_new_pages(firefox_binary, opener_url):
    from invisible_playwright.async_api import InvisiblePlaywright

    async def drive():
        fresh_pages, popups = [], []
        async with InvisiblePlaywright(seed=106, binary_path=firefox_binary,
                                       headless=True) as browser:
            ctx = await browser.new_context()
            opener = await ctx.new_page()
            await opener.goto(opener_url)
            for _ in range(ROUNDS):
                popped = asyncio.get_running_loop().create_future()
                opener.once("popup", lambda p: popped.set_result(p))
                _, fresh = await asyncio.gather(opener.click("#o"),
                                                ctx.new_page())
                popup = await asyncio.wait_for(popped, 15)
                fresh_pages.append((_server_number(fresh), _client_number(fresh)))
                popups.append((_server_number(popup), _client_number(popup)))
                await fresh.close()
                await popup.close()
            return _server_number(opener), fresh_pages, popups

    return asyncio.run(drive())


@pytest.mark.e2e
def test_gathered_pages_take_the_numbers_of_their_calls(firefox_binary):
    """Two `browser.new_page()` gathered, twelve times in one session: every
    page has the number of its call, in the server and in the client's
    initializer alike."""
    seen = _gathered_new_pages(firefox_binary)
    assert seen == [(n, n) for n in range(2 * ROUNDS)], (
        "pages asked for, (server, client), in call order: %s" % seen)


@pytest.mark.e2e
def test_a_popup_takes_no_number_of_a_page_asked_for(firefox_binary, opener_url):
    """A click that opens a popup gathered with a `new_page()`, twelve times
    in one session: each new page has the number of its call, and each popup
    the one its opener and its rank give it.

    ⛔ A SESSION OF ITS OWN, not the tail of the gathered one. After the 24
    pages of the test above, this loop stalled the engine at its seventh to
    tenth round (`Browser.newPage` or `Runtime.callFunction` unanswered for
    30 s) in 6 runs out of 12 with four browsers at once, on main as on this
    branch alike; in a fresh session, 0 out of 12."""
    opener_number, fresh_pages, popups = _popups_beside_new_pages(
        firefox_binary, opener_url)
    assert opener_number == 0
    assert fresh_pages == [(n, n) for n in range(1, ROUNDS + 1)], fresh_pages
    assert popups == [(popup_number(0, k),) * 2
                      for k in range(1, ROUNDS + 1)], popups
