"""E2E: the patched Firefox SENDS SOCKS5 username/password and routes through it.

Playwright's own ``proxy=`` ignores SOCKS auth; this is the patched
``nsProtocolProxyService`` feature (reads ``network.proxy.socks_username`` /
``socks_password``). ``test_proxy.py`` already unit-tests on CI that the wrapper
sets those prefs; this proves the binary actually performs the RFC1929 auth
handshake and relays traffic.

Fully hermetic - a local SOCKS5 server + a local HTTP target, with the localhost
target forced through the proxy via ``allow_hijacking_localhost`` - so it runs
identically on a dev box and on a GitHub runner (no external site, no secrets).
"""
from __future__ import annotations

import http.server
import socket
import socketserver
import struct
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

_USER = "ferd_socks_user"
_PASS = "ferd_socks_pw_42"


class _Socks5AuthRecorder:
    """SOCKS5 that REQUIRES RFC1929 user/pass auth, records the creds it saw,
    then relays CONNECT to the requested target."""

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        self.seen_creds: list[tuple[str, str]] = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _recv(self, s, n):
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle(self, conn):
        try:
            head = self._recv(conn, 2)
            if not head or head[0] != 0x05:
                conn.close(); return
            methods = self._recv(conn, head[1]) or b""
            if 0x02 not in methods:               # we REQUIRE user/pass
                conn.sendall(b"\x05\xff"); conn.close(); return
            conn.sendall(b"\x05\x02")             # select user/pass auth
            if not self._recv(conn, 1):           # RFC1929 version byte
                conn.close(); return
            ulen = self._recv(conn, 1)[0]
            uname = (self._recv(conn, ulen) or b"").decode("utf-8", "ignore")
            plen = self._recv(conn, 1)[0]
            passwd = (self._recv(conn, plen) or b"").decode("utf-8", "ignore")
            self.seen_creds.append((uname, passwd))
            conn.sendall(b"\x01\x00")             # auth success
            req = self._recv(conn, 4)
            if not req:
                conn.close(); return
            _, cmd, _, atyp = req
            if atyp == 0x01:
                addr = socket.inet_ntoa(self._recv(conn, 4))
            elif atyp == 0x03:
                addr = (self._recv(conn, self._recv(conn, 1)[0]) or b"").decode()
            elif atyp == 0x04:
                addr = socket.inet_ntop(socket.AF_INET6, self._recv(conn, 16))
            else:
                conn.close(); return
            port = struct.unpack("!H", self._recv(conn, 2))[0]
            if cmd != 0x01:                        # only CONNECT
                conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00"); conn.close(); return
            try:
                up = socket.create_connection((addr, port), timeout=15)
            except OSError:
                conn.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00"); conn.close(); return
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            self._pipe(conn, up)
        except Exception:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _pipe(a, b):
        def fwd(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        threading.Thread(target=fwd, args=(a, b), daemon=True).start()
        fwd(b, a)

    def close(self):
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


class _LocalHTTP:
    """A tiny localhost HTTP server - the CONNECT target relayed by the proxy."""

    _HTML = b"<!doctype html><title>ok</title><h1 id=ok>socks-routed</h1>"

    def __init__(self):
        html = self._HTML

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *a):
                pass

        self._srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self):
        self._srv.shutdown()


@pytest.fixture
def socks_auth():
    s = _Socks5AuthRecorder()
    yield s
    s.close()


@pytest.fixture
def local_http():
    h = _LocalHTTP()
    yield h
    h.close()


@pytest.mark.e2e
def test_socks5_auth_creds_sent_and_routed(firefox_binary, socks_auth, local_http):
    """The binary must perform SOCKS5 user/pass auth with the configured creds
    and relay the page through the proxy."""
    proxy = {
        "server": f"socks5://127.0.0.1:{socks_auth.port}",
        "username": _USER,
        "password": _PASS,
    }
    # Firefox bypasses the proxy for localhost by default; force it through.
    prefs = {
        "network.proxy.allow_hijacking_localhost": True,
        "network.proxy.no_proxies_on": "",
    }
    with InvisiblePlaywright(
        seed=42, binary_path=firefox_binary, proxy=proxy, extra_prefs=prefs
    ) as browser:
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{local_http.port}/", wait_until="load", timeout=30000)
        text = page.evaluate("() => document.getElementById('ok').textContent")

    assert text == "socks-routed", "page did not load through the SOCKS proxy"
    assert (_USER, _PASS) in socks_auth.seen_creds, (
        f"patched Firefox did not send the SOCKS5 auth creds from prefs; "
        f"proxy saw: {socks_auth.seen_creds!r}"
    )
