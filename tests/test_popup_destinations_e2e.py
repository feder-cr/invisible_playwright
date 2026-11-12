"""What a popup does depends on what its destination answers; the client must see what stock Firefox makes it see.

Reference: Playwright 1.62 with its own Firefox, measured (a `window.open(url)` from a click, and a clicked
`<a target=_blank>`, destination on the opener's site, two repetitions each, 30 s observed unless said; the destination on
another site gave the same):

  * 204 No Content: NO `page` event at all (observed for 30 s). The popup is never announced.
  * a destination that never answers: NO `page` event (observed for 30 s).
  * a destination that answers a normal HTML page after N seconds: the `page` event arrives when the answer comes, N s after
    the click, with the destination as its url, and the load follows at once. There is no ceiling: with N = 25 the event
    came at 25.1 s. This file runs both: 4 s for the two ways of opening, and 25 s once per transport, because a wait
    that gives up on a popup after 20 s passes the 4 s case and loses the 25 s one.
  * `Content-Disposition: attachment`: the `page` event arrives at once (url empty), a `download` event arrives on the
    OPENER (not on the popup) and the popup closes within about 10 ms.

Why it matters here: the page dispatcher announces a popup when the engine says it is ready (`Page.ready`), and gives up on
one that never does after 20 s. A destination that answers late must still be announced when it answers, one that never
becomes a page must not be announced, and a download started by a popup must reach the client on the opener like on stock.
The waits for "no event" are bounded to 8 s here (stock was observed for 30 s).

Transports: the client of this wheel in the same process, and the stock Playwright client over `launch_server`.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_popups_reach_the_client_e2e import browser  # noqa: F401  (the two-transport fixture of the sibling file)

pytestmark = pytest.mark.e2e

SLOW_SECONDS = 4
VERY_SLOW_SECONDS = 25      # past the 20 s after which a popup was once given up on
NO_EVENT_SECONDS = 8


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body=b"", extra=()):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        port = self.server.server_address[1]
        try:
            if self.path.startswith("/main"):
                body = ""
                for d in "12345":
                    url = "http://127.0.0.1:%d/d%s" % (port, d)
                    body += ("<a id=link%s target=_blank href='%s'>a</a> "
                             "<button id=open%s onclick=\"window.open('%s')\">w</button><br>") % (d, url, d, url)
                self._send(200, "text/html", ("<!doctype html><title>opener</title>" + body).encode())
            elif self.path.startswith("/d1"):
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path.startswith("/d2"):
                time.sleep(SLOW_SECONDS)
                self._send(200, "text/html", b"<!doctype html><title>slow</title><p>slow page</p>")
            elif self.path.startswith("/d3"):
                time.sleep(NO_EVENT_SECONDS + 6)      # longer than any wait of a test: it never answers in time
                self._send(200, "text/html", b"<!doctype html><title>late</title>")
            elif self.path.startswith("/d4"):
                self._send(200, "text/plain", b"hello attachment\n",
                           [("Content-Disposition", 'attachment; filename="a.txt"')])
            elif self.path.startswith("/d5"):
                time.sleep(VERY_SLOW_SECONDS)
                self._send(200, "text/html", b"<!doctype html><title>very slow</title><p>very slow page</p>")
            else:
                self._send(404, "text/plain", b"not found")
        except (ConnectionError, TimeoutError, OSError):
            self.close_connection = True


class _Server(ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture(scope="module")
def site():
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


class _Run:
    """One popup attempt: the opener page, what the context announced, what the opener downloaded."""

    def __init__(self, launched, port, how, dest):
        self.context = launched.new_context(accept_downloads=True)
        self.opener = self.context.new_page()
        self.popups, self.downloads, self.arrived_at = [], [], None
        self.context.on("page", self._on_page)
        self.opener.on("download", lambda download: self.downloads.append(download))
        self.opener.goto("http://127.0.0.1:%d/main" % port)
        self.clicked = time.time()
        self.opener.click("#%s%s" % (how, dest))

    def _on_page(self, page):
        self.popups.append(page)
        self.arrived_at = time.time() - self.clicked

    def wait_until(self, predicate, seconds):
        end = time.time() + seconds
        while time.time() < end and not predicate():
            self.opener.wait_for_timeout(100)      # the sync client reads the engine's events inside its calls
        return predicate()

    def close(self):
        try:
            self.context.close()
        except Exception:
            pass


@pytest.mark.parametrize("how", ["open", "link"])
def test_a_popup_to_a_204_is_never_announced(browser, site, how):      # noqa: F811
    run = _Run(browser, site, how, "1")
    try:
        assert not run.wait_until(lambda: run.popups, NO_EVENT_SECONDS), \
            "a 204 destination was announced as a page after %.1fs (stock gives no page event)" % run.arrived_at
        assert len(run.context.pages) == 1
    finally:
        run.close()


@pytest.mark.parametrize("how", ["open", "link"])
def test_a_popup_to_a_server_that_never_answers_is_never_announced(browser, site, how):      # noqa: F811
    run = _Run(browser, site, how, "3")
    try:
        assert not run.wait_until(lambda: run.popups, NO_EVENT_SECONDS), \
            "an unanswered destination was announced as a page after %.1fs (stock gives no page event)" % run.arrived_at
        assert len(run.context.pages) == 1
    finally:
        run.close()


@pytest.mark.parametrize("how", ["open", "link"])
def test_a_popup_to_a_slow_page_is_announced_when_it_answers(browser, site, how):      # noqa: F811
    run = _Run(browser, site, how, "2")
    try:
        assert run.wait_until(lambda: run.popups, SLOW_SECONDS + 6), "the slow popup never arrived"
        assert run.arrived_at >= SLOW_SECONDS - 0.5, "announced after %.1fs, before the page answered" % run.arrived_at
        popup = run.popups[0]
        popup.wait_for_load_state("load", timeout=10000)
        assert popup.url == "http://127.0.0.1:%d/d2" % site
        assert popup.evaluate("document.title") == "slow"
        assert len(run.context.pages) == 2
    finally:
        run.close()


def test_a_popup_that_answers_after_25_seconds_is_still_announced(browser, site):      # noqa: F811
    """No ceiling: stock announces this popup at 25.1 s. One way of opening is enough, the wait is what is tested."""
    run = _Run(browser, site, "open", "5")
    try:
        assert run.wait_until(lambda: run.popups, VERY_SLOW_SECONDS + 10), \
            "a popup whose page answers after %d s never reached the client (stock announces it then)" % VERY_SLOW_SECONDS
        assert run.arrived_at >= VERY_SLOW_SECONDS - 0.5, "announced after %.1fs, before the page answered" % run.arrived_at
        popup = run.popups[0]
        popup.wait_for_load_state("load", timeout=10000)
        assert popup.url == "http://127.0.0.1:%d/d5" % site
        assert popup.evaluate("document.title") == "very slow"
        assert len(run.context.pages) == 2
    finally:
        run.close()


@pytest.mark.parametrize("how", ["open", "link"])
def test_a_popup_to_an_attachment_reports_the_download_on_the_opener_and_closes(browser, site, how):      # noqa: F811
    run = _Run(browser, site, how, "4")
    try:
        assert run.wait_until(lambda: run.downloads, 10), "no `download` event on the opener (stock gives one)"
        assert run.downloads[0].suggested_filename == "a.txt"
        assert run.popups, "stock announces the popup (its url is empty) before it closes"
        assert run.wait_until(lambda: run.popups[0].is_closed(), 10), "the popup of a download stays open"
        assert len(run.context.pages) == 1
    finally:
        run.close()
