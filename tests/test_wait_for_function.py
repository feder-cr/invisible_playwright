"""`wait_for_function` runs the CALLER'S code, so it runs in the CALLER'S world.

⛔ IT POLLED BEHIND THE XRAY, and that made it blind to exactly the thing people
wait for. Everything in this server evaluates in the utility world on purpose -
a site cannot count reads it cannot see - and two methods leave that world
because the caller asked for the page's own by name: `evaluate`, and this one.
The second was missed.

From the utility world a page global does not exist. So
`() => !!window.Fingerprint` is false forever, the call dies on its own timeout,
and the message names an expression that was true in the page the whole time.

**What it cost, measured 2026-08-28.** Five e2e tests failed on this transport
and passed on the driver: every one that waits for a real detector library to
finish. 181 of 186 were green, so the gap read as a detector problem rather than
as a world problem.

⛔ AND THE SECOND HALF ONLY APPEARED ONCE THE FIRST WAS FIXED. The call must
return a HANDLE: `_frame.py` wraps the reply in `from_channel(...)`, so
answering `None` raises `AttributeError: 'NoneType' object has no attribute
'_object'` INSIDE the client, on a call that had just succeeded. While the poll
timed out first, that bug could not be reached.

⛔ And a handle born in the page's world cannot be read from the utility one: an
objectId is resolved inside the context it is given. The handle therefore
remembers which world it came from, and `json_value` asks there.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

from _servers import LocalServer

PAGE = b"""<!doctype html><html><head><title>wff</title></head><body>
<p id="state">waiting</p>
<script>
setTimeout(function () {
  window.__ready = {n: 42};
  document.getElementById('state').textContent = 'done';
}, 400);
</script>
</body></html>"""


@pytest.fixture
def served():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    with LocalServer(("127.0.0.1", 0), H) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d/" % srv.server_address[1]
        finally:
            srv.shutdown()


@pytest.mark.e2e
def test_it_sees_a_global_the_PAGE_defined(firefox_binary, served):
    """⛔ THE ASSERTION IS ON A PAGE GLOBAL, deliberately.

    A DOM-only expression would pass from either world - the utility world sees
    the same document through an Xray - so it cannot tell the two apart. A
    `window.` property set by the page's own script exists in one world and not
    the other, which is the whole difference this test is about.
    """
    with InvisiblePlaywright(seed=42, headless=True,
                             binary_path=str(firefox_binary)) as browser:
        page = browser.new_page()
        page.goto(served, wait_until="load")
        handle = page.wait_for_function("() => window.__ready", timeout=15000)

        assert handle is not None, (
            "wait_for_function answered None: `from_channel` turns that into "
            "an AttributeError inside the client, on a call that succeeded")
        assert handle.json_value() == {"n": 42}, (
            "the handle came back but its value did not: an objectId from the "
            "page's world cannot be read through the utility world")


@pytest.mark.e2e
def test_it_also_sees_the_dom(firefox_binary, served):
    """The half that worked before, kept so the fix cannot regress the other way.

    Moving the poll into the page's world must not lose what the utility world
    could already see.
    """
    with InvisiblePlaywright(seed=42, headless=True,
                             binary_path=str(firefox_binary)) as browser:
        page = browser.new_page()
        page.goto(served, wait_until="load")
        page.wait_for_function(
            "() => document.getElementById('state').textContent === 'done'",
            timeout=15000)
        assert page.text_content("#state") == "done"
