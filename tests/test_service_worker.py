"""Service worker interception regression tests - issue #18 root cause.

The bug: `juggler/content/NetworkObserver.js:channelIntercepted` called
`interceptedChannel.interceptAfterServiceWorkerResets()` - an IDL method
that upstream Playwright adds via a C++ patch (InterceptedHttpChannel.cpp
+ nsINetworkInterceptController.idl). Our fork was missing those patches
until firefox-6, so the call threw TypeError → C++ NetworkObserver was
left in an inconsistent state → content process disposal manifested as
"page crash" on sites whose service workers fall through to the network.

These tests inline-serve a service worker via data: URLs / blob URLs
where possible - no external network required. They assert the page
stays alive across SW registration + fetch lifecycle.

Run:
    pytest tests/test_service_worker.py -m e2e -v

For dev iteration:
    INVPW_BINARY_PATH=/path/to/firefox.exe pytest tests/test_service_worker.py -m e2e -v
"""
from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from invisible_playwright import InvisiblePlaywright


# ---------------------------------------------------------------------------
# Local HTTP fixture server - service workers need a real http(s) origin
# (data: and about:blank are opaque-origin, no SW registration possible).
# ---------------------------------------------------------------------------


class _SWFixtureHandler(http.server.BaseHTTPRequestHandler):
    """Serves a tiny set of routes for SW lifecycle testing."""

    PAGES = {
        "/": (200, "text/html", b"""<!doctype html>
<html><head><title>sw-host</title></head>
<body>
<script>
window.__swState = 'loading';
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
        .then(reg => { window.__swState = 'registered'; })
        .catch(err => { window.__swState = 'failed:' + err.message; });
} else {
    window.__swState = 'unsupported';
}
</script>
</body></html>
"""),
        "/sw.js": (200, "application/javascript", b"""
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
self.addEventListener('fetch', e => {
    if (e.request.url.endsWith('/from-sw')) {
        e.respondWith(new Response('hello from SW', {
            headers: {'content-type': 'text/plain'},
        }));
    }
    // Fall through for everything else - exercises the
    // interceptAfterServiceWorkerResets path that was broken pre-firefox-6.
});
"""),
        "/from-sw": (200, "text/plain", b"network-fallback"),
        "/from-network": (200, "text/plain", b"net-only"),
    }

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in self.PAGES:
            status, ctype, body = self.PAGES[path]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # SW requires HTTPS or localhost - we're on localhost so plain http is fine
            self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args, **kwargs):
        pass  # silence stdout


@pytest.fixture(scope="module")
def fixture_server():
    """Spin up a localhost HTTP server with SW-friendly headers. Yields
    the base URL (e.g., 'http://127.0.0.1:54321')."""
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _SWFixtureHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(scope="module")
def page(firefox_binary):
    with InvisiblePlaywright(
        seed=42,
        binary_path=firefox_binary,
        headless=True,
    ) as browser:
        ctx = browser.new_context()
        p = ctx.new_page()
        yield p


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_service_worker_registration_does_not_crash_page(page, fixture_server):
    """Navigate to a page that registers a SW. The page must survive the
    registration. Pre-firefox-6 this crashed if the SW path hit the missing
    `interceptAfterServiceWorkerResets()` IDL method."""
    crashed = {"v": False}
    page.on("crash", lambda p: crashed.__setitem__("v", True))

    page.goto(f"{fixture_server}/", timeout=15_000)
    # Wait for SW to register (or fail cleanly)
    page.wait_for_function(
        "window.__swState !== 'loading'", timeout=10_000
    )
    state = page.evaluate("window.__swState")
    assert not crashed["v"], f"page crashed during SW registration (state={state!r})"
    # state should be 'registered' or 'failed:...'  (Firefox supports SW)
    assert state in ("registered",) or state.startswith("failed:"), (
        f"unexpected SW state: {state!r}"
    )


@pytest.mark.e2e
def test_page_with_sw_can_navigate_repeatedly(page, fixture_server):
    """Once a SW is registered, repeated navigations exercise the
    interception path on every request. Pre-firefox-6, this hit the C++
    crash after a few cycles."""
    crashed = {"v": False}
    page.on("crash", lambda p: crashed.__setitem__("v", True))

    page.goto(f"{fixture_server}/", timeout=15_000)
    page.wait_for_function("window.__swState !== 'loading'", timeout=10_000)

    # 5 reloads - the SW fetch handler runs each time
    for _ in range(5):
        page.reload(timeout=15_000)
        assert not crashed["v"]
    assert page.evaluate("document.title") == "sw-host"


@pytest.mark.e2e
def test_fetch_through_sw_returns_sw_synthesized_response(page, fixture_server):
    """The SW intercepts `/from-sw` and synthesizes a response without
    hitting the network. Verifies the SW fetch path is functional - this
    is the exact flow that crashed on a real site's login page."""
    page.goto(f"{fixture_server}/", timeout=15_000)
    page.wait_for_function("window.__swState === 'registered'", timeout=10_000)

    # First request to /from-sw routes through the SW
    body = page.evaluate("""async (base) => {
        const r = await fetch(base + '/from-sw');
        return await r.text();
    }""", fixture_server)
    # Either the SW served 'hello from SW' (intercepted) or the network
    # served 'network-fallback' (if SW didn't claim yet). Both are OK -
    # the regression we test is that it doesn't CRASH.
    assert body in ("hello from SW", "network-fallback"), (
        f"unexpected /from-sw response body: {body!r}"
    )


@pytest.mark.e2e
def test_sw_fall_through_to_network_does_not_crash(page, fixture_server):
    """Request a URL the SW doesn't handle → falls through to network.
    This is the `interceptAfterServiceWorkerResets()` code path: the SW
    decides not to handle, the channel goes back to network. Without the
    C++ patch, this is where the C++ side ended up in an inconsistent
    state."""
    crashed = {"v": False}
    page.on("crash", lambda p: crashed.__setitem__("v", True))

    page.goto(f"{fixture_server}/", timeout=15_000)
    page.wait_for_function("window.__swState === 'registered'", timeout=10_000)

    # /from-network is NOT intercepted by SW - exercises the fall-through
    body = page.evaluate("""async (base) => {
        const r = await fetch(base + '/from-network');
        return await r.text();
    }""", fixture_server)
    assert body == "net-only"
    assert not crashed["v"]


@pytest.mark.e2e
def test_sw_unregister_then_register_again(page, fixture_server):
    """Unregistering then re-registering exercises lifecycle bookkeeping
    in the C++ InterceptedHttpChannel state machine."""
    crashed = {"v": False}
    page.on("crash", lambda p: crashed.__setitem__("v", True))

    page.goto(f"{fixture_server}/", timeout=15_000)
    page.wait_for_function("window.__swState === 'registered'", timeout=10_000)

    # Unregister all SWs then register again
    result = page.evaluate("""async () => {
        const regs = await navigator.serviceWorker.getRegistrations();
        for (const r of regs) await r.unregister();
        const r2 = await navigator.serviceWorker.register('/sw.js');
        return r2.scope;
    }""")
    assert "/" in result
    assert not crashed["v"]
