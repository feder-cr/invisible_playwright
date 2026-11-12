"""A DNS-over-HTTPS server and a SOCKS5 gateway that carries the DoH road, for tests that need a real wire.

Hermetic: loopback only, no DNS, no internet. Nothing here knows about the product.

What the core declares (core-19), for every session, is the DoH of Firefox at
``https://mozilla.cloudflare-dns.com/dns-query``, found with no DNS at the address ``network.trr.bootstrapAddr``
(162.159.61.4). A test does NOT impersonate that host: Firefox refuses a certificate override for an HSTS-preloaded or
pinned host (``mozilla.cloudflare-dns.com`` is one), so a self-signed certificate for it can never be accepted, whatever
``ignore_https_errors`` says. The test declares its own provider instead, through the core's ``extra_prefs`` overlay:
``network.trr.uri`` = ``https://doh.gateway.test/dns-query`` (a ``.test`` name, never preloaded) and
``network.trr.bootstrapAddr`` = ``192.0.2.53`` (TEST-NET-1, so nothing real is addressed). With a proxy the engine turns it on (``network.trr.mode`` 3, derived from the proxies it receives, never
written by the core), so every name the browser resolves goes to it; the DoH request is a channel like any other, so it
leaves THROUGH the proxy of the context of the lookup. A test of that needs three things this file builds:

* :class:`FakeDoh` - the DoH server. RFC 8484 (POST ``application/dns-message`` and GET ``?dns=base64url``) over TLS
  with a certificate for ``doh.gateway.test`` and ``127.0.0.1``, written by hand: the question is parsed from
  its labels and the answer is built byte by byte (header, the question copied, one A record, TTL 30). It answers
  HTTP/2 when the ``h2`` package is importable and ALPN selects it (Firefox's TRR speaks h2), HTTP/1.1 otherwise.
  Every query is recorded as ``(name, qtype, via)``.
* :class:`DohGateway` - a SOCKS5 gateway (the ``Gateway`` of ``_socks_servers``) whose CONNECT to the DoH address
  (``192.0.2.53:443``, or ``doh.gateway.test:443`` by name) is handed to the FakeDoh of the test, with the
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
    import h2.errors
    import h2.events
    import h2.exceptions
    H2_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    H2_AVAILABLE = False

#: The host name of the DoH the TEST declares (``network.trr.uri``) and the address it is found at without DNS
#: (``network.trr.bootstrapAddr``). Not the core's default provider: that host is HSTS-preloaded/pinned, so Firefox
#: refuses any certificate override for it and a fake could never be accepted. A ``.test`` name is on no preload list;
#: 192.0.2.0/24 is TEST-NET-1 (RFC 5737), reserved for documentation, so the address cannot reach anything real.
DOH_HOST = "doh.gateway.test"
DOH_BOOTSTRAP = "192.0.2.53"
DOH_PORT = 443
DOH_PATH = "/dns-query"
#: The two prefs that make a session use the FakeDoh as its provider (applied with ``extra_prefs``).
DOH_PROVIDER_PREFS = {"network.trr.uri": "https://%s%s" % (DOH_HOST, DOH_PATH),
                      "network.trr.bootstrapAddr": DOH_BOOTSTRAP}
#: Every CONNECT target the gateways hand to the FakeDoh.
DOH_TARGETS = frozenset({(DOH_BOOTSTRAP, DOH_PORT), (DOH_HOST, DOH_PORT)})

QTYPE_A, QTYPE_AAAA, QTYPE_HTTPS = 1, 28, 65
RCODE_OK, RCODE_SERVFAIL, RCODE_NXDOMAIN, RCODE_REFUSED = 0, 2, 3, 5
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
            415: "Unsupported Media Type", 503: "Service Unavailable"}


class DohQuery(NamedTuple):
    name: str
    qtype: int
    via: str


#: The ways :class:`FakeDoh` can refuse a query (``fail_first``). All three are failures of the lookup for Firefox's TRR:
#: ``close`` ends the exchange with no answer (an h2 stream reset, or a closed connection over HTTP/1.1): a failure of
#: the CHANNEL, the kind ``TRRService::ConfirmationContext::RecordTRRStatus`` counts (``NS_FAILED(channelStatus)``);
#: ``http503`` answers an HTTP error (``TRR_SERVER_RESPONSE_ERR``); ``servfail`` answers a well-formed DNS message with
#: rcode 2 (``TRR_RCODE_FAIL``).
FAILURE_KINDS = ("close", "http503", "servfail")


class DohAnswer(NamedTuple):
    """What the FakeDoh did with one query: ``outcome`` is ``"answered"`` or a failure kind, ``delay`` the seconds
    between the arrival of the query and the moment its answer (or its refusal) was sent."""
    name: str
    qtype: int
    via: str
    outcome: str
    delay: float


class _Reply(NamedTuple):
    status: int
    body: bytes
    content_type: str
    delay: float = 0.0
    close: bool = False      # no answer at all: reset the stream / close the connection
    record: Optional[Tuple[str, int, str, str]] = None   # (name, qtype, via, outcome), logged when it is sent


class FakeDoh:
    """A DoH server on 127.0.0.1 (its own port, ``via`` ``"direct"``) that also serves any socket a gateway hands it.

    ``names`` maps a name to the address it resolves to; it can be changed while the server runs. ``h2=False`` makes
    it offer HTTP/1.1 only (the way to test that a client falls back), ``h2=True`` is the default when the ``h2``
    package is importable.
    """

    def __init__(self, names: Optional[Dict[str, str]] = None, *, h2: Optional[bool] = None,
                 delay_s: float = 0.0, delay_names: Optional[Dict[str, float]] = None,
                 fail_first: Optional[Dict[str, str]] = None):
        self.names: Dict[str, str] = {k.lower(): v for k, v in (names or {}).items()}
        self.offers_h2 = H2_AVAILABLE if h2 is None else (h2 and H2_AVAILABLE)
        #: Latency of EVERY answer, in seconds (each response is held that long after its query arrived, not only the
        #: connection set-up); ``delay_names`` overrides it per name (a name or any name under it). Both can be changed
        #: while the server runs. Over h2 the answers are held independently, so the A and the AAAA of one lookup come
        #: back together after the delay, not one after the other.
        self.delay_s = delay_s
        self.delay_names: Dict[str, float] = {k.lower().rstrip("."): v for k, v in (delay_names or {}).items()}
        #: ``{name: kind}`` (kind in :data:`FAILURE_KINDS`): the FIRST query of each type (A, AAAA...) for ``name`` is
        #: refused in that way; every later one is served as usual. Can be changed while the server runs.
        self.fail_first: Dict[str, str] = {k.lower().rstrip("."): v for k, v in (fail_first or {}).items()}
        for kind in self.fail_first.values():
            if kind not in FAILURE_KINDS:
                raise ValueError("unknown failure kind %r, expected one of %s" % (kind, FAILURE_KINDS))
        #: What was done with every query, in the order it was SENT (see :class:`DohAnswer`).
        self.answers: List[DohAnswer] = []
        self._failed: Set[Tuple[str, int]] = set()
        #: Every query, in order.
        self.queries: List[DohQuery] = []
        #: The names for which this server answered a type 65 (HTTPS) query WITH DATA. It never does: the retail Firefox reads
        #: h3 at its first visit when the DNS answer carries that record, so a test that expects h2 first (the h3 e2e) asserts
        #: that this stays empty, and says so if the server learns to serve it.
        self.https_served: List[str] = []
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

    def answers_for(self, name: str, qtype: Optional[int] = None) -> List[DohAnswer]:
        name = name.lower().rstrip(".")
        with self._lock:
            return [a for a in self.answers if a.name == name and (qtype is None or a.qtype == qtype)]

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

    def _delay_of(self, name: str) -> float:
        for key, seconds in self.delay_names.items():
            if name == key or name.endswith("." + key):
                return seconds
        return self.delay_s

    def _record(self, record: Optional[Tuple[str, int, str, str]], received: float) -> None:
        """Log that an answer is being sent now (``received`` is the monotonic time its query arrived)."""
        if record is not None:
            with self._lock:
                self.answers.append(DohAnswer(*record, time.monotonic() - received))

    def _answer(self, via: str, method: str, target: str, content_type: str, body: bytes) -> _Reply:
        """What to send for one HTTP request, recording the DNS query it carries."""
        def bad(status: int) -> _Reply:
            with self._lock:
                self.bad_requests.append((via, method, target, status))
            return _Reply(status, b"", "text/plain")

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
            kind = self.fail_first.get(query.name)
            if kind is not None and (query.name, query.qtype) in self._failed:
                kind = None
            elif kind is not None:
                self._failed.add((query.name, query.qtype))
        delay = self._delay_of(query.name)
        record = (query.name, query.qtype, via, kind or "answered")
        if kind == "close":
            return _Reply(0, b"", "", delay, True, record)
        if kind == "http503":
            return _Reply(503, b"", "text/plain", delay, False, record)
        if kind == "servfail":
            return _Reply(200, build_response(query, RCODE_SERVFAIL), "application/dns-message", delay, False, record)
        message = answer_for(names, query)
        if query.qtype == QTYPE_HTTPS and struct.unpack("!H", message[6:8])[0] > 0:
            with self._lock:
                self.https_served.append(query.name)
        return _Reply(200, message, "application/dns-message", delay, False, record)

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
            received = time.monotonic()
            reply = self._answer(via, method, target, headers.get("content-type", ""), body)
            if reply.delay:
                time.sleep(reply.delay)
            self._record(reply.record, received)
            if reply.close:
                return
            status, payload, content_type = reply.status, reply.body, reply.content_type
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
        #: Answers being held back: (due monotonic time, stream id, reply, monotonic time the query arrived). One thread
        #: reads, holds and writes, so the TLS socket never has a reader and a writer at once, and the answers of one
        #: connection are held independently of each other (the A and the AAAA of a lookup both wait ``delay``).
        held: List[Tuple[float, int, _Reply, float]] = []
        tls.settimeout(0.05)
        last_activity = time.monotonic()

        def send(stream_id: int, reply: _Reply, received: float) -> None:
            self._record(reply.record, received)
            try:
                if reply.close:
                    connection.reset_stream(stream_id, error_code=h2.errors.ErrorCodes.INTERNAL_ERROR)
                else:
                    connection.send_headers(stream_id, [
                        (":status", str(reply.status)), ("content-type", reply.content_type),
                        ("content-length", str(len(reply.body))), ("cache-control", "max-age=%d" % DOH_TTL)])
                    connection.send_data(stream_id, reply.body, end_stream=True)
            except h2.exceptions.H2Error:  # the client reset the stream while its answer was held
                pass

        while True:
            now = time.monotonic()
            for item in [h for h in held if h[0] <= now]:
                held.remove(item)
                send(item[1], item[2], item[3])
            pending = connection.data_to_send()
            if pending:
                tls.sendall(pending)
            try:
                data = tls.recv(65536)
            except (socket.timeout, ssl.SSLWantReadError):
                if not held and time.monotonic() - last_activity > 60:
                    return
                continue
            if not data:
                return
            last_activity = time.monotonic()
            for event in connection.receive_data(data):
                if isinstance(event, h2.events.RequestReceived):
                    streams[event.stream_id] = {"headers": dict(event.headers), "body": b""}
                elif isinstance(event, h2.events.DataReceived):
                    streams[event.stream_id]["body"] += event.data
                    connection.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    request = streams.pop(event.stream_id)
                    headers = request["headers"]
                    received = time.monotonic()
                    reply = self._answer(
                        via, headers.get(":method", ""), headers.get(":path", ""),
                        headers.get("content-type", ""), request["body"])
                    if reply.delay:
                        held.append((received + reply.delay, event.stream_id, reply, received))
                    else:
                        send(event.stream_id, reply, received)
                elif isinstance(event, h2.events.StreamReset):
                    streams.pop(event.stream_id, None)
                    held[:] = [h for h in held if h[1] != event.stream_id]
                elif isinstance(event, h2.events.ConnectionTerminated):
                    return

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
def pump(source: socket.socket, sink: socket.socket, delay: float = 0.0) -> None:
    """Copy bytes from ``source`` to ``sink`` until either ends, then shut both. ``delay`` seconds wait before each
    chunk is forwarded (a gateway that is far away)."""
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            if delay:
                time.sleep(delay)
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for s in (source, sink):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def relay(a: socket.socket, b: socket.socket, delay: float = 0.0) -> None:
    """Carry bytes both ways between two connected sockets until one ends. Blocks."""
    a.settimeout(None)
    b.settimeout(None)
    thread = threading.Thread(target=pump, args=(b, a, delay), daemon=True)
    thread.start()
    pump(a, b, delay)
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
                 first_connection_delay: float = 0.0, **gateway: object):
        # ``gateway`` goes to the base Gateway as it is (``udp="relay"``, ``relay_host``, ``rtt_ms``, ``exit_host``,
        # ``tcp_exit``, ``udp_exit``, ``tcp_data_delay``): a road with UDP is this same class, not another gateway.
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
        options = dict(udp="refuse", tcp_exit=EXIT, udp_exit=EXIT)
        options.update(gateway)
        super().__init__(user=user, password=password, **options)

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
            if self.tcp_data_delay and self.rtt:   # a far gateway: every chunk of the tunnel waits half the round trip
                near, far = socket.socketpair()
                threading.Thread(target=self.doh.serve, args=(far, self.name), daemon=True).start()
                try:
                    relay(c, near, self.rtt / 2)
                finally:
                    near.close()
                return
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
            relay(c, upstream, self.rtt / 2 if self.tcp_data_delay else 0.0)
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
    "DOH_BOOTSTRAP", "DOH_HOST", "DOH_PATH", "DOH_PORT", "DOH_PROVIDER_PREFS", "DOH_TARGETS", "DOH_TTL", "H2_AVAILABLE", "QTYPE_A",
    "QTYPE_AAAA", "QTYPE_HTTPS", "RCODE_NXDOMAIN", "RCODE_OK", "RCODE_REFUSED", "RCODE_SERVFAIL", "DohAnswer", "DohGateway", "DohQuery",
    "FAILURE_KINDS", "FakeDoh", "Query",
    "answer_for", "build_response", "encode_query", "is_loopback", "make_certificate", "parse_query", "pump",
    "relay", "wait_until",
]
