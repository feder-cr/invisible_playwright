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

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _SilentHandler(BaseHTTPRequestHandler):
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


def _serve(payload: bytes, port: int) -> HTTPServer:
    """Start an HTTP server on 127.0.0.1:port serving ``payload`` on every GET."""
    handler_cls = type(
        "_H", (_SilentHandler,), {"PAYLOAD": payload}
    )
    srv = HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture
def cross_origin_harness():
    """Spin up TWO local HTTP servers on different localhost ports.

    Two ports = two distinct origins under SOP (same host, different port
    → different origin). The parent page on port A embeds an iframe with
    src pointing at port B. Same cross-origin browsing-context shape as
    a parent-page-plus-third-party-iframe layout, fully offline.
    """
    pa, pb = _free_port(), _free_port()
    parent_html = f"""<!doctype html><html><head><title>parent</title></head><body>
<h1>parent</h1>
<iframe id="ifr_plain"   src="http://127.0.0.1:{pb}/child"            width="300" height="120"></iframe>
<iframe id="ifr_sandbox" src="http://127.0.0.1:{pb}/child"            width="300" height="120"
        sandbox="allow-scripts allow-same-origin"></iframe>
<iframe id="ifr_titled"  src="http://127.0.0.1:{pb}/child"            width="300" height="120"
        title="cross-origin titled iframe"></iframe>
</body></html>""".encode("utf-8")
    child_html = b"""<!doctype html><html><body>
<button id="ok">confirm</button>
<button class="btn-primary">primary</button>
<script>document.getElementById('ok').addEventListener('click', () => document.title = 'clicked')</script>
</body></html>"""
    sa = _serve(parent_html, pa)
    sb = _serve(child_html, pb)
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
        page.wait_for_timeout(500)

        urls = [f.url for f in page.frames]
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
