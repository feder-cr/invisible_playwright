"""Support of the DoH e2e files and of the fixtures of conftest.py that serve them (no pytest in here)."""
from __future__ import annotations

import http.server
import os
import sys
import threading

from _servers import LocalServer

def a_display_when_the_host_has_none() -> dict:
    """The launch arguments that give a browser a screen on a Linux machine that has none (a CI runner, a root
    shell under ``sudo``): ``headless=True`` is a headed browser on a virtual display the wrapper starts itself. A
    machine with a display of its own, and Windows, are left as they are."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return {"headless": True}
    return {}


#: The zone of the FakeDoh: every name a test uses, all pointing at the loopback where the page server is.
ZONE = {"page-a.test": "127.0.0.1", "page-b.test": "127.0.0.1", "page-c.test": "127.0.0.1",
        "stun-a.test": "127.0.0.1"}


class Pages:
    """A local HTTP server: every path answers a page that says which Host it was asked for."""

    def __init__(self):
        outer = self
        self.hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                host = self.headers.get("Host", "")
                outer.hits.append((host, self.path))
                body = ("<!doctype html><title>page</title><body>HOST=%s</body>" % host).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = LocalServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, name):
        return "http://%s:%d/" % (name, self.port)

    def close(self):
        self._server.shutdown()
        self._server.server_close()
