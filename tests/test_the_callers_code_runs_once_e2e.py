"""The caller's code runs once when its page navigates while it waits [B285].

A script waiting on a promise when its page navigated was refused by the new
document in the same words as one that never ran (Juggler sends the waiting
message again), and from core 41.36.0 `page.evaluate` sent it again into the
new page: a script with an effect ran twice. Playwright raises "Execution
context was destroyed, most likely because of a navigation" and never sends it
again; so does this, through `evaluate` and `eval_on_selector_all`.
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

FIRST = b"<!doctype html><html><head><title>first</title></head><body><p>a</p></body></html>"
NEXT = b"<!doctype html><html><head><title>next</title></head><body>next</body></html>"


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = NEXT if self.path.startswith("/next") else FIRST
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture(scope="module")
def page(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4322, binary_path=firefox_binary,
                             headless=True) as browser:
        yield browser.new_page()


def _settled_on_next(page):
    deadline = time.monotonic() + 20
    while page.title() != "next":
        assert time.monotonic() < deadline, "the page never reached /next"
        time.sleep(0.05)
    time.sleep(0.5)  # a second run would have navigated and counted again


def test_evaluate_interrupted_by_its_navigation_runs_once(page, site):
    from invisible_playwright.sync_api import Error
    page.goto(site)
    page.evaluate("localStorage.clear()")
    with pytest.raises(Error, match="Execution context was destroyed"):
        page.evaluate(
            "(() => { localStorage.runs = String(Number(localStorage.runs || 0) + 1);"
            " setTimeout(() => { location.href = '/next'; }, 100);"
            " return new Promise(() => {}); })()")
    _settled_on_next(page)
    assert page.evaluate("localStorage.runs") == "1"


def test_eval_on_selector_all_interrupted_by_its_navigation_runs_once(page, site):
    from invisible_playwright.sync_api import Error
    page.goto(site)
    page.evaluate("localStorage.clear()")
    with pytest.raises(Error, match="Execution context was destroyed"):
        page.eval_on_selector_all(
            "p",
            "async els => { localStorage.runs = String(Number(localStorage.runs || 0) + 1);"
            " setTimeout(() => { location.href = '/next'; }, 100);"
            " await new Promise(() => {}); }")
    _settled_on_next(page)
    assert page.evaluate("localStorage.runs") == "1"


def test_a_read_declared_free_of_effects_answers_from_the_page_that_replaced_it(page, site):
    """`evaluate_read` is the fork's door for a library's fixed read (the
    MCP's snapshot): interrupted by a navigation, it is made again on the new
    page, where `evaluate` raises. The first run here starts the navigation and
    waits forever; on /next the same expression answers."""
    page.goto(site)
    title = page.evaluate_read(
        "(async () => { if (location.pathname !== '/next') {"
        " setTimeout(() => { location.href = '/next'; }, 100);"
        " await new Promise(() => {}); }"
        " return document.title; })()")
    assert title == "next"


def test_wait_for_function_goes_on_through_a_navigation(page, site):
    page.goto(site)
    page.evaluate("setTimeout(() => { location.href = '/next'; }, 300)")
    page.wait_for_function("document.title === 'next'", timeout=15_000)
    assert page.title() == "next"
