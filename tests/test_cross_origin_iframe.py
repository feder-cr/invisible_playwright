"""Regression tests for cross-origin / cross-process iframe interaction.

History: wrapper repo issue #20 reported that a third-party cookie
consent iframe was completely unreachable from Playwright in 0.1.7 -
``element_handle.content_frame()`` returned ``None``, ``frame.evaluate()``
threw cross-origin SOP errors, and ``frame_locator().click()`` timed
out.

Root cause was a missing pref. FF150 ships with
``fission.webContentIsolationStrategy=1`` (IsolateEverything), which
site-isolates cross-origin iframes into separate webIsolated content
processes even when ``fission.autostart=False``. The Juggler code paths
inherited from the FF146 era assume same-process iframes. The wrapper's
``_BASELINE`` now pins the pref to 0 (IsolateNothing).

These tests exist so a future Firefox upgrade or a fingerprint A/B
that flips this pref by accident cannot ship without a red CI signal.

Layers:
  * ``unit`` - ``_BASELINE`` contains the pref with the right value. No browser.
  * ``e2e``  - launch the real binary against a LOCAL HTTP harness on
              ``127.0.0.1`` (two ports = two SOP origins) and verify the
              four protocol operations that regressed: frame URL tracking,
              ``handle.content_frame()``, ``frame.evaluate()``, and
              ``frame_locator(...).locator(...)`` element resolution.

The e2e tests run entirely offline. They never call out to a real site;
the cross-origin shape is reproduced with two local HTTP servers on
random free ports.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import _BASELINE, translate_profile_to_prefs


# ────────────────────────────────────────────────────────────────────
# Unit layer - fast, no browser, runs on every CI
# ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_baseline_pins_web_content_isolation_strategy_to_zero():
    """Regression sentinel.

    ``fission.webContentIsolationStrategy`` MUST be 0 (IsolateNothing).
    The FF150 default is 1 (IsolateEverything), which site-isolates
    cross-origin iframes into separate webIsolated content processes
    and breaks Playwright frame tracking from the parent process.
    """
    assert _BASELINE["fission.webContentIsolationStrategy"] == 0, (
        "fission.webContentIsolationStrategy must be 0 (IsolateNothing). "
        "If you bumped it for an A/B, cross-origin iframes will appear "
        "in page.frames with empty URLs and content_frame() will return "
        "None - see the changelog entry that introduced this test."
    )


@pytest.mark.unit
def test_baseline_keeps_fission_autostart_off():
    """Belt for the suspenders above. All three prefs are required."""
    assert _BASELINE["fission.autostart"] is False
    assert _BASELINE["fission.autostart.session"] is False
    assert _BASELINE["dom.ipc.processCount.webIsolated"] == 1


@pytest.mark.unit
def test_translated_profile_propagates_isolation_strategy():
    """The fix must survive translate_profile_to_prefs, not just live in _BASELINE."""
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    assert prefs["fission.webContentIsolationStrategy"] == 0


@pytest.mark.unit
def test_extra_prefs_override_can_break_isolation_only_explicitly():
    """If a caller wants to A/B isolation, they have to set it explicitly.
    The wrapper does not silently flip it back on.
    """
    p = generate_profile(seed=42)
    prefs_default = translate_profile_to_prefs(p)
    assert prefs_default["fission.webContentIsolationStrategy"] == 0

    prefs_ab = translate_profile_to_prefs(
        p, extra_prefs={"fission.webContentIsolationStrategy": 1}
    )
    assert prefs_ab["fission.webContentIsolationStrategy"] == 1


# ────────────────────────────────────────────────────────────────────
# E2E layer - needs cached binary + bind to localhost ports
# ────────────────────────────────────────────────────────────────────


# ⛔ THERE IS NO `_free_port()` HERE ANY MORE, AND ITS ABSENCE IS THE POINT.
#
# It used to bind port 0, read the number the kernel assigned, CLOSE the
# socket, and hand the number back for someone to bind again later. Between the
# close and the second bind the port belongs to nobody, so a second process
# asking the same question in that window is told the same number, and whichever
# of the two binds second dies with EADDRINUSE.
#
# Sequentially that window is never contended and the pattern was fine for as
# long as the suite ran one test at a time. `run_e2e.py` now opens four workers
# by default, so it IS contended, and the failure it produces would be an
# address-in-use traceback in a browser test - which reads as the product being
# broken rather than the harness racing itself.
#
# The fix is to never let go of the port: `_serve` below binds 0 itself and the
# caller reads the number back off the listening socket, so there is no window
# at all. `test_proxy_socks_auth_e2e.py` and `test_webrtc_realness.py` were
# already written this way; this file and `test_long_session_e2e.py` were the
# two that were not.


class _SilentHandler(BaseHTTPRequestHandler):
    #: A mute socket no longer pins a thread: after five seconds it drops.
    timeout = 5
    """Suppress per-request access logging so pytest output stays clean."""
    PAYLOAD = b""  # set per-instance via subclassing

    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self.PAYLOAD)


def _serve(payload: bytes) -> tuple[ThreadingHTTPServer, int]:
    """Serve ``payload`` on every GET from a kernel-chosen port on 127.0.0.1.

    Returns the server and the port it is ALREADY listening on, so the number
    is never valid-but-unbound in between. See the note above.
    """
    handler_cls = type(
        "_H", (_SilentHandler,), {"PAYLOAD": payload}
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


@pytest.fixture
def cross_origin_harness():
    """Spin up TWO local HTTP servers on different localhost ports.

    Two ports = two distinct origins under SOP (same host, different port
    → different origin). The parent page on port A embeds an iframe with
    src pointing at port B. Same cross-origin browsing-context shape as
    a parent-page-plus-third-party-iframe layout, fully offline.
    """
    # The CHILD goes up first, because the parent's markup has to name the
    # child's port and there is no longer a way to learn that number without
    # binding it. That ordering is the whole cost of closing the race.
    child_html = b"""<!doctype html><html><body>
<button id="ok">confirm</button>
<button class="btn-primary">primary</button>
<script>document.getElementById('ok').addEventListener('click', () => document.title = 'clicked')</script>
</body></html>"""
    sb, pb = _serve(child_html)
    parent_html = f"""<!doctype html><html><head><title>parent</title></head><body>
<h1>parent</h1>
<iframe id="ifr_plain"   src="http://127.0.0.1:{pb}/child"            width="300" height="120"></iframe>
<iframe id="ifr_sandbox" src="http://127.0.0.1:{pb}/child"            width="300" height="120"
        sandbox="allow-scripts allow-same-origin"></iframe>
<iframe id="ifr_titled"  src="http://127.0.0.1:{pb}/child"            width="300" height="120"
        title="cross-origin titled iframe"></iframe>
</body></html>""".encode("utf-8")
    sa, pa = _serve(parent_html)
    try:
        yield {"parent_url": f"http://127.0.0.1:{pa}/", "child_origin": f"http://127.0.0.1:{pb}"}
    finally:
        sa.shutdown()
        sb.shutdown()


@pytest.mark.e2e
def test_cross_origin_iframe_url_appears_in_page_frames(firefox_binary, cross_origin_harness):
    """``page.frames`` must list the cross-origin iframe with its real URL.

    Before the pref fix, the URL came back as '' because the navigation
    observer for the iframe fired in a different content process than
    the parent's FrameTree was registered in.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(cross_origin_harness["parent_url"], wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("iframe#ifr_plain", timeout=10_000)

        # ⛔ WAIT FOR THE CONDITION, NOT FOR A DURATION. This was
        # `wait_for_timeout(500)`, and that measured the machine rather than
        # the product: by then the child frame is ATTACHED but on a loaded box
        # it has not NAVIGATED, so `page.frames` answers with empty URLs and
        # the assertion below reads them as the very defect it guards.
        #
        # Measured 2026-08-29: red inside the full e2e run, with
        # `urls = ['http://127.0.0.1:.../', '', '', '']`, and green 5 times out
        # of 5 on its own. That gap between loaded and idle is the signature of
        # a timing assumption, not of a regression.
        #
        # It was never retried either: `run_e2e.py` reruns on a fixed list of
        # load-flake messages, and this assertion's text is domain-specific so
        # it can never match. Adding it to that list would have been the wrong
        # fix - it hides the failure instead of removing the race.
        #
        # ⛔ THE FAILURE MODE IS DELIBERATELY UNCHANGED. If no frame EVER
        # reports the child origin - the pref regression this file exists for -
        # the loop runs out of time and the same assertion fires with the same
        # message. Verified by mutation: with the origin replaced by one that
        # never appears, this still goes red.
        deadline = time.monotonic() + 10.0
        urls: list = []
        while True:
            urls = [f.url for f in page.frames]
            if any(cross_origin_harness["child_origin"] in (u or "") for u in urls):
                break
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(100)

        assert any(cross_origin_harness["child_origin"] in (u or "") for u in urls), (
            f"no frame had the child origin in its URL; page.frames urls = {urls!r}"
        )


@pytest.mark.e2e
def test_cross_origin_iframe_content_frame_resolves(firefox_binary, cross_origin_harness):
    """``handle.content_frame()`` must return a Frame (not None) for every
    cross-origin iframe shape we care about: plain, sandboxed, titled.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(cross_origin_harness["parent_url"], wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("iframe#ifr_plain", timeout=10_000)
        page.wait_for_timeout(500)

        for sel in ("iframe#ifr_plain", "iframe#ifr_sandbox", "iframe#ifr_titled"):
            handle = page.query_selector(sel)
            assert handle is not None, f"{sel!r} not found in DOM"
            cf = handle.content_frame()
            assert cf is not None, f"{sel!r}: content_frame() returned None"
            assert cross_origin_harness["child_origin"] in (cf.url or ""), (
                f"{sel!r}: content_frame().url = {cf.url!r}, "
                f"expected child origin {cross_origin_harness['child_origin']!r}"
            )


@pytest.mark.e2e
def test_cross_origin_iframe_evaluate_returns_real_values(firefox_binary, cross_origin_harness):
    """``frame.evaluate()`` inside the cross-origin iframe must work.

    Pre-fix: every evaluate failed with a cross-origin SOP error because
    the iframe ended up with a stale/wrong execution context.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(cross_origin_harness["parent_url"], wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("iframe#ifr_plain", timeout=10_000)
        page.wait_for_timeout(500)

        cf = page.query_selector("iframe#ifr_plain").content_frame()
        assert cf is not None
        href = cf.evaluate("() => location.href")
        assert cross_origin_harness["child_origin"] in href
        title = cf.evaluate("() => document.title")
        assert isinstance(title, str)
        n_buttons = cf.evaluate("() => document.querySelectorAll('button').length")
        assert n_buttons == 2


@pytest.mark.e2e
def test_cross_origin_iframe_frame_locator_resolves_button(firefox_binary, cross_origin_harness):
    """``frame_locator(...).locator(...)`` must reach the button inside the iframe."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(cross_origin_harness["parent_url"], wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("iframe#ifr_plain", timeout=10_000)

        for selector in ("button#ok", "button.btn-primary"):
            cnt = page.frame_locator("iframe#ifr_plain").locator(selector).count()
            assert cnt == 1, f"locator({selector!r}) found {cnt} elements (expected 1)"


@pytest.mark.e2e
def test_cross_origin_iframe_dispatch_event_click_works(firefox_binary, cross_origin_harness):
    """End-to-end interaction via ``dispatch_event`` must succeed.

    Plain ``.click()`` can trip Playwright's actionability heuristic on
    some third-party UIs (same on vanilla Playwright Firefox - not our
    regression), but ``dispatch_event('click')`` always works once the
    iframe is reachable.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(cross_origin_harness["parent_url"], wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("iframe#ifr_plain", timeout=10_000)

        page.frame_locator("iframe#ifr_plain").locator("button#ok").dispatch_event(
            "click", timeout=4_000
        )
        cf = page.query_selector("iframe#ifr_plain").content_frame()
        assert cf.evaluate("() => document.title") == "clicked"


# ──────────────────────────────────────────────────────────────────────
#  The geometry invariant INSIDE a nested frame
# ──────────────────────────────────────────────────────────────────────
_PARENT_GEOM = b"""<!doctype html><meta charset=utf-8><title>parent</title>
<style>html,body{margin:0;padding:0;height:100%}
#f{position:absolute;left:@LEFTpx;top:@TOPpx;width:360px;height:220px;border:0}</style>
<body><iframe id=f src="@SRC"></iframe>
<script>
window.__geom = () => {
  const w = document.getElementById('f').contentWindow;
  const r = document.getElementById('f').getBoundingClientRect();
  return {top_x: window.mozInnerScreenX, top_y: window.mozInnerScreenY,
          ifr_x: w.mozInnerScreenX,      ifr_y: w.mozInnerScreenY,
          rect_x: r.x, rect_y: r.y};
};
</script>"""

_CHILD_GEOM = b"""<!doctype html><meta charset=utf-8><title>child</title>
<style>html,body{margin:0;padding:0;height:100%}
#g{position:absolute;left:30px;top:40px;width:220px;height:130px;background:#06c}</style>
<body><div id=g>y</div>
<script>
window.__ev = [];
for (const t of ['mousemove','mousedown','mouseup','click']) {
  window.addEventListener(t, e => {
    window.__ev.push({t:t, cx:e.clientX, cy:e.clientY, sx:e.screenX, sy:e.screenY});
  }, true);
}
window.__inner = () => [window.mozInnerScreenX, window.mozInnerScreenY];
</script>"""


@pytest.mark.e2e
def test_the_geometry_invariant_holds_inside_a_nested_frame(firefox_binary):
    """screenX - clientX == mozInnerScreenX, INSIDE an iframe and not just at the top.

    The defect this test exists to stop, measured on 2026-08-10:
    `mozInnerScreenX/Y` answered the origin of the TOP-LEVEL content to
    any window, so an iframe positioned at (220, 150) in the page
    reported (0, 85) - the same origin as the document that contains it - and
    in there `screenX - clientX` was 220 against a `mozInnerScreenX` of 0.

    It was the contradiction that the geometry declaration exists to
    remove, recreated identically one level down. And the gate written the day
    before to defend exactly that relationship was GREEN, because it looked at
    a single document: a relationship that holds at one level and not the next
    is not a relationship, it is a coincidence at the first level.

    Needs real pages from 127.0.0.1: `data:` URLs carry a CSP that changes the
    behavior, and on one of those measurements the browser refuses `set_content`
    as an unsafe operation.
    """
    # SAME ORIGIN, one server and two paths. Serving parent and child on
    # different ports, the security policy forbids the parent from reading
    # `mozInnerScreenX` of the child - "Permission denied to access property on
    # cross-origin object" - and the test would measure the SOP, not the geometry.
    left, top = 220, 150
    parent_html = (_PARENT_GEOM
                   .replace(b"@LEFT", str(left).encode())
                   .replace(b"@TOP", str(top).encode())
                   .replace(b"@SRC", b"/child"))
    class _H(_SilentHandler):
        def do_GET(self):
            body = _CHILD_GEOM if self.path.startswith("/child") else parent_html
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto("http://127.0.0.1:%d/" % port,
                      wait_until="load", timeout=30000)
            g = page.evaluate("() => window.__geom()")

            # 1. the iframe reports its OWN origin, not the parent's
            expected = (g["top_x"] + g["rect_x"], g["top_y"] + g["rect_y"])
            assert (g["ifr_x"], g["ifr_y"]) == expected, (
                f"the iframe reports mozInnerScreen ({g['ifr_x']}, {g['ifr_y']}) "
                f"instead of {expected}: it is the origin of the document that "
                f"contains it, so every frame contradicts its own events"
            )

            # 2. and the relationship holds on the events received IN THERE
            frame = page.frame_locator("#f")
            page.frames[1].evaluate("() => { window.__ev = []; }")
            frame.locator("#g").hover(timeout=10000)
            page.wait_for_timeout(400)
            ev = page.frames[1].evaluate("() => window.__ev")
            inner = page.frames[1].evaluate("() => window.__inner()")
            assert ev, "no event received in the iframe: coverage absent, and this is NOT a pass"
            for e in ev:
                assert e["sx"] - e["cx"] == inner[0], (
                    f"in the iframe screenX-clientX = {e['sx'] - e['cx']} against "
                    f"mozInnerScreenX = {inner[0]}: a page reads it with a subtraction"
                )
                assert e["sy"] - e["cy"] == inner[1], (
                    f"in the iframe screenY-clientY = {e['sy'] - e['cy']} against "
                    f"mozInnerScreenY = {inner[1]}"
                )
            page.close()
    finally:
        srv.shutdown()
