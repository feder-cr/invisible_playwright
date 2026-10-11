"""`bypass_csp=True` lifts the page's policy; without it the policy holds.

⛔ KNOWN-BAD, issue #295, reproduced on firefox-40: a context created with
`bypass_csp=True` kept enforcing every policy the page sent. The wrapper sent
`Browser.setBypassCSP`, Juggler wrote `docShell.bypassCSPEnabled`, and nothing
in the engine read it: the attribute had been ported as storage only. From
firefox-41 the CSP code reads it, for the header and for a <meta> policy.

⛔ AND THE OTHER HALF IS NOT DECORATION. The same port left that docshell flag
uninitialized, so a docshell Juggler never wrote to could have bypassed CSP by
accident once something read the flag. Fresh contexts WITHOUT the option must
still block the page's inline script and eval; several of them, because an
uninitialized bit is not wrong every time.
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

POLICY = "script-src 'self'"
PAGES = {
    "/header": (b"<!doctype html><p>header</p><script>window.inline = 1</script>", True),
    "/meta": (b"<!doctype html><head><meta http-equiv=\"Content-Security-Policy\" content=\""
              + POLICY.encode() + b"\"></head><p>meta</p><script>window.inline = 1</script>", False),
}


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, header = PAGES.get(self.path, (b"", False))
            self.send_response(200 if self.path in PAGES else 404)
            self.send_header("Content-Type", "text/html")
            if header:
                self.send_header("Content-Security-Policy", POLICY)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % port


def _what_the_page_could_do(page):
    page.add_script_tag(content="window.added = 7")
    return {
        "inline": page.evaluate("() => window.inline === 1"),
        "eval": page.evaluate("() => { try { return eval('2 + 2'); } catch (e) { return e.name; } }"),
        "added": page.evaluate("() => window.added === 7"),
    }


@pytest.mark.e2e
def test_bypass_csp_lifts_the_page_policy_and_its_absence_keeps_it(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, base = _serve()
    blocked = {"inline": False, "eval": "EvalError", "added": False}
    lifted = {"inline": True, "eval": 4, "added": True}
    try:
        with InvisiblePlaywright(seed=4245, binary_path=firefox_binary,
                                 headless=True) as browser:
            for path in PAGES:
                for _ in range(4):
                    ctx = browser.new_context()
                    page = ctx.new_page()
                    page.goto(base + path, wait_until="load")
                    assert _what_the_page_could_do(page) == blocked, (
                        "%s: without bypass_csp the page's policy must hold" % path)
                    ctx.close()
                ctx = browser.new_context(bypass_csp=True)
                page = ctx.new_page()
                page.goto(base + path, wait_until="load")
                assert _what_the_page_could_do(page) == lifted, (
                    "%s: bypass_csp=True left the page's policy in force" % path)
                ctx.close()
    finally:
        srv.shutdown()
