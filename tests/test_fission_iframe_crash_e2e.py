"""The crash of an iframe's process leaves the tab open, on a real engine with a process per site.

⛔ THE KNOWN-BAD INPUT: with Fission on (a process per site), an out-of-process iframe's BrowserParent has the TAB's
<browser> as its owner element, so the engine's `oop-frameloader-crashed` notification reaches Juggler's observer with
the tab's frameloader for the crash of one iframe. The observer answered every notification the same way: Page.crashed,
dispose the target, close the tab. A page whose only broken part was an iframe was closed under the client.

From firefox-62 the engine passes the crashed BrowsingContext's id as the notification's data
(nsFrameLoader::MaybeNotifyCrashed) and the observer takes a branch of its own for a context with a parent: the tab
stays and the client gets no Page.crashed (it is told the frame is gone with Page.frameDetached).

The process of the iframe is found, not guessed: the engine is asked (Marionette, chrome scope) for the OS pid of the
process that holds the top document and of the one that holds the iframe's; the test requires them to differ (the
iframe is out of process) and kills the iframe's.

The engine that does it is not published when this file is written: `engine_at_least` makes the test wait for it,
and on an older engine it is skipped, never passed.
"""
from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

#: the strategy of one process per site, the hosts resolved to the local server (one port: a.test:P and b.test:P are
#: two sites)
FISSION = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
    "network.dns.localDomains": "a.test,b.test",
}

#: for each browser window, each tab whose document has a child: [top pid, child pid]
PIDS = """
const out = [];
for (const win of Services.wm.getEnumerator('navigator:browser')) {
  for (const tab of win.gBrowser.tabs) {
    const bc = tab.linkedBrowser.browsingContext;
    if (!bc || !bc.children.length) continue;
    const pid = c => c.currentWindowGlobal && c.currentWindowGlobal.domProcess ? c.currentWindowGlobal.domProcess.osPid : null;
    out.push([pid(bc), pid(bc.children[0])]);
  }
}
return JSON.stringify(out);
"""


def _serve():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            if self.path.startswith("/frame"):
                body = b"<!doctype html><title>frame</title><h1 id=h>in the iframe on " + host.encode() + b"</h1>"
            else:
                body = (b"<!doctype html><title>top</title><h1>top</h1>"
                        b"<iframe id=f src='http://b.test:%d/frame'></iframe>" % self.server.server_address[1])
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait(predicate, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def test_the_crash_of_an_iframe_s_process_leaves_the_tab(firefox_binary, engine_at_least, monkeypatch):
    engine_at_least("firefox-62")
    import psutil
    import webfont_probe
    from invisible_playwright import InvisiblePlaywright

    monkeypatch.setenv("MOZ_REMOTE_ALLOW_SYSTEM_ACCESS", "1")   # the chrome-scope reads below
    server = _serve()
    port = server.server_address[1]
    marionette = None
    try:
        prefs = dict(FISSION, **{"marionette.port": webfont_probe.MARIONETTE_PORT})
        with InvisiblePlaywright(binary_path=firefox_binary, headless=True, humanize=False,
                                 extra_args=["-marionette"], extra_prefs=prefs) as browser:
            page = browser.new_context().new_page()
            crashes, closes = [], []
            page.on("crash", lambda p: crashes.append(p))
            page.on("close", lambda p: closes.append(p))
            page.goto("http://a.test:%d/" % port, wait_until="load")
            assert _wait(lambda: len(page.frames) == 2, 20), "the iframe never became a frame of the page"
            # the iframe's document is not read, nor its address: what the client can see of a frame in another
            # process is what the rest of Fission builds

            marionette = webfont_probe._Marionette(webfont_probe.MARIONETTE_PORT)
            marionette.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            marionette.call("Marionette:SetContext", {"value": "chrome"})
            pids = json.loads(marionette.call("WebDriver:ExecuteScript", {"script": PIDS, "args": []})["value"])
            assert len(pids) == 1, "one tab with an iframe expected, the engine says %r" % (pids,)
            top_pid, iframe_pid = pids[0]
            assert top_pid and iframe_pid and top_pid != iframe_pid, \
                "the iframe is not out of process (top %r, iframe %r): this engine is not running a process per site" % (top_pid, iframe_pid)

            psutil.Process(iframe_pid).kill()

            # The tab stays. The client is told neither that the page crashed nor that it closed; if the engine
            # closed the tab, those events come within a moment, so they are waited for before they are denied.
            _wait(lambda: crashes or closes or page.is_closed(), 8)
            assert not crashes, "Page.crashed was sent for the crash of an iframe"
            assert not closes and not page.is_closed(), "the tab was closed for the crash of an iframe"
            assert page.evaluate("1 + 1") == 2
            assert page.text_content("h1") == "top"
            # Not asserted here: that `page.frames` drops the crashed frame. The client keeps a frame of another
            # process until the Fission lifecycle work (a frame removed from the DOM stays in `page.frames` too).
    finally:
        if marionette is not None:
            marionette.close()
        server.shutdown()
