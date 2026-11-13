"""The page work of the "nothing leaves but the proxy" e2e: the names, the pages and the server (no pytest in here).

A page can make a browser do much more than load a document: hints (``dns-prefetch``, ``preconnect``, ``prefetch``, ``preload``), an
image, a ``fetch``, a WebSocket, an EventSource, a WebRTC gather with a STUN and a TURN server, a geolocation request, a service worker
with push, and a page still loading when its context is closed. Each of them is tried in a context of its own behind a SOCKS5 gateway,
and what the test asks is where the browser went: no question to the resolver of the machine, no direct connection, every name asked
to the DoH THROUGH the gateway of the context, every CONNECT by an ADDRESS.

The names are the zone of a ``FakeDoh``. The pages are served from 127.0.0.1 and reached by a name (``<case>-site.test``) that points
there. Every name the page work uses points at a DOCUMENTATION address (RFC 5737, 198.51.100.0/24), never at loopback, for two reasons:
a connection a browser opens straight to it is seen by the leak watcher (a loopback one never is), and the gateway of the tests refuses
a CONNECT to a non-loopback address and lists it, which is how a test sees that the browser CONNECTed by the address the DoH gave.
"""
from __future__ import annotations

import http.server
import threading
from typing import Dict, List, Optional

from _servers import LocalServer

#: The zone of the page work: name -> the documentation address a lookup of it answers.
ROUTABLE: Dict[str, str] = {
    "prefetch-a.test": "198.51.100.21", "preconnect-b.test": "198.51.100.22", "prefetch-c.test": "198.51.100.23",
    "preload-d.test": "198.51.100.24",
    "img-e.test": "198.51.100.25", "fetch-f.test": "198.51.100.26", "ws-g.test": "198.51.100.27", "es-h.test": "198.51.100.28",
    "stun-i.test": "198.51.100.29", "turn-j.test": "198.51.100.30",
}

#: The pages' own names: they point at the local server. ``push`` is served by its address (a service worker needs a secure
#: context, and ``.test`` is not one; 127.0.0.1 is).
SITES: Dict[str, str] = {"hints-site.test": "127.0.0.1", "subresources-site.test": "127.0.0.1", "webrtc-site.test": "127.0.0.1",
                         "geolocation-site.test": "127.0.0.1", "closing-site.test": "127.0.0.1"}

ZONE: Dict[str, str] = {**SITES, **ROUTABLE}

#: What each case's page does. ``%`` is not used: the names are written out, the page of a case is a constant.
CASES: Dict[str, str] = {
    "hints": '<link rel=dns-prefetch href="//prefetch-a.test"><link rel=preconnect href="http://preconnect-b.test">'
             '<link rel=prefetch href="http://prefetch-c.test/x"><link rel=preload as=image href="http://preload-d.test/i.png">',
    "subresources": '<img src="http://img-e.test/i.png"><script>fetch("http://fetch-f.test/x").catch(() => {});'
                    'try { new WebSocket("ws://ws-g.test/s").onerror = () => {}; } catch (e) {}'
                    'try { new EventSource("http://es-h.test/s").onerror = function () { this.close(); }; } catch (e) {}</script>',
    "webrtc": '<script>(async () => { const pc = new RTCPeerConnection({iceServers: [{urls: "stun:stun-i.test:3478"},'
              '{urls: "turn:turn-j.test:3478", username: "u", credential: "p"}]});'
              'pc.createDataChannel("d"); await pc.setLocalDescription(await pc.createOffer()); })().catch(() => {});</script>',
    "geolocation": '<script>try { navigator.geolocation.getCurrentPosition(() => {}, () => {}, {timeout: 4000}); } catch (e) {}</script>',
    # a page still loading when its context is closed: requests made between the close and the death of its processes. These names
    # are in no zone: the DoH answers NXDOMAIN, and what is asked is where the question goes.
    "closing": '<script>let n = 0; setInterval(() => { for (let k = 0; k < 3; k++) fetch("http://late-" + (n++) + ".test/x")'
               '.catch(() => {}); }, 5);</script>',
    # `__pushStage` says how far the page got (registering, registered, ready, subscription-read): a page that stays `pending` names the step it
    # is stuck in.
    "push": '<script>window.__push = "pending"; window.__pushStage = "start"; (async () => { if (!navigator.serviceWorker) { window.__push = "no-service-worker"; return; }'
            'window.__pushStage = "registering"; const r = await navigator.serviceWorker.register("/sw.js"); window.__pushStage = "registered";'
            'await navigator.serviceWorker.ready; window.__pushStage = "ready";'
            # The parent process listens for the content process's push messages only once its PushServiceParent exists (dom/push/PushComponents.sys.mjs,
            # PushServiceBase -> _addListeners), and that is created by an IDLE task of the first window (BrowserGlue "PushService.ensureReady"): a request
            # sent before it is dropped, never queued, so the call is repeated until it is answered (as a page that retries would).
            'let sub; for (let i = 1; i <= 15 && sub === undefined; i++) { window.__pushTries = i; sub = await Promise.race([r.pushManager.getSubscription(),'
            ' new Promise(res => setTimeout(() => res(undefined), 2000))]); }'
            'if (sub === undefined) return; window.__push = "sub:" + JSON.stringify(sub); window.__pushStage = "subscription-read";'
            'await r.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: new Uint8Array(65).fill(4)});'
            '})().catch(e => { window.__push = String(window.__push).startsWith("sub:") ? window.__push : "error:" + e.name; });</script>',
}

#: The names the DoH must be asked for, by case, when the work is done (a hint is speculative: the browser may skip it, so none is demanded).
ASKED: Dict[str, List[str]] = {
    "hints": [], "subresources": ["img-e.test", "fetch-f.test", "ws-g.test", "es-h.test"], "webrtc": ["stun-i.test", "turn-j.test"],
    "geolocation": [], "closing": ["late-0.test"], "push": [],
}

#: How long the page is left to work before the context is closed, in seconds: a case that does little is closed sooner.
SETTLE_SECONDS: Dict[str, float] = {"hints": 6.0, "subresources": 6.0, "webrtc": 6.0, "geolocation": 6.0, "closing": 1.5, "push": 6.0}

SW = b"self.addEventListener('push', () => {});"


def site_name(case: str) -> Optional[str]:
    """The name a case's page is reached by, or None for the one reached by its address."""
    name = "%s-site.test" % case
    return name if name in SITES else None


def is_documentation_address(address: str) -> bool:
    """True for an address of 198.51.100.0/24 (TEST-NET-2)."""
    return address.startswith("198.51.100.")


class CaseSite:
    """A local HTTP server: ``/<case>`` answers the page of that case, ``/sw.js`` a service worker with a push listener."""

    def __init__(self):
        outer = self
        self.hits: List[tuple] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                outer.hits.append((self.headers.get("Host", ""), path))
                if path == "/sw.js":
                    body, kind = SW, "application/javascript"
                elif path.lstrip("/") in CASES:
                    body, kind = ("<!doctype html><meta charset=utf-8><title>%s</title>%s" % (path.lstrip("/"), CASES[path.lstrip("/")])
                                  ).encode(), "text/html; charset=utf-8"
                else:
                    body, kind = b"", "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = LocalServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, case: str) -> str:
        name = site_name(case)
        return "http://%s:%d/%s" % (name or "127.0.0.1", self.port, case)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
