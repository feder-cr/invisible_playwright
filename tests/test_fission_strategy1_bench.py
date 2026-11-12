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
    # an iframe nested past the depth Firefox allows (nsFrameLoader::DepthTooGreat, 10), all of one origin
    for n in range(0, 14):
        routes["/deep%d" % n] = (lambda n: lambda host: "<!doctype html><title>deep%d</title><iframe id=\"f\" src=\"%s\"></iframe>" % (n, at(host, "/deep%d" % (n + 1))))(n)
    # a page that reports each time it is shown, and whether it came out of the back-forward cache
    routes["/shown"] = lambda host: ("<!doctype html><title>shown</title><script>addEventListener('pageshow', e => "
                                     "fetch('/report/pageshow?persisted=' + e.persisted + '&top=' + (window === top)))</script>")
    routes["/top-shown"] = lambda host: ('<!doctype html><title>top-shown</title><iframe id="b" src="%s"></iframe>' % at("b.test", "/shown"))
    # a page and one iframe of another site, named, with nothing else in it
    routes["/one-iframe"] = lambda host: ('<!doctype html><title>one</title><iframe id="f" name="f" src="%s"></iframe>' % at("b.test", "/plain"))
    routes["/plain"] = lambda host: "<!doctype html><title>plain</title><p>plain"
    try:
        yield {"top": at("a.test", "/top"), "one_iframe": at("a.test", "/one-iframe"), "deep": at("a.test", "/deep0"), "top_shown": at("a.test", "/top-shown"), "mid": at("b.test", "/mid"), "leaf": at("a.test", "/leaf"),
               "same_site": at("a.test", "/same-site"), "plain": at("a.test", "/plain"),
               "reports": reports, "origin_mid": "http://b.test:%d" % port, "origin_top": "http://a.test:%d" % port}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def engine_dir(firefox_binary, browser):
    engine = Engine(str(Path(firefox_binary).resolve().parent))
    engine.root_pid = browser._test_root_pid
    return engine


#: The engine keeps spare content processes (dom.ipc.processPrelaunch: three with Fission) and starts a new one whenever one is
#: taken, at a moment that depends on the machine (stock Firefox does the same). This test asks whether a same-site frame started a
#: process, and nothing the client can read says which process hosts a frame: no protocol field carries a process id (the Juggler
#: protocol, juggler/protocol/Protocol.js, has none; the only process the engine names is a context's, TargetRegistry.js
#: `_processesOfContext`), so the processes are counted, and a spare that appears a second after the page would be counted too.
#: With the spares off every process counted was started for a page. (Measured on the Linux runner, 20 runs each: the
#: service-worker test counted 5 instead of 2 in 7 runs with the spares on, 0 with them off; this test passed 20 of 20 alone and
#: failed in the full suite, on a loaded runner.) The service-worker test keeps the spares on, as shipped.
NO_SPARE_PROCESSES = {"dom.ipc.processPrelaunch.enabled": False}


class Engine(str):
    """The engine's directory, and the pid of the main process of the browser THIS test launched (`root_pid`)."""

    root_pid = None


def engine_main_processes(engine_dir):
    """The pids of the engine's main processes (not `-contentproc`) below this test process."""
    import psutil

    found = set()
    for child in psutil.Process(os.getpid()).children(recursive=True):
        try:
            if os.path.normcase(child.exe()).startswith(os.path.normcase(str(engine_dir))) and "-contentproc" not in child.cmdline():
                found.add(child.pid)
        except psutil.Error:
            continue
    return found


def content_processes(engine_dir):
    """The pids of the web content processes (`-contentproc ... tab`) of THIS test's browser.

    Of that browser and no other: the pytest process of a worker may still hold the browsers of the tests it ran before (one
    that is closing, one a fixture keeps), and a process one of THEM starts is not a process this test's frame started.
    Counting everything below the pytest process, as this did, made a same-site frame "start a process" in the full suite
    and never alone."""
    import psutil

    root = getattr(engine_dir, "root_pid", None)
    try:
        below = (psutil.Process(root) if root else psutil.Process(os.getpid())).children(recursive=True)
    except psutil.Error:
        return set()
    pids = set()
    for child in below:
        try:
            if not os.path.normcase(child.exe()).startswith(os.path.normcase(str(engine_dir))):
                continue
            cmd = child.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd and cmd[-1] == "tab":
            pids.add(child.pid)
    return pids


def describe(pids):
    """What the new processes are, for the message of an assertion (a failure that cannot be explained is not a result)."""
    import psutil

    out = []
    for pid in sorted(pids):
        try:
            p = psutil.Process(pid)
            out.append("%d parent=%d age=%.1fs cmd=%s" % (pid, p.ppid(), time.time() - p.create_time(), " ".join(p.cmdline())[-120:]))
        except psutil.Error:
            out.append("%d (gone)" % pid)
    return out


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

    directory = str(Path(firefox_binary).resolve().parent)
    before = engine_main_processes(directory)
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True,
                             extra_prefs={**FISSION_ON, **NO_SPARE_PROCESSES}) as browser:
        import psutil

        mine = engine_main_processes(directory) - before
        # the topmost one if the launcher keeps a process above the browser's own
        tops = [pid for pid in mine if psutil.Process(pid).ppid() not in mine]
        assert len(tops) == 1, "the main process of this test's browser: %s" % sorted(mine)
        browser._test_root_pid = tops[0]
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
    assert not (after - before), "a same-site frame started a process: %s" % describe(after - before)


# ── 3 .. 10: what the product does not do yet ──────────────────────────────────

@pytest.mark.e2e
def test_frame_list_has_real_urls(page, cross_site_loop, engine_at_least):
    """Red until step 3: the frame of another process is listed with an empty url (a placeholder)."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 3)
    urls = sorted(frame.url for frame in page.frames)
    assert urls == sorted([cross_site_loop["top"], cross_site_loop["mid"], cross_site_loop["leaf"]])


@pytest.mark.e2e
def test_frame_evaluate_in_oop_iframe(page, cross_site_loop, engine_at_least):
    """Red until steps 2a + 1a + 1b: no execution context exists for a frame of another process."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 2)
    assert len(page.frames) >= 2
    mid = page.frames[1]
    assert mid.evaluate("location.origin") == cross_site_loop["origin_mid"]


@pytest.mark.e2e
def test_nested_a_b_a_parent_chain(page, cross_site_loop, engine_at_least):
    """Red until steps 2a + 1b: a placeholder has no parent chain."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    wait_for(page, lambda: len(page.frames) >= 3)
    top = page.main_frame
    mid = next((f for f in page.frames if f.url == cross_site_loop["mid"]), None)
    leaf = next((f for f in page.frames if f.url == cross_site_loop["leaf"]), None)
    assert mid is not None and leaf is not None, [f.url for f in page.frames]
    assert leaf.parent_frame == mid
    assert mid.parent_frame == top


@pytest.mark.e2e
def test_content_frame_resolves(page, cross_site_loop, engine_at_least):
    """Red until step 4: the element of an iframe whose document is in another process has no frame."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    owner = page.query_selector("#b")
    try:
        frame = owner.content_frame()
    finally:
        owner.dispose()
    assert frame is not None
    assert frame.url == cross_site_loop["mid"]


@pytest.mark.e2e
def test_click_in_oop_iframe_by_selector(page, cross_site_loop, engine_at_least):
    """Red until steps 5 + 6: a trusted click reaches the leaf, two frames down, in the process of the top."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    page.frame_locator("#b").frame_locator("#a").locator("#ok").click()
    assert wait_for(page, lambda: any(r["path"] == "/report/clicked" and r["query"]["isTrusted"] == ["true"]
                                for r in cross_site_loop["reports"]))


@pytest.mark.e2e
def test_init_script_reaches_oop_frame_first(page, cross_site_loop, engine_at_least):
    """Red until step 1a: an init script runs in the frame of another process before the page's own scripts."""
    engine_at_least("firefox-62")
    page.add_init_script("(window.__o = window.__o || []).push('init')")
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: len([r for r in cross_site_loop["reports"] if r["path"] == "/report/loaded"]) >= 3)
    orders = {r["query"]["path"][0]: json.loads(r["query"]["order"][0])
              for r in cross_site_loop["reports"] if r["path"] == "/report/loaded"}
    assert orders == {"/top": ["init", "page"], "/mid": ["init", "page"], "/leaf": ["init", "page"]}


@pytest.mark.e2e
def test_named_world_declared_after_oop_iframe(page, cross_site_loop, events, engine_at_least):
    """Red until steps 1a + 2b: the utility world the client declares exists in every frame, the other
    process's included (today the declaration to a frame without a docShell is lost without a word)."""
    engine_at_least("firefox-62")
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
def test_console_from_oop_frame_reaches_client(page, cross_site_loop, engine_at_least):
    """Red until step 1a: a console message of a frame of another process arrives."""
    engine_at_least("firefox-62")
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
def test_oop_frames_are_listed_again_after_the_top_comes_back_from_the_cache(page, cross_site_loop):
    """Red until FrameLifecycle announces again the frames below a document restored from the back-forward cache:
    the content process no longer announces a frame of another process (step 1b), and the client forgets the
    frames below a frame when a document commits in it."""
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: len(page.frames) >= 3)
    page.goto(cross_site_loop["plain"], wait_until="load")
    page.go_back(wait_until="load")
    assert wait_for(page, lambda: len(page.frames) >= 3), [f.url for f in page.frames]


@pytest.mark.e2e
def test_oop_frame_has_its_url_and_answers_after_the_top_comes_back_from_the_cache(page, cross_site_loop, engine_at_least):
    """Red until steps 2b + 3: the restored frames come back with their url (the parent's registry of navigations)
    and with their execution contexts (announced again by the mux)."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: len(page.frames) >= 3)
    page.goto(cross_site_loop["plain"], wait_until="load")
    page.go_back(wait_until="load")
    assert wait_for(page, lambda: any(f.url == cross_site_loop["mid"] for f in page.frames))
    mid = next(f for f in page.frames if f.url == cross_site_loop["mid"])
    assert mid.evaluate("1") == 1


@pytest.mark.e2e
def test_removed_oop_frame_leaves_the_frame_list_and_the_page_answers(page, cross_site_loop, engine_at_least):
    """Red until PageHandler listens to the FrameLifecycle through helper.on: its own emitter calls a listener with
    the event name first, so `{frameId}` was read from a string and the frame was never detached. The frame of
    another process that is removed from the DOM must leave `page.frames`, and the commands that go to every
    process (init scripts) must not wait for the one that is gone."""
    engine_at_least("firefox-62")
    page.goto(cross_site_loop["top"], wait_until="load")
    assert wait_for(page, lambda: len(page.frames) >= 3)
    page.evaluate("document.getElementById('b').remove()")
    assert wait_for(page, lambda: len(page.frames) == 1), [f.url for f in page.frames]
    page.add_init_script("window.__after = 1")
    page.goto(cross_site_loop["plain"], wait_until="load")
    assert page.evaluate("window.__after") == 1


@pytest.mark.e2e
def test_content_frame_of_an_iframe_nested_too_deep_is_none(page, cross_site_loop):
    """Red until step 8: past the depth Firefox allows (10) an <iframe> has a browsing context that loads
    nothing and `element.browsingContext` does not apply the depth check, so the element named a frame with no
    document. `contentWindow` is null there, and the client expects no content frame."""
    page.goto(cross_site_loop["deep"], wait_until="load")
    assert wait_for(page, lambda: len(page.frames) >= 10)
    page.wait_for_timeout(1500)
    last = page.frames[-1]
    owner = last.frame_element()
    try:
        assert owner.content_frame() is None
    finally:
        owner.dispose()


@pytest.mark.e2e
def test_oop_frame_comes_out_of_the_back_forward_cache_with_the_top(page, cross_site_loop):
    """What the engine does, not the product: the document of a frame in another process is shown again, persisted,
    when the top comes back from the cache (step 8.3 relies on it)."""
    page.goto(cross_site_loop["top_shown"], wait_until="load")
    assert wait_for(page, lambda: any(r["path"] == "/report/pageshow" for r in cross_site_loop["reports"]))
    page.goto(cross_site_loop["plain"], wait_until="load")
    page.go_back(wait_until="load")
    assert wait_for(page, lambda: any(r["path"] == "/report/pageshow" and r["query"]["persisted"] == ["true"] and
                                      r["query"]["top"] == ["false"] for r in cross_site_loop["reports"])),         cross_site_loop["reports"]


@pytest.mark.e2e
def test_the_frames_have_their_urls_when_goto_returns(browser, cross_site_loop, engine_at_least):
    """Red until the load of a frame waits for the commit of the frames below it: the embedder's process says `load` of the
    top while the commit of the iframe of another site waits for the name of its <iframe> (asked of that same process), and
    the frames read right after `goto` had an empty url 3 times in 20 (at 50 runs the chance of seeing it is 1 - 0.85^50). Nothing here waits: the frames are read at once."""
    engine_at_least("firefox-62")
    empty = []
    for i in range(50):
        page = browser.new_context().new_page()
        page.goto(cross_site_loop["one_iframe"] + "?i=%d" % i, wait_until="load")
        urls = [frame.url for frame in page.frames]
        if "" in urls or len(urls) < 2:
            empty.append((i, urls))
        page.context.close()
    assert not empty, "frames with no url right after goto, in %d of 50 runs: %s" % (len(empty), empty[:3])
