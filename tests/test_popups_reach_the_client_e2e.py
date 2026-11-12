"""A popup reaches the client: the `page` event arrives, the new page is at its address and answers.

Why this file exists: the page dispatcher once waited for the engine's ready signal (`Page.ready`) before it handed a new
page over. A popup whose first request goes through a context route (the shape of every production client: abort the
images, continue the rest) was held behind a page that did not exist yet, the engine never said ready for it, the wait gave
up after 20 s and the popup vanished: the client never got the `page` event. That was found by a user journey (a
`target=_blank` link) and it is the same for a link and for `window.open`, to the page's own site and to another one.

The matrix: four ways to open a popup (a `target=_blank` link, the same link with `rel=noopener`, `window.open(url)` from a
click, `window.open(url, '_blank', 'noopener')`) x the target on the opener's site (`127.0.0.1`) or on another site
(`localhost`: another site for the engine, no pref needed) x with and without a context route x two transports: the client
of this wheel in the same process, and the stock Playwright client over `launch_server` (the way the fake web does it).
Each case: the `page` event arrives within 10 s, the page ends at the target address, answers an `evaluate`, and has an
opener (`page.opener()`). Stock Firefox (Playwright 1.62) gives an opener in all four kinds, `noopener` included, so this is
asserted for all four; if the engine ever returns None for a noopener kind the test says so.

One extra case proves that the other-site target is really in another process, so that the matrix cannot quietly become a
same-site test: opening it adds a content process below the test; the same-site target does not. That case has a browser
of its own that starts no content process by itself (`QUIET_PREFS`, test-only): a browser as shipped preallocates content
processes and refills its pool whenever it gets to it, and its preloaded New Tab page starts a process of its own and, in
some countries, one more for a Mozilla site, about a second later and with any page. Counted between two reads, any of
those was taken for the popup's doing. Where the system names a process by its site (Linux, `NAMED_BY_SITE`), the case
also asks by name: the other-site popup's process is the process of `http://localhost`. It runs on Windows too: there is
no skip, so the count stays the same on every system.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from _processes import NAMED_BY_SITE, site_pids

pytestmark = pytest.mark.e2e

OWN, OTHER = "127.0.0.1", "localhost"

#: The browser of the process case starts no content process by itself: no pool of preallocated processes, and no
#: preloaded New Tab page (which starts a process of its own and, in some countries, one for a Mozilla site). Test-only.
QUIET_PREFS = dict(NAMED_BY_SITE, **{"dom.ipc.processPrelaunch.fission.number": 0, "browser.newtab.preload": False})
KINDS = {
    "a-blank": "a1",
    "a-noopener": "a2",
    "open": "w1",
    "open-noopener": "w2",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path.startswith("/main"):
            body = ""
            for tag, host in (("s", OWN), ("o", OTHER)):
                url = "http://%s:%d/p" % (host, port)
                body += (
                    "<a id=a1%s target=_blank href='%s'>a</a> "
                    "<a id=a2%s target=_blank rel=noopener href='%s'>a</a> "
                    "<button id=w1%s onclick=\"window.open('%s')\">w</button> "
                    "<button id=w2%s onclick=\"window.open('%s','_blank','noopener')\">w</button><br>"
                ) % (tag, url, tag, url, tag, url, tag, url)
        else:
            body = "<p>popup</p>"
        data = ("<!doctype html><title>t</title>" + body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module", params=["in-process", "stock-client"])
def browser(request, firefox_binary):
    """One browser per transport for the whole file."""
    if request.param == "in-process":
        from invisible_playwright import InvisiblePlaywright

        with InvisiblePlaywright(binary_path=firefox_binary, headless=True) as b:
            yield b
        return
    try:     # the stock client (a pinned dev dependency), imported by name: the fork's guard forbids a static import
        sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    except ImportError as exc:
        pytest.fail("the stock Playwright client is missing (%s): install the wrapper with its dev extra" % exc,
                    pytrace=False)
    from invisible_playwright._juggler.remote import launch_server

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome")
    try:
        with sync_playwright() as p:
            b = p.firefox.connect(server.ws_endpoint)
            try:
                yield b
            finally:
                b.close()
    finally:
        server.close()


@pytest.fixture(params=["in-process", "stock-client"])
def quiet_browser(request, firefox_binary):
    """A browser of its own for the process case, on each transport, that starts no content process by itself."""
    if request.param == "in-process":
        from invisible_playwright import InvisiblePlaywright

        with InvisiblePlaywright(binary_path=firefox_binary, headless=True, extra_prefs=QUIET_PREFS) as b:
            yield b
        return
    try:
        sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    except ImportError as exc:
        pytest.fail("the stock Playwright client is missing (%s): install the wrapper with its dev extra" % exc,
                    pytrace=False)
    from invisible_playwright._juggler.remote import launch_server

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome",
                           extra_prefs=QUIET_PREFS)
    try:
        with sync_playwright() as p:
            b = p.firefox.connect(server.ws_endpoint)
            try:
                yield b
            finally:
                b.close()
    finally:
        server.close()


def _route(context):
    context.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media")
                  else route.continue_())


@pytest.mark.parametrize("routed", [False, True], ids=["no-route", "route"])
@pytest.mark.parametrize("target", ["same-site", "other-site"])
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_popup_reaches_the_client(browser, site, kind, target, routed):
    host, tag = (OWN, "s") if target == "same-site" else (OTHER, "o")
    context = browser.new_context()
    try:
        if routed:
            _route(context)
        page = context.new_page()
        page.goto("http://%s:%d/main" % (OWN, site))
        t0 = time.time()
        with context.expect_page(timeout=10000) as info:
            page.click("#%s%s" % (KINDS[kind], tag))
        popup = info.value
        arrived = time.time() - t0
        popup.wait_for_load_state("load", timeout=10000)
        assert popup.url == "http://%s:%d/p" % (host, site), "the popup is at %s" % popup.url
        assert popup.evaluate("document.title") == "t"
        assert popup.opener() is not None, "no opener (stock Firefox gives one for every kind)"
        assert arrived < 10
    finally:
        context.close()


def _content_processes(engine_dir):
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


def _quiet(engine_dir, seconds=3.0, limit=30.0):
    """The set of content processes once it has not changed for `seconds` (the engine keeps a spare one)."""
    end = time.time() + limit
    last, since = _content_processes(engine_dir), time.time()
    while time.time() < end:
        time.sleep(0.25)
        now = _content_processes(engine_dir)
        if now != last:
            last, since = now, time.time()
        elif time.time() - since >= seconds:
            break
    return last


def test_the_other_site_popup_runs_in_another_process(quiet_browser, firefox_binary, site):
    """The control of the matrix above: the popup to `localhost` adds a content process, the one to `127.0.0.1` does not."""
    import psutil

    engine_dir = str(Path(firefox_binary).resolve().parent)
    context = quiet_browser.new_context()
    page = context.new_page()
    page.goto("http://%s:%d/main" % (OWN, site))
    before = _quiet(engine_dir)
    with context.expect_page(timeout=10000) as info:
        page.click("#a1s")
    info.value.wait_for_load_state("load")
    same = _quiet(engine_dir)
    info.value.close()
    page.goto("http://%s:%d/main" % (OWN, site))
    base = _quiet(engine_dir)
    with context.expect_page(timeout=10000) as info:
        page.click("#a1o")
    info.value.wait_for_load_state("load")
    other = _quiet(engine_dir)
    assert not (same - before), "a popup to the opener's own site started a process: %s" % sorted(same - before)
    assert len(other - base) >= 1, "the other-site popup added no content process: %s -> %s" % (
        sorted(base), sorted(other))
    if sys.platform.startswith("linux"):      # only Linux names a process by its site; elsewhere the count above is the proof
        children = psutil.Process(os.getpid()).children(recursive=True)
        assert site_pids(children, "http://" + OTHER, engine_dir) & (other - base), (
            "none of the new processes is the process of %s: %s" % (OTHER, sorted(other - base)))
