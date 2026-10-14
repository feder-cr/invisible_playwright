"""A page cannot tell that the engine carries the juggler (issue #336).

`new Worker("chrome://<name>/...")` returns for a chrome package the browser
registers and throws SecurityError at once for one it does not: the parent sends
every registered package to each content process. Every engine until firefox-47
registered `juggler`, and captcha vendors probed it from 2026-10-06 (hCaptcha's
hsw.js runs `new Worker("chrome://juggler/content/content/WorkerMain.js")`,
camoufox #847): a challenge solved by hand was refused on our engine while
retail Firefox passed. Measured from a page on firefox-46 and on retail Firefox
151 under geckodriver, 2026-10-14: of the 65 chrome packages and resource
substitutions the engine registers, `juggler` was the one retail does not; retail
returns a Worker for chrome://global and resource://gre and throws for an absent
name, ours did the same and returned a Worker for chrome://juggler.

Since firefox-47 the juggler loads from resource://gre/modules/juggler/, which
every Firefox registers. The test bounds both sides: the juggler's name answers
as an absent package, the names retail registers still answer as registered (a
build that refused every chrome:// URL would be its own tell), and inside
resource://gre a file of the juggler answers exactly as a file that does not
exist, through every way a page can ask. And no stack a page can read names it.
"""
from __future__ import annotations

import http.server
import threading

import pytest

pytestmark = pytest.mark.e2e

_PAGE = b"<html><body><p>probe</p></body></html>"

#: What the Worker constructor does with each URL: retail Firefox 151's answer.
_REGISTERED_AS_RETAIL = {
    "chrome://juggler/content/content/WorkerMain.js": "throws SecurityError",
    "chrome://juggler/content": "throws SecurityError",
    "chrome://nosuchpackage/content/x.js": "throws SecurityError",
    "chrome://global/content/x.js": "Worker",
    "chrome://remote/content/x.js": "Worker",
    "resource://gre/modules/x.js": "Worker",
    "resource://nosuchres/x.js": "throws SecurityError",
}

_WORKER = r"""(urls) => {
  const out = {};
  for (const url of urls) {
    try { const w = new Worker(url); out[url] = "Worker"; w.terminate(); }
    catch (e) { out[url] = "throws " + e.name; }
  }
  return out;
}"""

#: Every way a page can ask for a URL, and what it hears back. Timings are left
#: out on purpose: a file that exists and one that does not are refused by the
#: same security check before anything is read, so what can differ is the
#: answer, and the answer is compared.
_CHANNELS = r"""async (url) => {
  const settle = (p, ms = 3000) => Promise.race([p, new Promise(r => setTimeout(() => r("no answer"), ms))]);
  const out = {};
  out.worker = await settle(new Promise(r => {
    try {
      const w = new Worker(url);
      w.onerror = e => { e.preventDefault(); r("error event: " + (e.message || "")); };
      w.onmessage = () => r("message");
    } catch (e) { r("throws " + e.name); }
  }));
  out.module_worker = await settle(new Promise(r => {
    try {
      const w = new Worker(url, {type: "module"});
      w.onerror = e => { e.preventDefault(); r("error event: " + (e.message || "")); };
    } catch (e) { r("throws " + e.name); }
  }));
  out.shared_worker = await settle(new Promise(r => {
    try {
      const w = new SharedWorker(url);
      w.onerror = e => { e.preventDefault(); r("error event"); };
    } catch (e) { r("throws " + e.name); }
  }));
  out.fetch = await settle(fetch(url).then(res => "status " + res.status, e => "rejects " + e.name));
  out.xhr = await settle(new Promise(r => {
    const seen = [];
    try {
      const x = new XMLHttpRequest();
      for (const t of ["loadstart", "progress", "error", "abort", "load", "loadend"])
        x.addEventListener(t, () => { seen.push(t); if (t === "loadend") r(seen.join(",")); });
      x.open("GET", url);
      x.send();
    } catch (e) { r("throws " + e.name); }
  }));
  out.import = await settle(import(url).then(() => "loaded", e => "rejects " + e.name));
  for (const [tag, attr] of [["script", "src"], ["img", "src"], ["link", "href"], ["iframe", "src"]]) {
    out[tag] = await settle(new Promise(r => {
      const el = document.createElement(tag);
      if (tag === "link") el.rel = "stylesheet";
      el.onload = () => r("load");
      el.onerror = () => r("error");
      el[attr] = url;
      document.body.appendChild(el);
    }), 1500);
  }
  return out;
}"""


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(_PAGE)))
            self.end_headers()
            self.wfile.write(_PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture(scope="module")
def page(firefox_binary, server):
    from invisible_playwright import InvisiblePlaywright
    with InvisiblePlaywright(seed=336, binary_path=firefox_binary, headless=True) as browser:
        p = browser.new_page()
        p.goto(server)
        yield p


def test_the_juggler_name_answers_as_a_package_the_browser_does_not_have(page):
    got = page.evaluate(_WORKER, list(_REGISTERED_AS_RETAIL))
    assert got == _REGISTERED_AS_RETAIL


def test_a_juggler_file_answers_as_a_file_that_does_not_exist(page):
    """Inside resource://gre, which retail registers too, the engine's own file
    and a name that is nowhere must look the same from every channel."""
    ours = page.evaluate(_CHANNELS, "resource://gre/modules/juggler/content/WorkerMain.js")
    nothing = page.evaluate(_CHANNELS, "resource://gre/modules/juggler-nowhere/x.js")
    assert ours == nothing


def test_no_stack_a_page_reads_names_the_juggler(page):
    """The page's own stacks, from code the page runs and from a callback the
    engine's input drives into it."""
    stacks = page.evaluate(r"""() => {
      const out = [new Error("here").stack];
      try { null.x; } catch (e) { out.push(e.stack); }
      return out;
    }""")
    page.evaluate("""() => { window.__stack = null;
      document.body.addEventListener('click', () => { window.__stack = new Error('click').stack; },
                                     {once: true}); }""")
    page.click("p")
    stacks.append(page.evaluate("() => window.__stack"))
    assert stacks[-1], "the click handler did not run"
    for s in stacks:
        assert "juggler" not in s.lower(), s
