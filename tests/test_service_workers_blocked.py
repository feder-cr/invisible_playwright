"""Per-context worker blocking, with native APIs and working request routes.

The engine blocks them (`Browser.setServiceWorkersBlocked`, firefox-37 on):
no page script, no global preference. All traffic is loopback.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

pytestmark = pytest.mark.e2e

_REGISTER = """async () => {
    try {
        await navigator.serviceWorker.register('/sw.js');
        return {registered: true};
    } catch (e) {
        return {name: e.name, message: e.message};
    }
}"""

_SURFACE = """() => ({
    source: navigator.serviceWorker.register.toString(),
    own: Object.getOwnPropertyNames(navigator.serviceWorker),
    prototype: Object.getOwnPropertyNames(ServiceWorkerContainer.prototype)
})"""

_MESSAGE = """async () => {
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration || !registration.active)
        return 'no worker';
    return new Promise(resolve => {
        const channel = new MessageChannel();
        channel.port1.onmessage = e => resolve(e.data);
        registration.active.postMessage('post', [channel.port2]);
    });
}"""


@pytest.fixture
def sw_server():
    hits = []
    state = {"network_error": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(("GET", self.path))
            if self.path == "/sw.js" and state["network_error"]:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            scripts = {
                "/sw.js": b"""
                    importScripts('/sw-import.js');
                    self.addEventListener('install',
                        e => e.waitUntil(self.skipWaiting()));
                    self.addEventListener('activate',
                        e => e.waitUntil(self.clients.claim()));
                    self.addEventListener('fetch', e => {
                        if (new URL(e.request.url).pathname === '/sw-answer')
                            e.respondWith(Promise.resolve(new Response('worker')));
                    });
                    self.addEventListener('message', e => {
                        e.waitUntil(fetch('/sw-post', {method: 'POST', body: 'sw'})
                            .then(r => e.ports[0].postMessage(r.status))
                            .catch(() => e.ports[0].postMessage('failed')));
                    });
                """,
                "/sw-import.js": b"self.imported = true;",
                "/worker.js": b"importScripts('/worker-import.js');",
                "/worker-import.js": b"self.postMessage('ordinary worker');",
            }
            body = scripts.get(self.path, b"""<!doctype html><title>network</title>
                <script>
                window.initialController = !!navigator.serviceWorker.controller;
                window.initialRegistrations = navigator.serviceWorker
                    .getRegistrations().then(regs => regs.map(r => r.scope));
                </script>""")
            if self.path == "/sw-answer":
                body = b"network"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript"
                             if self.path in scripts else "text/html")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            hits.append(("POST", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _browser(binary, **kwargs):
    return InvisiblePlaywright(
        seed=42, binary_path=binary, headless=True,
        locale="en-US", timezone="UTC", **kwargs)


class _Persistent(InvisiblePlaywright):
    def __init__(self, binary, profile, *, block):
        super().__init__(
            seed=42, binary_path=binary, headless=True, profile_dir=profile,
            locale="en-US", timezone="UTC")
        self._block = block

    def _persistent_context_kwargs(self):
        options = super()._persistent_context_kwargs()
        if self._block:
            options["service_workers"] = "block"
        return options


def _guard(context):
    routed = []

    def guard(route):
        routed.append((route.request.method, route.request.url))
        if route.request.method == "POST":
            route.abort()
        else:
            route.continue_()

    context.route("**/*", guard)
    return routed


def _assert_blocked_page(page, origin, hits, routed):
    assert page.evaluate("window.initialController") is False
    assert page.evaluate("window.initialRegistrations") == []
    assert page.evaluate("navigator.serviceWorker.controller") is None
    assert page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistrations()).length") == 0
    assert page.evaluate("async () => (await fetch('/sw-answer')).text()") == "network"
    assert ("GET", origin + "/sw-answer") in routed
    assert ("GET", "/sw-answer") in hits
    for path in ("/page-post", "/sw-answer"):
        assert not page.evaluate("""async path => {
            try {
                await fetch(path, {method: 'POST', body: 'page'});
                return true;
            } catch { return false; }
        }""", path)
        assert ("POST", origin + path) in routed
        assert ("POST", path) not in hits
    assert page.evaluate(_MESSAGE) == "no worker"
    assert ("POST", "/sw-post") not in hits


def _install(page, origin):
    page.goto(origin)
    assert page.evaluate(_REGISTER) == {"registered": True}
    page.wait_for_function("navigator.serviceWorker.controller !== null",
                           timeout=10_000)
    assert page.evaluate("async () => (await fetch('/sw-answer')).text()") == "worker"
    assert page.evaluate(_MESSAGE) == 204


def test_block_rejects_registration_and_keeps_routes(firefox_binary, sw_server):
    origin, hits, _ = sw_server
    with _browser(firefox_binary) as browser:
        context = browser.new_context(service_workers="block")
        routed = _guard(context)
        page = context.new_page()
        page.goto(origin)
        for _ in range(3):
            error = page.evaluate(_REGISTER)
            assert "registered" not in error, error
        assert ("GET", "/sw.js") not in hits
        assert ("GET", "/sw-import.js") not in hits
        _assert_blocked_page(page, origin, hits, routed)


def test_persistent_block_removes_saved_workers(firefox_binary, sw_server, tmp_path):
    origin, hits, _ = sw_server
    profile = tmp_path / "profile"
    with _Persistent(firefox_binary, profile, block=False) as context:
        _install(context.new_page(), origin)
        assert ("POST", "/sw-post") in hits
    assert (origin + "/sw.js") in (
        profile / "serviceworker.txt").read_text(encoding="utf-8")
    hits.clear()

    with _Persistent(firefox_binary, profile, block=True) as context:
        routed = _guard(context)
        page = context.new_page()
        page.goto(origin)
        _assert_blocked_page(page, origin, hits, routed)
        assert "registered" not in page.evaluate(_REGISTER)
        assert ("GET", "/sw.js") not in hits

    # Cleanup must survive another restart, not just hide the old registration.
    with _Persistent(firefox_binary, profile, block=False) as context:
        page = context.new_page()
        page.goto(origin)
        assert page.evaluate(
            "async () => (await navigator.serviceWorker.getRegistrations()).length"
        ) == 0
        assert page.evaluate("navigator.serviceWorker.controller") is None
    assert ("POST", "/sw-post") not in hits


def test_block_has_native_surface_and_stock_network_error(
        firefox_binary, sw_server, record_property):
    origin, hits, state = sw_server
    # The reference is the same engine WITHOUT the block, where sw.js fails on
    # the network: a blocked register() must be indistinguishable from it.
    state["network_error"] = True
    with _browser(firefox_binary) as browser:
        page = browser.new_context().new_page()
        page.goto(origin)
        native_surface = page.evaluate(_SURFACE)
        native_error = page.evaluate(_REGISTER)
        assert "registered" not in native_error
    assert ("GET", "/sw.js") in hits
    state["network_error"] = False
    hits.clear()

    with _browser(firefox_binary) as browser:
        page = browser.new_context(service_workers="block").new_page()
        page.goto(origin)
        surface = page.evaluate(_SURFACE)
        assert "[native code]" in surface["source"]
        assert surface == native_surface
        blocked_error = page.evaluate(_REGISTER)
        record_property("stock_error", json.dumps(native_error))
        record_property("blocked_error", json.dumps(blocked_error))
        record_property("native_surface", json.dumps(surface))
        assert blocked_error == native_error
        assert ("GET", "/sw.js") not in hits


def test_block_is_context_local_and_keeps_ordinary_workers(firefox_binary, sw_server):
    origin, hits, _ = sw_server
    with _browser(firefox_binary) as browser:
        allowed = browser.new_context()
        allowed_page = allowed.new_page()
        _install(allowed_page, origin)
        blocked = browser.new_context(service_workers="block")
        page = blocked.new_page()
        page.goto(origin)
        assert "registered" not in page.evaluate(_REGISTER)
        assert page.evaluate("""() => new Promise((resolve, reject) => {
            const worker = new Worker('/worker.js');
            worker.onmessage = e => { worker.terminate(); resolve(e.data); };
            worker.onerror = e => { worker.terminate(); reject(e.message); };
        })""") == "ordinary worker"
        assert allowed_page.evaluate(
            "async () => (await fetch('/sw-answer')).text()") == "worker"
        allowed_page.evaluate("""async () => {
            for (const reg of await navigator.serviceWorker.getRegistrations())
                await reg.unregister();
        }""")
        _install(allowed_page, origin)
        blocked.close()
        assert allowed_page.evaluate(_MESSAGE) == 204
        assert ("GET", "/worker-import.js") in hits
