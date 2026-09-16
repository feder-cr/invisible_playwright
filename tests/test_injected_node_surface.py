"""The injected script does not leave anything on the page's NODES.

⛔ THE OTHER HALF OF A CLASS WHOSE FIRST HALF IS ALREADY CLOSED. The bundle used
to live on the page's `window` - capture listeners for the duration of every
action - and that is gone. This file covers the second verb: what the bundle
WRITES on elements.

The one known writer is `computeAriaRef`, which sets `element._ariaRef` while it
builds the accessibility tree. It runs only when the caller asks for
`mode="ai"`, which nothing in this package asks for today - but a guard that is
the VALUE OF A CALLER'S ARGUMENT is not a property of the code, and the question
underneath it had never been answered: does an assignment made through an Xray
stay in the sandbox, or does it reach the page's node?

Measured 2026-09-16, and the answer is that it stays: with `mode="ai"` the
snapshot comes back carrying 8 refs and the sandbox sees `typeof
el._ariaRef === "object"`, while the page, looking at its own element with its
own script, sees nothing at all.

⛔ SO WHY A GATE FOR SOMETHING THAT IS NOT A DEFECT. Because the day that
confinement stops holding - a Gecko change, or the injected script being built
somewhere that is not an Xray sandbox - every interactable element in the
document would carry a property any page can enumerate, and nothing would say
so. This is the cheapest possible assertion on the most expensive possible
regression.
"""
from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

pytestmark = pytest.mark.e2e


PAGE = """<!doctype html>
<html><head><title>node surface</title></head><body>
<button id="b">press</button>
<a id="l" href="#">a link</a>
<input id="i">
<div id="verdict">not yet</div>
<div id="control">not yet</div>
<script>
// ⛔ THE PAGE LOOKS AT ITSELF. Asking through `page.evaluate` would report what
// OUR world can see, which is the other question - and the one that is already
// known to be true.
const BASE = new WeakMap();
function remember(el) { BASE.set(el, new Set(Object.getOwnPropertyNames(el))); }

// ⛔ ONE function, used by BOTH the verdict and its control. The first
// version had two, and a mutation said so: blinding the verdict's function left
// the control - which looked at the element its own way - still reporting that
// it could see. A control read with a different instrument measures the
// instrument, not the arm.
function arrivedFromOutside(el, label) {
  const found = [];
  for (const p of Object.getOwnPropertyNames(el))
    if (!BASE.get(el).has(p)) found.push(label + '.' + p);
  // ⛔ THE DIRECT LOOK IS NOT REDUNDANT WITH THE COMPARISON ABOVE, and a
  // mutation proved it: a write that lands on the page's PROTOTYPE never shows
  // up in the element's own property names, and only `in` sees it. The
  // comparison catches writes to the element, this catches writes above it, and
  // each of the two survives a mutation that removes the other.
  if (el._ariaRef !== undefined) found.push(label + '._ariaRef');
  if ('_ariaRef' in el) found.push(label + ':_ariaRef in el');
  return found;
}

const WATCHED = ['b', 'l', 'i'].map(id => document.getElementById(id));
WATCHED.forEach(remember);

setInterval(() => {
  const f = [];
  WATCHED.forEach((el, n) => f.push(...arrivedFromOutside(el, 'el' + n)));
  document.getElementById('verdict').textContent =
      f.length ? ('DIRTY ' + f.join(' ')) : 'clean';
}, 50);

// ⛔ THE CONTROL FOR THE WATCHER ITSELF. The page stamps a fresh element with
// the very property being hunted, and THE SAME FUNCTION has to notice. A
// watcher that has only ever said "clean" does not distinguish clean from
// blind - which is exactly the failure this project has recorded more than
// once.
const probe = document.createElement('button');
document.body.appendChild(probe);
remember(probe);
probe._ariaRef = {role: 'button', name: 'fake', ref: 'e1'};
const seen = arrivedFromOutside(probe, 'probe');
document.getElementById('control').textContent =
    seen.length ? ('the watcher sees: ' + seen.join(' ')) : 'THE WATCHER IS BLIND';
</script>
</body></html>
"""


class _Served:
    """``PAGE`` on a kernel-chosen port of 127.0.0.1.

    A real origin, not a `data:` URL: several of the things this file reasons
    about - secure context, the script executing on its own - differ there, and
    a measurement taken on `about:blank` has produced nineteen false findings in
    this project before.
    """

    def __enter__(self):
        body = PAGE.encode("utf-8")

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/" % self._srv.server_address[1]
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()


def _page_dispatcher(page):
    """The server-side object serving this page.

    The engine server runs IN THIS PROCESS, so the branch that writes can be
    reached directly. `mode` is not on the public signature - which is the whole
    point: this asserts on a path a caller could take, not only on the one this
    package takes.
    """
    impl = getattr(page, "_impl_obj", page)
    server = impl._channel._connection._transport._server
    pages = [o for o in server._objects.values()
             if getattr(o, "injected", None) is not None]
    assert pages, "no PageDispatcher: the in-process layout has changed"
    return pages[-1]


def _verdict(page, which):
    return page.locator("#" + which).text_content()


def test_the_aria_snapshot_writes_nothing_the_page_can_see(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with _Served() as site, InvisiblePlaywright(
            seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(site.url)
        page.wait_for_timeout(200)

        assert "the watcher sees:" in _verdict(page, "control"), (
            "the watcher cannot see the property it is hunting, so its 'clean' "
            "means nothing: %r" % _verdict(page, "control"))
        assert _verdict(page, "verdict") == "clean", "dirty before we touched it"

        d = _page_dispatcher(page)
        frame = d.actions.lifecycle.main_frame
        call = ("(injected, o) => injected.ariaSnapshot("
                "document.documentElement, o)")

        snapshot = d.injected.call(frame, call, {"mode": "ai"})
        page.wait_for_timeout(200)

        # ⛔ THE BRANCH HAS TO HAVE RUN, or this test goes green on a build
        # where nothing was written and says nothing about confinement. `ref=`
        # in the snapshot is what `computeAriaRef` produces, and the sandbox
        # seeing the expando is the assignment itself.
        assert str(snapshot).count("[ref=") > 0, (
            "no refs in the snapshot, so the writing branch was never reached "
            "and this assertion is empty")
        from_sandbox = d.injected.evaluate(
            frame, "(() => { const el = document.getElementById('b');"
                   " return [typeof el._ariaRef, '_ariaRef' in el]; })()")
        assert list(from_sandbox) == ["object", True], (
            "the sandbox does not see the expando either, so nothing was "
            "written: %r" % (from_sandbox,))

        # And the thing this file exists for.
        assert _verdict(page, "verdict") == "clean", (
            "the injected script left something on a page NODE: %r"
            % _verdict(page, "verdict"))


def test_the_default_path_does_not_even_write_in_the_sandbox(firefox_binary):
    """`raw` is what this package asks for, and it leaves before writing.

    Worth its own case: if the default ever started writing, the test above
    would still pass - it drives `ai` deliberately - and the change would be
    invisible.
    """
    from invisible_playwright import InvisiblePlaywright

    with _Served() as site, InvisiblePlaywright(
            seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto(site.url)
        page.wait_for_timeout(200)

        d = _page_dispatcher(page)
        frame = d.actions.lifecycle.main_frame
        d.injected.call(frame,
                        "(injected, o) => injected.ariaSnapshot("
                        "document.documentElement, o)", {"mode": "raw"})
        page.wait_for_timeout(200)

        from_sandbox = d.injected.evaluate(
            frame, "(() => { const el = document.getElementById('b');"
                   " return [typeof el._ariaRef, '_ariaRef' in el]; })()")
        assert list(from_sandbox) == ["undefined", False], (
            "the default mode wrote an expando: %r" % (from_sandbox,))
        assert _verdict(page, "verdict") == "clean"
