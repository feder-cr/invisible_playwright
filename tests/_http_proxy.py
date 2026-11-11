"""An HTTP proxy on 127.0.0.1 that asks for credentials, and carries the DoH road to a :class:`FakeDoh`.

The counterpart of :class:`_doh_servers.DohGateway` for the ``http`` scheme:

* a request without a valid ``Proxy-Authorization: Basic`` gets ``407`` with ``Proxy-Authenticate: Basic`` (the
  connection is closed, as many real proxies do); the right credentials get through;
* ``CONNECT`` to the DoH address (``162.159.61.4:443`` or ``mozilla.cloudflare-dns.com:443``) is answered ``200``
  and handed to the FakeDoh with ``via=name``; ``CONNECT`` to a loopback address is relayed for real; any other
  CONNECT is refused (``403``) and recorded;
* a plain ``GET http://host:port/path`` (the form a browser sends to an HTTP proxy for an ``http`` page: the NAME
  travels to the proxy, the proxy is the one that resolves it) is forwarded for real to a loopback address. The
  proxy "resolves" the name with the zone of the FakeDoh (``doh.names``) and never asks any other resolver; a name
  that is not in the zone answers ``502``. That is the exit's own resolution, and it is why a page behind an HTTP
  proxy does NOT make the browser ask DoH for its name: something else must (an ICE server name does);
* every request is recorded in :attr:`seen` with the ``Proxy-Authorization`` header it carried and the status given.

Hermetic: nothing leaves the loopback and no name is resolved by the operating system.
"""
from __future__ import annotations

import base64
import socket
import threading
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlsplit

from _doh_servers import DOH_TARGETS, FakeDoh, is_loopback, relay

_REASONS = {200: "Connection Established", 403: "Forbidden", 405: "Method Not Allowed",
            407: "Proxy Authentication Required", 502: "Bad Gateway"}


def basic(user: str, password: str) -> str:
    """The ``Proxy-Authorization`` value of Basic credentials (UTF-8, RFC 7617)."""
    return "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")


class Seen(NamedTuple):
    method: str
    host: str
    port: int
    #: The Proxy-Authorization header as it came, None when the request had none.
    authorization: Optional[str]
    status: int


class HttpProxy:
    """See the module docstring. ``user=None`` means no authentication is asked."""

    def __init__(self, name: str, doh: FakeDoh, *, user: Optional[str] = None, password: Optional[str] = None):
        self.name = name
        self.doh = doh
        self.user, self.password = user, password
        #: Every request, in order.
        self.seen: List[Seen] = []
        self._lock = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self.port: int = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    # --- what the test hands to the product ---------------------------------------------------------------------
    @property
    def server(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    @property
    def proxy(self) -> dict:
        """The endpoint dict the core takes: ``{"server": ..., [username, password]}``."""
        out = {"server": self.server}
        if self.user is not None:
            out.update(username=self.user, password=self.password)
        return out

    @property
    def expected_authorization(self) -> Optional[str]:
        return basic(self.user, self.password) if self.user is not None else None

    # --- what the test reads ------------------------------------------------------------------------------------
    @property
    def connects(self) -> List[Tuple[str, int]]:
        """The targets of the CONNECTs that were accepted, in order."""
        with self._lock:
            return [(s.host, s.port) for s in self.seen if s.method == "CONNECT" and s.status == 200]

    @property
    def refused_for_credentials(self) -> List[Seen]:
        with self._lock:
            return [s for s in self.seen if s.status == 407]

    def authorizations_for(self, host: str, port: int) -> List[Optional[str]]:
        """The Proxy-Authorization of every CONNECT to ``host:port`` (the DoH address, for instance)."""
        with self._lock:
            return [s.authorization for s in self.seen if s.method == "CONNECT" and (s.host, s.port) == (host, port)]

    def doh_authorizations(self) -> List[Optional[str]]:
        with self._lock:
            return [s.authorization for s in self.seen if s.method == "CONNECT" and (s.host, s.port) in DOH_TARGETS]

    def doh_connects(self) -> List[Tuple[str, int]]:
        return [t for t in self.connects if t in DOH_TARGETS]

    # --- serving ------------------------------------------------------------------------------------------------
    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass

    def __enter__(self) -> "HttpProxy":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _accept(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _answer(conn: socket.socket, status: int, extra: str = "") -> None:
        conn.sendall(("HTTP/1.1 %d %s\r\n%sContent-Length: 0\r\n%s\r\n" % (
            status, _REASONS.get(status, "Status"), extra,
            "Connection: close\r\n" if status != 200 else "")).encode("latin-1"))

    def _record(self, method: str, host: str, port: int, authorization: Optional[str], status: int) -> None:
        with self._lock:
            self.seen.append(Seen(method, host, port, authorization, status))

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _version = lines[0].split(" ", 2)
            headers: Dict[str, str] = {}
            for line in lines[1:]:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            authorization = headers.get("proxy-authorization")
            if method == "CONNECT":
                host, _, port_text = target.rpartition(":")
                host, port = host.strip("[]"), int(port_text)
            else:
                split = urlsplit(target)
                host, port = split.hostname or "", split.port or 80
            if self.user is not None and authorization != self.expected_authorization:
                self._record(method, host, port, authorization, 407)
                self._answer(conn, 407, 'Proxy-Authenticate: Basic realm="invisible"\r\n')
                return
            if method == "CONNECT":
                self._connect(conn, host, port, authorization)
            elif method == "GET":
                self._forward(conn, host, port, authorization, split, headers, rest)
            else:
                self._record(method, host, port, authorization, 405)
                self._answer(conn, 405)
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _connect(self, conn: socket.socket, host: str, port: int, authorization: Optional[str]) -> None:
        if (host, port) in DOH_TARGETS:
            self._record("CONNECT", host, port, authorization, 200)
            self._answer(conn, 200)
            self.doh.serve(conn, self.name)
            return
        if not is_loopback(host):
            self._record("CONNECT", host, port, authorization, 403)
            self._answer(conn, 403)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            self._record("CONNECT", host, port, authorization, 502)
            self._answer(conn, 502)
            return
        self._record("CONNECT", host, port, authorization, 200)
        self._answer(conn, 200)
        try:
            relay(conn, upstream)
        finally:
            upstream.close()

    def _forward(self, conn: socket.socket, host: str, port: int, authorization: Optional[str], split,
                 headers: Dict[str, str], rest: bytes) -> None:
        """A plain GET, in the absolute form: resolved by the zone of the FakeDoh, then fetched for real."""
        address = host if is_loopback(host) else self.doh.names.get(host.lower(), "")
        if not address or not is_loopback(address):
            self._record("GET", host, port, authorization, 502)
            self._answer(conn, 502)
            return
        try:
            upstream = socket.create_connection((address, port), timeout=10)
        except OSError:
            self._record("GET", host, port, authorization, 502)
            self._answer(conn, 502)
            return
        self._record("GET", host, port, authorization, 200)
        try:
            path = (split.path or "/") + ("?" + split.query if split.query else "")
            passed = "".join("%s: %s\r\n" % (k, v) for k, v in headers.items()
                             if not k.startswith("proxy-") and k not in ("connection", "keep-alive"))
            upstream.sendall(("GET %s HTTP/1.1\r\n%sConnection: close\r\n\r\n" % (path, passed)).encode("latin-1"))
            conn.settimeout(None)
            upstream.settimeout(None)
            while True:
                data = upstream.recv(65536)
                if not data:
                    break
                conn.sendall(data)
        finally:
            upstream.close()


__all__ = ["HttpProxy", "Seen", "basic"]
