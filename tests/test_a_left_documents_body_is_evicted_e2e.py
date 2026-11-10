"""The body of a response of a document the tab left is evicted, on a real engine.

⛔ THE KNOWN-BAD INPUT, measured on firefox-59 over 95 sites in one reused tab: the engine's
`ResponseStorage` kept one entry per request for good, and the bodies of every page the tab had
left (compressed bytes, HTML, CSS: 105 MB, 5,000 entries) stayed readable and held in the parent
process, up to its 100 MB cap. A retail Firefox holds none of them.

Chromium clears a tab's bodies when the main frame commits a navigation (`DidCommitLoad`, which
keeps only the loader that committed), and the engine does the same from firefox-60: the body of
a document the tab left, and of its sub-resources, answers "evicted" (Playwright's own message for
a body over the engine's cap), while the bodies of the document that is current stay readable.

Also here: nothing else is lost. A second context loads a page, and a context that was closed
leaves the browser usable.

The engine that does it is not published when this file is written: `engine_at_least` makes the
test wait for it, and on an older engine it is skipped, never passed.
"""
from __future__ import annotations

import threading

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.e2e

PAGES = {
    "/a.html": (b"<html><head><link rel=stylesheet href=/a.css><script src=/a.js></script></head>"
                b"<body>document A</body></html>", "text/html"),
    "/a.css": (b"body{color:red} /* A */", "text/css"),
    "/a.js": (b"window.fromA = 1;", "text/javascript"),
    "/b.html": (b"<html><head><script src=/b.js></script></head><body>document B</body></html>",
                "text/html"),
    "/b.js": (b"window.fromB = 2;", "text/javascript"),
}


def _serve():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, content_type = PAGES.get(self.path, (b"", "text/plain"))
            self.send_response(200 if self.path in PAGES else 404)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def test_the_body_of_a_document_the_tab_left_is_evicted(firefox_binary, engine_at_least):
    engine_at_least("firefox-60")
    from invisible_playwright import InvisiblePlaywright

    server, base = _serve()
    try:
        with InvisiblePlaywright(seed=4249, binary_path=firefox_binary, headless=True) as browser:
            page = browser.new_page()
            responses = {}
            page.on("response", lambda r: responses.setdefault(r.url.rsplit("/", 1)[-1], r))

            page.goto(base + "/a.html", wait_until="load", timeout=20_000)
            page.wait_for_timeout(300)
            assert b"document A" in responses["a.html"].body()
            assert b"/* A */" in responses["a.css"].body(), "the current document's sub-resource is readable"

            page.goto(base + "/b.html", wait_until="load", timeout=20_000)
            page.wait_for_timeout(300)
            assert b"document B" in responses["b.html"].body(), "the document that committed keeps its body"
            assert b"fromB" in responses["b.js"].body()

            for name in ("a.html", "a.css", "a.js"):
                with pytest.raises(Exception) as err:
                    responses[name].body()
                assert "evicted" in str(err.value).lower(), (name, str(err.value))
    finally:
        server.shutdown()


def test_nothing_else_is_lost_when_the_bodies_go(firefox_binary, engine_at_least):
    engine_at_least("firefox-60")
    from invisible_playwright import InvisiblePlaywright

    server, base = _serve()
    try:
        with InvisiblePlaywright(seed=4250, binary_path=firefox_binary, headless=True) as browser:
            page = browser.new_page()
            for i in range(6):
                page.goto(base + "/%s.html" % "ab"[i % 2], wait_until="load", timeout=20_000)
            second = browser.new_page()
            second.goto(base + "/a.html", wait_until="load", timeout=20_000)
            assert "document A" in second.content()
            second.close()
            page.goto(base + "/b.html", wait_until="load", timeout=20_000)
            assert "document B" in page.content()
    finally:
        server.shutdown()
