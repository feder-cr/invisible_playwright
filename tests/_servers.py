"""The local HTTP server the tests serve their pages from: ONE class.

⛔ THREADED, BECAUSE A SINGLE-THREADED SERVER CAN BE HELD BY ONE CONNECTION.
`socketserver.TCPServer` and `http.server.HTTPServer` serve one connection at a
time: a connection the browser opens and leaves without a request (a
speculative connect, a keep-alive) holds the only thread, so the next request
waits behind it and `shutdown()` waits for it. That is a candidate for
[B277], a Windows e2e worker that stopped for 420 s on the screencast test, and
it was in 35 servers of 18 files, each its own copy. A thread per connection,
and daemon threads, so neither a request nor the shutdown can be held.

Same interface as the classes it replaces (`serve_forever`, `shutdown`,
`server_address`, the `with` block), so a test changes only its constructor.
Not `http.server.ThreadingHTTPServer`: its `server_bind` asks `getfqdn` for
the address, a reverse lookup that can take seconds on Windows.
"""
from __future__ import annotations

import socketserver


class LocalServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # What `http.server.HTTPServer` sets, for the tests that bind a port they
    # picked rather than 0.
    allow_reuse_address = True

    def server_bind(self):
        """`server_name` and `server_port`, which `http.server.HTTPServer`
        gives and tests read, without its `getfqdn`."""
        socketserver.ThreadingTCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


__all__ = ["LocalServer"]
