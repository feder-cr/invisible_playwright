"""A DNS-over-HTTPS server and a SOCKS5 gateway that carries the DoH road, for tests that need a real wire.

Hermetic: loopback only, no DNS, no internet. Nothing here knows about the product.

What a proxied session declares (core-19) is that every name the browser resolves goes to the DoH of Firefox
(``network.trr.mode`` 3), at ``https://mozilla.cloudflare-dns.com/dns-query``, found with no DNS at the address
``network.trr.bootstrapAddr`` (162.159.61.4), and that the DoH request is a channel like any other, so it leaves
THROUGH the proxy of the context of the lookup. A test of that needs three things this file builds:

* :class:`FakeDoh` - the DoH server. RFC 8484 (POST ``application/dns-message`` and GET ``?dns=base64url``) over TLS
  with a certificate for ``mozilla.cloudflare-dns.com`` and ``127.0.0.1``, written by hand: the question is parsed from
  its labels and the answer is built byte by byte (header, the question copied, one A record, TTL 30). It answers
  HTTP/2 when the ``h2`` package is importable and ALPN selects it (Firefox's TRR speaks h2), HTTP/1.1 otherwise.
  Every query is recorded as ``(name, qtype, via)``.
* :class:`DohGateway` - a SOCKS5 gateway (the ``Gateway`` of ``_socks_servers``) whose CONNECT to the DoH address
  (``162.159.61.4:443``, or ``mozilla.cloudflare-dns.com:443`` by name) is handed to the FakeDoh of the test, with the
  gateway's name as ``via``; every other CONNECT to a loopback address is relayed for real. It never resolves a name
  itself and never reaches a non-loopback address: a CONNECT that asks for one is refused and recorded, so a test run
  cannot leak anything and cannot be mistaken for leaking.
* the DNS codec (:func:`parse_query`, :func:`build_response`), which the resolver trap of ``_leaks`` reuses.

``via`` answers "by which road did this query arrive": the name of the gateway that carried the CONNECT, or
``"direct"`` for a client that connected to the FakeDoh's own port.
"""
from __future__ import annotations

import base64
import datetime
import ipaddress
import socket
import ssl
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

from _socks_servers import EXIT, Gateway, WEB_NAME, WHOAMI4, WHOAMI6

try:  # the HTTP/2 side needs it; a test that wants h2 says pytest.importorskip("h2")
    import h2.config
    import h2.connection
    import h2.events
    import h2.exceptions
    H2_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    H2_AVAILABLE = False

#: The host name of the DoH the session declares, and the address it is found at without DNS.
DOH_HOST = "mozilla.cloudflare-dns.com"
DOH_BOOTSTRAP = "162.159.61.4"
DOH_PORT = 443
DOH_PATH = "/dns-query"
#: Every CONNECT target the gateways hand to the FakeDoh.
DOH_TARGETS = frozenset({(DOH_BOOTSTRAP, DOH_PORT), (DOH_HOST, DOH_PORT)})

QTYPE_A, QTYPE_AAAA = 1, 28
RCODE_OK, RCODE_NXDOMAIN, RCODE_REFUSED = 0, 3, 5
DOH_TTL = 30


# --------------------------------------------------------------------------------------------------------- DNS codec
class Query(NamedTuple):
    ident: int
    flags: int
    name: str            # lower case, no trailing dot
    qtype: int
    qclass: int
    question: bytes      # the question section as it came, to be copied into the answer


def parse_query(msg: bytes) -> Query:
    """The first (and only) question of a DNS message. Raises ``ValueError`` on anything that is not one."""
    try:
        ident, flags, qdcount, _an, _ns, _ar = struct.unpack("!6H", msg[:12])
        if qdcount != 1:
            raise ValueError("expected one question, got %d" % qdcount)
        i, labels = 12, []
        while True:
            length = msg[i]
            i += 1
            if length == 0:
                break
            if length & 0xC0:
                raise ValueError("a compressed name in the question")
            labels.append(msg[i:i + length].decode("ascii"))
            i += length
        qtype, qclass = struct.unpack("!HH", msg[i:i + 4])  # struct.error when truncated
        i += 4
    except (IndexError, struct.error, UnicodeDecodeError) as exc:
        raise ValueError("not a DNS query: %s" % exc) from None
    return Query(ident, flags, ".".join(labels).lower(), qtype, qclass, msg[12:i])


def build_response(query: Query, rcode: int = RCODE_OK, answers: Tuple[Tuple[int, bytes], ...] = ()) -> bytes:
    """A response to ``query``: QR+RA, the RD bit copied, ``rcode``, the question copied, one record per
    ``(type, rdata)`` with the name as a pointer to the question and TTL 30."""
    flags = 0x8000 | (query.flags & 0x0100) | 0x0080 | (rcode & 0xF)
    out = struct.pack("!6H", query.ident, flags, 1, len(answers), 0, 0) + query.question
    for rtype, rdata in answers:
        out += struct.pack("!HHHIH", 0xC00C, rtype, 1, DOH_TTL, len(rdata)) + rdata
    return out


def encode_query(name: str, qtype: int = QTYPE_A, ident: int = 0) -> bytes:
    """A query message (RD set), the way a client builds it."""
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.rstrip(".").split(".") if p)
    return struct.pack("!6H", ident, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack("!HH", qtype, 1)


def answer_for(names: Dict[str, str], query: Query) -> bytes:
    """The response a zone made of ``names`` (name -> address) gives: A for an IPv4 value, AAAA for an IPv6 one,
    NOERROR with no answer for any other type of a known name, NXDOMAIN for an unknown name."""
    value = names.get(query.name)
    if value is None:
        return build_response(query, RCODE_NXDOMAIN)
    address = ipaddress.ip_address(value)
    if query.qtype == QTYPE_A and address.version == 4:
        return build_response(query, RCODE_OK, ((QTYPE_A, address.packed),))
    if query.qtype == QTYPE_AAAA and address.version == 6:
        return build_response(query, RCODE_OK, ((QTYPE_AAAA, address.packed),))
    return build_response(query, RCODE_OK)


def wait_until(predicate: Callable[[], object], timeout: float, step: float = 0.05) -> bool:
    """True as soon as ``predicate()`` is truthy, False after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


# ------------------------------------------------------------------------------------------------------ certificate
def make_certificate(directory: Path, dns_names: Tuple[str, ...] = (DOH_HOST,),
                     ip_addresses: Tuple[str, ...] = ("127.0.0.1",)) -> Tuple[Path, Path]:
    """A self-signed certificate (EC P-256, 30 days) whose SAN covers ``dns_names`` and ``ip_addresses``.
    Returns (certificate path, key path), both PEM."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_names[0] if dns_names else ip_addresses[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(n) for n in dns_names] + [x509.IPAddress(ipaddress.ip_address(a)) for a in ip_addresses]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "doh-cert.pem", directory / "doh-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
            415: "Unsupported Media Type"}


class DohQuery(NamedTuple):
    name: str
    qtype: int
    via: str


class FakeDoh:
    """A DoH server on 127.0.0.1 (its own port, ``via`` ``"direct"``) that also serves any socket a gateway hands it.

    ``names`` maps a name to the address it resolves to; it can be changed while the server runs. ``h2=False`` makes
    it offer HTTP/1.1 only (the way to test that a client falls back), ``h2=True`` is the default when the ``h2``
    package is importable.
    """

    def __init__(self, names: Optional[Dict[str, str]] = None, *, h2: Optional[bool] = None):
        self.names: Dict[str, str] = {k.lower(): v for k, v in (names or {}).items()}
        self.offers_h2 = H2_AVAILABLE if h2 is None else (h2 and H2_AVAILABLE)
        #: Every query, in order.
        self.queries: List[DohQuery] = []
        #: Every TLS connection served: (via, negotiated protocol).
        self.connections: List[Tuple[str, str]] = []
        #: The TLS handshakes that did not complete (a client that refused the certificate): (via, error).
        self.tls_failures: List[Tuple[str, str]] = []
        #: Every HTTP request that was not a well-formed DoH query: (via, method, target, status).
        self.bad_requests: List[Tuple[str, str, str, int]] = []
        self._lock = threading.Lock()
        self._open: List[Tuple[str, ssl.SSLSocket]] = []
        self._dir = tempfile.TemporaryDirectory()
        self.certificate_path, self._key_path = make_certificate(Path(self._dir.name))
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(str(self.certificate_path), str(self._key_path))
        self._context.set_alpn_protocols(["h2", "http/1.1"] if self.offers_h2 else ["http/1.1"])
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self.port: int = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    # --- what a test reads ------------------------------------------------------------------------------------
    def queries_for(self, name: str, qtype: Optional[int] = None) -> List[DohQuery]:
        name = name.lower().rstrip(".")
        with self._lock:
            return [q for q in self.queries if q.name == name and (qtype is None or q.qtype == qtype)]

    def vias_of(self, name: str, qtype: Optional[int] = None) -> Set[str]:
        """The set of roads (gateway names) a name was asked by."""
        return {q.via for q in self.queries_for(name, qtype)}

    def seen_names(self) -> List[str]:
        with self._lock:
            return sorted({q.name for q in self.queries})

    def alpn_of(self, via: str) -> Set[str]:
        with self._lock:
            return {proto for v, proto in self.connections if v == via}

    # --- serving ----------------------------------------------------------------------------------------------
    def _accept(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn, "direct"), daemon=True).start()

    def serve(self, sock: socket.socket, via: str) -> None:
        """Serve one connection (TLS first) that arrived by ``via``. Blocks until it ends, and closes ``sock``."""
        try:
            sock.settimeout(60)
            tls = self._context.wrap_socket(sock, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            with self._lock:
                self.tls_failures.append((via, "%s: %s" % (type(exc).__name__, exc)))
            try:
                sock.close()
            except OSError:
                pass
            return
        protocol = tls.selected_alpn_protocol() or "http/1.1"
        with self._lock:
            self.connections.append((via, protocol))
            self._open.append((via, tls))
        try:
            if protocol == "h2":
                self._serve_h2(tls, via)
            else:
                self._serve_h1(tls, via)
        except (OSError, ssl.SSLError, ValueError, KeyError):
            pass
        except Exception as exc:  # noqa: BLE001 - an h2 protocol error ends the connection like a reset
            if not (H2_AVAILABLE and isinstance(exc, h2.exceptions.H2Error)):
                raise
        finally:
            with self._lock:
                self._open = [(v, s) for v, s in self._open if s is not tls]
            try:
                tls.close()
            except OSError:
                pass

    def drop(self, via: Optional[str] = None) -> int:
        """Close every open connection that arrived by ``via`` (all of them when None). Returns how many."""
        with self._lock:
            victims = [s for v, s in self._open if via is None or v == via]
        for s in victims:
            for action in (lambda: s.shutdown(socket.SHUT_RDWR), s.close):
                try:
                    action()
                except OSError:
                    pass
        return len(victims)

    def _answer(self, via: str, method: str, target: str, content_type: str,
                body: bytes) -> Tuple[int, bytes, str]:
        """(status, body, content type) of one HTTP request, recording the DNS query it carries."""
        def bad(status: int) -> Tuple[int, bytes, str]:
            with self._lock:
                self.bad_requests.append((via, method, target, status))
            return status, b"", "text/plain"

        split = urlsplit(target)
        if split.path != DOH_PATH:
            return bad(404)
        if method == "POST":
            if content_type.split(";")[0].strip().lower() != "application/dns-message":
                return bad(415)
            message = body
        elif method == "GET":
            values = parse_qs(split.query).get("dns")
            if not values:
                return bad(400)
            try:
                message = base64.urlsafe_b64decode(values[0] + "=" * (-len(values[0]) % 4))
            except ValueError:
                return bad(400)
        else:
            return bad(405)
        try:
            query = parse_query(message)
        except ValueError:
            return bad(400)
        with self._lock:
            self.queries.append(DohQuery(query.name, query.qtype, via))
            names = dict(self.names)
        return 200, answer_for(names, query), "application/dns-message"

    def _serve_h1(self, tls: ssl.SSLSocket, via: str) -> None:
        buffer = b""
        while True:
            while b"\r\n\r\n" not in buffer:
                chunk = tls.recv(65536)
                if not chunk:
                    return
                buffer += chunk
            head, _, buffer = buffer.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _version = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            while len(buffer) < length:
                chunk = tls.recv(65536)
                if not chunk:
                    return
                buffer += chunk
            body, buffer = buffer[:length], buffer[length:]
            status, payload, content_type = self._answer(via, method, target, headers.get("content-type", ""), body)
            close = headers.get("connection", "").lower() == "close"
            tls.sendall(("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: max-age=%d\r\n"
                         "%s\r\n" % (status, _REASONS.get(status, "Status"), content_type, len(payload), DOH_TTL,
                                     "Connection: close\r\n" if close else "")).encode("latin-1") + payload)
            if close:
                return

    def _serve_h2(self, tls: ssl.SSLSocket, via: str) -> None:
        connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding="utf-8"))
        connection.initiate_connection()
        tls.sendall(connection.data_to_send())
        streams: Dict[int, dict] = {}
        while True:
            data = tls.recv(65536)
            if not data:
                return
            for event in connection.receive_data(data):
                if isinstance(event, h2.events.RequestReceived):
                    streams[event.stream_id] = {"headers": dict(event.headers), "body": b""}
                elif isinstance(event, h2.events.DataReceived):
                    streams[event.stream_id]["body"] += event.data
                    connection.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    request = streams.pop(event.stream_id)
                    headers = request["headers"]
                    status, payload, content_type = self._answer(
                        via, headers.get(":method", ""), headers.get(":path", ""),
                        headers.get("content-type", ""), request["body"])
                    connection.send_headers(event.stream_id, [
                        (":status", str(status)), ("content-type", content_type),
                        ("content-length", str(len(payload))), ("cache-control", "max-age=%d" % DOH_TTL)])
                    connection.send_data(event.stream_id, payload, end_stream=True)
                elif isinstance(event, h2.events.StreamReset):
                    streams.pop(event.stream_id, None)
                elif isinstance(event, h2.events.ConnectionTerminated):
                    return
            pending = connection.data_to_send()
            if pending:
                tls.sendall(pending)

    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass
        self.drop()
        try:
            self._dir.cleanup()
        except OSError:
            pass

    def __enter__(self) -> "FakeDoh":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------------------------------------- relaying
def pump(source: socket.socket, sink: socket.socket) -> None:
    """Copy bytes from ``source`` to ``sink`` until either ends, then shut both."""
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for s in (source, sink):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def relay(a: socket.socket, b: socket.socket) -> None:
    """Carry bytes both ways between two connected sockets until one ends. Blocks."""
    a.settimeout(None)
    b.settimeout(None)
    thread = threading.Thread(target=pump, args=(b, a), daemon=True)
    thread.start()
    pump(a, b)
    thread.join(timeout=2)


def is_loopback(host: str) -> bool:
    """True for a loopback ADDRESS. A name is never loopback here: nothing in these helpers resolves a name."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class DohGateway(Gateway):
    """A SOCKS5 gateway of ``_socks_servers`` that carries the DoH road to a :class:`FakeDoh`.

    * CONNECT to ``DOH_TARGETS`` is served by ``doh`` with ``via=name``;
    * CONNECT to a loopback address is relayed for real (the pages and the echo servers of a test);
    * the egress endpoints of the base class (``api.ipify.org:80``) keep working;
    * any other CONNECT is refused (reply 2 "not allowed", for an address; 4 "host unreachable", for a name) and
      listed in :attr:`refused`: the gateway never resolves a name and never reaches past the loopback.

    :attr:`connects` (inherited) lists EVERY CONNECT target in order, so ``("page-a.test", 80)`` in it means the
    browser sent a name where it should have sent an address. :attr:`credentials_seen` lists every
    username/password offered, ``first_connection_delay`` holds the first accepted connection that many seconds
    before it is answered (a gateway that is slow to say hello), and :meth:`kill` makes the gateway dead: the port
    refuses, and every open tunnel is closed.
    """

    def __init__(self, name: str, doh: FakeDoh, *, user: Optional[str] = None, password: Optional[str] = None,
                 first_connection_delay: float = 0.0):
        # State first: the base constructor starts the accept thread.
        self.name = name
        self.doh = doh
        self.first_connection_delay = first_connection_delay
        #: Every (username, password) a client offered.
        self.credentials_seen: List[Tuple[str, str]] = []
        #: Every CONNECT that was refused: (host, port).
        self.refused: List[Tuple[str, int]] = []
        #: How many connections were accepted, and the monotonic time of each.
        self.accepted_at: List[float] = []
        self.killed = False
        self._live: List[socket.socket] = []
        self._live_lock = threading.Lock()
        super().__init__(udp="refuse", tcp_exit=EXIT, udp_exit=EXIT, user=user, password=password)

    # --- accepting, and the slow first hello ---------------------------------------------------------------------
    def _handle(self, c: socket.socket) -> None:
        with self._live_lock:
            self.accepted_at.append(time.monotonic())
            first = len(self.accepted_at) == 1
            self._live.append(c)
        if first and self.first_connection_delay:
            time.sleep(self.first_connection_delay)
        super()._handle(c)

    def _greet(self, c: socket.socket) -> bool:
        """The base negotiation, recording the credentials a client sent."""
        ver, n = _recv_exact(c, 2)
        methods = _recv_exact(c, n)
        if ver != 5:
            return False
        if self.user is not None:
            if 2 not in methods:
                self._send(c, b"\x05\xff")
                return False
            self._send(c, b"\x05\x02")
            _recv_exact(c, 1)
            offered = (_recv_exact(c, _recv_exact(c, 1)[0]).decode("utf-8", "replace"),
                       _recv_exact(c, _recv_exact(c, 1)[0]).decode("utf-8", "replace"))
            self.credentials_seen.append(offered)
            ok = offered == (self.user, self.password)
            self._send(c, b"\x01" + (b"\x00" if ok else b"\x01"))
            return ok
        self._send(c, b"\x05\x00" if 0 in methods else b"\x05\x02")
        if 0 not in methods:  # a client that only offers user/password: accept any
            _recv_exact(c, 1)
            offered = (_recv_exact(c, _recv_exact(c, 1)[0]).decode("utf-8", "replace"),
                       _recv_exact(c, _recv_exact(c, 1)[0]).decode("utf-8", "replace"))
            self.credentials_seen.append(offered)
            self._send(c, b"\x01\x00")
        return True

    # --- CONNECT ---------------------------------------------------------------------------------------------------
    def _connect(self, c: socket.socket, host: str, port: int) -> None:
        if (host, port) in DOH_TARGETS:
            self.connects.append((host, port))
            self._send(c, self._reply(0))
            self.doh.serve(c, self.name)
            return
        if (host, port) in (WHOAMI4, WHOAMI6, (WEB_NAME, 80)):
            super()._connect(c, host, port)
            return
        if not is_loopback(host):
            self.connects.append((host, port))
            self.refused.append((host, port))
            self._send(c, self._reply(2 if _is_address(host) else 4))
            return
        self.connects.append((host, port))
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            self._send(c, self._reply(5))
            return
        with self._live_lock:
            self._live.append(upstream)
        self._send(c, self._reply(0))
        try:
            relay(c, upstream)
        finally:
            upstream.close()

    # --- the DoH road, by CONNECT target --------------------------------------------------------------------------
    def doh_connects(self) -> List[Tuple[str, int]]:
        return [t for t in self.connects if t in DOH_TARGETS]

    def kill(self) -> None:
        """Dead proxy: the port refuses new connections and every tunnel that was open is closed."""
        self.killed = True
        # shutdown() first: on Linux, close() alone leaves a listening socket open (and accepting) while another
        # thread is blocked in accept() on it, so the "dead" proxy would still take connections.
        try:
            self._listener.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.close()
        self.doh.drop(self.name)
        with self._live_lock:
            sockets, self._live = self._live, []
        for s in sockets:
            for action in (lambda: s.shutdown(socket.SHUT_RDWR), s.close):
                try:
                    action()
                except OSError:
                    pass


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _recv_exact(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


__all__ = [
    "DOH_BOOTSTRAP", "DOH_HOST", "DOH_PATH", "DOH_PORT", "DOH_TARGETS", "DOH_TTL", "H2_AVAILABLE", "QTYPE_A",
    "QTYPE_AAAA", "RCODE_NXDOMAIN", "RCODE_OK", "RCODE_REFUSED", "DohGateway", "DohQuery", "FakeDoh", "Query",
    "answer_for", "build_response", "encode_query", "is_loopback", "make_certificate", "parse_query", "pump",
    "relay", "wait_until",
]
