"""A browser launched without a session is still a session, on the engine (see the unit file of the same name).

A bare `firefox.launch()` on the server's client (`invisible_playwright._pw`, the client Playwright's own
suite drives) wrote no prefs of the core: the engine came up with Mozilla's defaults. On Linux its first
window then held the first-run policy tab next to the page's (the Terms of Use, which the core declares
accepted, were not), and the first `new_page` failed with "Browser.newPage: Unexpected number of tabs in
the new window: 2", 15 times in 15 on firefox-61 and on the firefox-62 candidate. Every value a page read
was the engine's own default, not a persona: the Linux engine said "X11; Linux" where every session says
Windows.

And the remote transport (`launch_server`, what a remote client drives): every context a client opens on the
served browser lives in the profile the server composed, so each says the same Windows machine.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

_READ = "() => [navigator.userAgent, navigator.platform, navigator.hardwareConcurrency]"


def test_a_bare_launch_has_one_tab_and_a_persona(firefox_binary):
    from invisible_playwright._pw.sync_api import sync_playwright

    for attempt in range(3):     # the policy tab came in every first window: three are three chances
        with sync_playwright() as p:
            browser = p.firefox.launch(executable_path=firefox_binary, headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()        # refused before: "Unexpected number of tabs: 2"
                agent, platform, cores = page.evaluate(_READ)
                assert len(context.pages) == 1
                assert "Windows NT" in agent and platform == "Win32", (
                    "a bare launch is the engine's defaults, not a session: %r %r" % (agent, platform))
                context.close()
            finally:
                browser.close()


async def _two_contexts(endpoint):
    """Two contexts on the served browser, a page in each, what each page reads; a client written on
    `websockets` alone, as in test_a_served_browser_answers_a_plain_client_e2e.py."""
    import json
    from websockets.asyncio.client import connect

    objects, counter = {}, [0]
    async with connect(endpoint, max_size=None) as ws:
        async def call(guid, method, params=None):
            counter[0] += 1
            await ws.send(json.dumps({"id": counter[0], "guid": guid, "method": method,
                                      "params": params or {}, "metadata": {}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("method") == "__create__":
                    objects[m["params"]["guid"]] = m["params"]
                if m.get("id") == counter[0]:
                    assert "error" not in m, m
                    return m.get("result")

        await call("", "initialize", {"sdkLanguage": "python"})
        browser = [g for g, p in objects.items() if p["type"] == "Browser"][0]
        seen = []
        for _ in range(2):
            ctx = (await call(browser, "newContext", {}))["context"]["guid"]
            page = (await call(ctx, "newPage", {}))["page"]["guid"]
            frame = objects[page]["initializer"]["mainFrame"]["guid"]
            value = (await call(frame, "evaluateExpression", {
                "expression": "() => JSON.stringify([navigator.userAgent, navigator.platform, "
                              "navigator.hardwareConcurrency])",
                "isFunction": True, "arg": {"value": {"v": "undefined"}, "handles": []}}))["value"]
            seen.append(json.loads(value["s"]))
            await call(ctx, "close", {})
        return seen


def test_every_context_of_a_served_browser_is_the_session(firefox_binary):
    import asyncio

    import websockets  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    from invisible_playwright._juggler.remote import launch_server

    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US",
                           timezone="America/New_York")
    try:
        seen = asyncio.run(asyncio.wait_for(_two_contexts(server.ws_endpoint), 120))
    finally:
        server.close()
    assert seen[0] == seen[1], "two contexts of one served session are two machines: %r" % seen
    agent, platform, _ = seen[0]
    assert "Windows NT" in agent and platform == "Win32", seen[0]


def test_a_bare_launch_behind_a_proxy_is_composed_through_it(firefox_binary, monkeypatch):
    """A bare launch behind a proxy gets a session composed THROUGH that proxy (the egress, the timezone and
    the locale measured there, the network declared from the seed), and the page's WebRTC gather still
    completes with mDNS hosts. The declaration (STEALTHFOX_WEBRTC_NETWORK) is read by the new engine only;
    its value is asserted without a browser in test_the_network_declaration_reaches_the_engine.py. The
    proxy is the local TCP-only SOCKS5 of test_webrtc_realness.py; the egress the session measures through
    it is answered with a TEST-NET address (RFC 5737), as in those tests."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    from invisible_core import decide_session_locale
    from invisible_core._geo import SessionGeo
    from invisible_playwright import _session
    from invisible_playwright._pw.sync_api import sync_playwright
    from _socks_servers import Socks5TcpOnly
    from test_webrtc_realness import _PROBE_JS, candidates, host_is_mdns

    egress = "203.0.113.7"
    monkeypatch.delenv("STEALTHFOX_WEBRTC_NETWORK", raising=False)
    measured = []

    def through_the_proxy(timezone, proxy, locale, **_kw):
        measured.append(proxy)
        return SessionGeo("America/New_York", egress,
                          locale=decide_session_locale("en-US"))

    monkeypatch.setattr(_session, "session_geo", through_the_proxy, raising=False)  # absent before the fix

    class Page(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>wrtc</body></html>")

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    socks = Socks5TcpOnly()
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(executable_path=firefox_binary, headless=True,
                                       proxy={"server": "socks5://127.0.0.1:%d" % socks.port})
            try:
                page = browser.new_context().new_page()
                page.goto("http://127.0.0.1:%d/" % httpd.server_address[1], wait_until="domcontentloaded")
                res = page.evaluate(_PROBE_JS)
            finally:
                browser.close()
    finally:
        socks.close()
        httpd.shutdown()
    cands = candidates(res["candidates"])
    assert cands and host_is_mdns(cands), "behind the proxy the gather is blocked or leaks a host: %s" % res["candidates"]
    assert measured and measured[0] and measured[0].get("server", "").startswith("socks5://"), (
        "the session was not composed through the launch's proxy: %r" % measured)
