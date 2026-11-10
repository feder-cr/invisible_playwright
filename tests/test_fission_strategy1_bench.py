"""The bench for firefox-62 (Fission, strategy 1): a page whose frames live in different processes.

Strategy 1 is `fission.webContentIsolationStrategy = 1` (IsolateEverything: one remote type per site) with
`fission.autostart` on. The wrapper still launches with strategy 0 (`_BASELINE`): the prefs below are an
overlay for this file only, so the product stays green while this bench is red.

Hosts `a.test`, `b.test`, `c.test` resolve to one local server through `network.dns.localDomains`, on one
port: two hosts are two SITES, so a frame of `b.test` inside a page of `a.test` is out of process, and the
`a.test` frame inside that one (a -> b -> a) is a fragment root of its own, in the process of the top.
`localhost` and `127.0.0.1` would not do for this: `127.0.0.1:P1` and `127.0.0.1:P2` are one site.

Tests 1 and 2 hold the bench itself (the sites ARE separated; the same site is NOT): green today. Every other
test says what the product does not do yet and the step that makes it green:

    3  frame list with real urls              step 3 (navigations)
    4  evaluate in the out-of-process frame   steps 2a + 1a + 1b
    5  a -> b -> a parent chain               steps 2a + 1b
    6  content_frame() of the iframe element  step 4
    7  click by selector in the leaf          steps 5 + 6
    8  init script reaches the frame first    step 1a
    9  named world after the OOP frame        step 1a + 2b
    10 console of the OOP frame               step 1a

The oracle for "another process" is the engine's own processes below the test (`-contentproc ... tab`): a
cross-site frame adds at least one, a same-site one adds none. The count is read after the browser is quiet
(the engine pre-allocates a content process and replaces it when it is used).
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from _servers import LocalServer

FISSION_ON = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,   # IsolateEverything: site-origin
    "dom.ipc.processCount.webIsolated": 4,      # the desktop default
    "network.dns.localDomains": "a.test,b.test,c.test",
}


@pytest.fixture
def cross_site_loop():
    """a.test/top -> b.test/mid -> a.test/leaf, plus a same-site frame page and the reports the pages send."""
    reports = []
    routes = {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            host = (self.headers.get("Host") or "").split(":")[0]
            if url.path.startswith("/report"):
                reports.append({"host": host, "path": url.path, "query": parse_qs(url.query)})
                body, ctype = b"ok", "text/plain"
            elif url.path in routes:
                body, ctype = routes[url.path](host).encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = LocalServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def at(host, path):
        return "http://%s:%d%s" % (host, port, path)

    # What a page reports: where it is, and the order of the scripts that ran in it (an init script pushes
    # 'init' to window.__o; the page pushes 'page').
    report = ("<script>(window.__o = window.__o || []).push('page');"
              "console.log('hello from ' + location.pathname + ' on ' + location.host);"
              "fetch('/report/loaded?path=' + location.pathname + '&order=' + encodeURIComponent(JSON.stringify(window.__o)))"
              "</script>")
    routes["/top"] = lambda host: ("<!doctype html><title>top</title>"
                                   '<iframe id="b" src="%s" width="600" height="400"></iframe>' % at("b.test", "/mid") + report)
    routes["/mid"] = lambda host: ("<!doctype html><title>mid</title>"
                                   '<iframe id="a" src="%s" width="500" height="300"></iframe>' % at("a.test", "/leaf") + report)
    routes["/leaf"] = lambda host: ("<!doctype html><title>leaf</title><button id=\"ok\" style=\"width:120px;height:40px\">ok</button>"
                                    "<script>ok.addEventListener('click', e => fetch('/report/clicked?isTrusted=' + e.isTrusted))</script>" + report)
    routes["/same-site"] = lambda host: ("<!doctype html><title>same-site</title>"
                                         '<iframe id="s" src="%s" width="300" height="100"></iframe>' % at("a.test", "/leaf"))
    routes["/plain"] = lambda host: "<!doctype html><title>plain</title><p>plain"
    try:
        yield {"top": at("a.test", "/top"), "mid": at("b.test", "/mid"), "leaf": at("a.test", "/leaf"),
               "same_site": at("a.test", "/same-site"), "plain": at("a.test", "/plain"),
               "reports": reports, "origin_mid": "http://b.test:%d" % port, "origin_top": "http://a.test:%d" % port}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def engine_dir(firefox_binary):
    return str(Path(firefox_binary).resolve().parent)


def content_processes(engine_dir):
    """The pids of the engine's web content processes (`-contentproc ... tab`) below this test."""
    import psutil

    pids = set()
    for child in psutil.Process(os.getpid()).children(recursive=True):
        try:
            if not os.path.normcase(child.exe()).startswith(os.path.normcase(engine_dir)):
                continue
            cmd = child.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd and cmd[-1] == "tab":
            pids.add(child.pid)
    return pids


def quiet(engine_dir, seconds=3.0, limit=30.0):
    """The set of content processes once it has not changed for `seconds`."""
    end = time.time() + limit
    last, since = content_processes(engine_dir), time.time()
    while time.time() < end:
        time.sleep(0.25)
        now = content_processes(engine_dir)
        if now != last:
            last, since = now, time.time()
        elif time.time() - since >= seconds:
            break
    return last


@pytest.fixture
def browser(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True,
                             extra_prefs=FISSION_ON) as browser:
        yield browser


@pytest.fixture
def page(browser):
    page = browser.new_context().new_page()
    page.set_default_timeout(3000)
    page.set_default_navigation_timeout(20000)
    return page


def wait_for(page, predicate, seconds=8.0):
    """Until `predicate()`, with the client's events being read: the sync client takes the engine's events
    only inside its own calls, so a plain sleep would never see a frame arrive."""
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        page.wait_for_timeout(100)
    return predicate()


@pytest.fixture
def events(monkeypatch):
    """Every protocol event the client receives, in order (the client's own view of the page)."""
    from invisible_core.juggler.connection import EventListeners

    seen = []
    dispatch = EventListeners.dispatch_event

    def record(self, method, params, session):
        seen.append({"method": method, "params": params})
        return dispatch(self, method, params, session)

    monkeypatch.setattr(EventListeners, "dispatch_event", record)
    return seen


def frame_with_origin(page, origin):
    for frame in page.frames:
        if frame.url.startswith(origin):
            return frame
    return None


# ── 1, 2: the bench itself ─────────────────────────────────────────────────────

@pytest.mark.e2e
def test_cross_site_iframe_gets_its_own_process(page, cross_site_loop, engine_dir):
    """Green today: it says the bench separates sites. Two sites in the frame tree add at least one process."""
    page.goto(cross_site_loop["plain"])
    before = quiet(engine_dir)
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: any(r["path"] == "/report/loaded" and r["host"] == "a.test" and
                                r["query"]["path"] == ["/leaf"] for r in cross_site_loop["reports"]))
    after = quiet(engine_dir)
    assert len(after - before) >= 1, "no new content process: before %s, after %s" % (sorted(before), sorted(after))


@pytest.mark.e2e
def test_same_site_other_port_stays_in_process(page, cross_site_loop, engine_dir):
    """Green today, the control: a frame of the page's own site adds no content process."""
    page.goto(cross_site_loop["plain"])
    before = quiet(engine_dir)
    page.goto(cross_site_loop["same_site"], wait_until="load")
    assert wait_for(page, lambda: any(r["path"] == "/report/loaded" for r in cross_site_loop["reports"]))
    after = quiet(engine_dir)
    assert not (after - before), "a same-site frame started a process: %s" % sorted(after - before)


# ── 3 .. 10: what the product does not do yet ──────────────────────────────────

@pytest.mark.e2e
def test_frame_list_has_real_urls(page, cross_site_loop):
    """Red until step 3: the frame of another process is listed with an empty url (a placeholder)."""
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 3)
    urls = sorted(frame.url for frame in page.frames)
    assert urls == sorted([cross_site_loop["top"], cross_site_loop["mid"], cross_site_loop["leaf"]])


@pytest.mark.e2e
def test_frame_evaluate_in_oop_iframe(page, cross_site_loop):
    """Red until steps 2a + 1a + 1b: no execution context exists for a frame of another process."""
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 2)
    assert len(page.frames) >= 2
    mid = page.frames[1]
    assert mid.evaluate("location.origin") == cross_site_loop["origin_mid"]


@pytest.mark.e2e
def test_nested_a_b_a_parent_chain(page, cross_site_loop):
    """Red until steps 2a + 1b: a placeholder has no parent chain."""
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 3)
    top = page.main_frame
    mid = next((f for f in page.frames if f.url == cross_site_loop["mid"]), None)
    leaf = next((f for f in page.frames if f.url == cross_site_loop["leaf"]), None)
    assert mid is not None and leaf is not None, [f.url for f in page.frames]
    assert leaf.parent_frame == mid
    assert mid.parent_frame == top


@pytest.mark.e2e
def test_content_frame_resolves(page, cross_site_loop):
    """Red until step 4: the element of an iframe whose document is in another process has no frame."""
    page.goto(cross_site_loop["top"], wait_until="load")
    owner = page.query_selector("#b")
    try:
        frame = owner.content_frame()
    finally:
        owner.dispose()
    assert frame is not None
    assert frame.url == cross_site_loop["mid"]


@pytest.mark.e2e
def test_click_in_oop_iframe_by_selector(page, cross_site_loop):
    """Red until steps 5 + 6: a trusted click reaches the leaf, two frames down, in the process of the top."""
    page.goto(cross_site_loop["top"], wait_until="load")
    page.frame_locator("#b").frame_locator("#a").locator("#ok").click()
    assert wait_for(page, lambda: any(r["path"] == "/report/clicked" and r["query"]["isTrusted"] == ["true"]
                                for r in cross_site_loop["reports"]))


@pytest.mark.e2e
def test_init_script_reaches_oop_frame_first(page, cross_site_loop):
    """Red until step 1a: an init script runs in the frame of another process before the page's own scripts."""
    page.add_init_script("(window.__o = window.__o || []).push('init')")
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: len([r for r in cross_site_loop["reports"] if r["path"] == "/report/loaded"]) >= 3)
    orders = {r["query"]["path"][0]: json.loads(r["query"]["order"][0])
              for r in cross_site_loop["reports"] if r["path"] == "/report/loaded"}
    assert orders == {"/top": ["init", "page"], "/mid": ["init", "page"], "/leaf": ["init", "page"]}


@pytest.mark.e2e
def test_named_world_declared_after_oop_iframe(page, cross_site_loop, events):
    """Red until steps 1a + 2b: the utility world the client declares exists in every frame, the other
    process's included (today the declaration to a frame without a docShell is lost without a word)."""
    from invisible_core.juggler.injected import UTILITY_WORLD

    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 3)
    page.wait_for_timeout(1000)
    created = [e["params"] for e in events if e["method"] == "Runtime.executionContextCreated"]
    default = {c["auxData"]["frameId"] for c in created if not c["auxData"].get("name")}
    utility = {c["auxData"]["frameId"] for c in created if c["auxData"].get("name") == UTILITY_WORLD}
    assert len(default) >= 3, "frames with a default context: %d" % len(default)
    assert default <= utility, "frames without a utility context: %d" % len(default - utility)
    # The contexts above can be those of the frame's first about:blank, which no one ever destroys: what
    # counts is a read of the frame's CURRENT document through them.
    mid = next((f for f in page.frames if f.url == cross_site_loop["mid"]), None)
    assert mid is not None, [f.url for f in page.frames]
    assert mid.locator("#a").count() == 1


@pytest.mark.e2e
def test_console_from_oop_frame_reaches_client(page, cross_site_loop):
    """Red until step 1a: a console message of a frame of another process arrives."""
    messages = []
    page.on("console", lambda message: messages.append(message.text))
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: any("/mid on b.test" in text for text in messages)), messages


# ── what step 1 adds to the bench ──────────────────────────────────────────────

@pytest.mark.e2e
def test_a_context_of_the_top_survives_an_oop_frame_coming_and_going(page, cross_site_loop):
    """A guard, green on every engine: the `cleared` of one process must not take the contexts of another with it.
    A handle held on the top is read after a frame of another process has come and gone."""
    page.goto(cross_site_loop["plain"], wait_until="load")
    body = page.evaluate_handle("document.body")
    page.evaluate("""url => new Promise(resolve => {
        const f = document.createElement('iframe'); f.id = 'x'; f.src = url; f.onload = resolve; document.body.appendChild(f);
    })""", cross_site_loop["mid"])
    wait_for(page, lambda: len(page.frames) >= 2)
    assert body.evaluate("b => b.tagName") == "BODY"
    page.evaluate("document.getElementById('x').remove()")
    assert body.evaluate("b => b.tagName") == "BODY"


@pytest.mark.e2e
def test_oop_frame_still_answers_after_the_top_comes_back_from_the_cache(page, cross_site_loop):
    """Red until the frames of other processes are announced again for a document restored from the back-forward
    cache: the content process no longer announces them (step 1b), so the parent's FrameLifecycle must, when the
    page handler forgets the frames below the restored document (PageHandler._forgetFramesBelow)."""
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 2)
    page.goto(cross_site_loop["plain"], wait_until="load")
    page.go_back(wait_until="load")
    assert wait_for(page, lambda: any(f.url == cross_site_loop["mid"] for f in page.frames))
    mid = next(f for f in page.frames if f.url == cross_site_loop["mid"])
    assert mid.evaluate("1") == 1


@pytest.mark.e2e
def test_removed_oop_frame_leaves_the_frame_list_and_the_page_answers(page, cross_site_loop):
    """Red until PageHandler listens to the FrameLifecycle through helper.on: its own emitter calls a listener with
    the event name first, so `{frameId}` was read from a string and the frame was never detached. The frame of
    another process that is removed from the DOM must leave `page.frames`, and the commands that go to every
    process (init scripts, extra headers) must not wait for the one that is gone."""
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: len(page.frames) >= 3)
    page.evaluate("document.getElementById('b').remove()")
    assert wait_for(page, lambda: len(page.frames) == 1), [f.url for f in page.frames]
    page.add_init_script("window.__after = 1")
    page.set_extra_http_headers({"x-bench": "1"})
    page.goto(cross_site_loop["plain"], wait_until="load")
    assert page.evaluate("window.__after") == 1
