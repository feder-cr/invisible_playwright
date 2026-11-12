"""Three local SOCKS5 gateways on 127.0.0.1, for tests that need a real wire.

Reusable: nothing here knows about the core. A test (or the wrapper's system
test) starts one with ``with coherent() as gw:`` and points the product at
``gw.server`` (``socks5://127.0.0.1:<port>``), with ``gw.credentials`` when the
gateway asks for a user and password.

The gateways are the things a real one can be:

* :func:`coherent` - TCP CONNECT works, UDP ASSOCIATE is granted, and the relay
  carries a STUN binding request to a fake STUN server that answers with the
  SAME address the TCP side reports as the exit. This is the gateway whose UDP
  can be trusted.
* :func:`tcp_only` - TCP CONNECT works, UDP ASSOCIATE is refused with reply code
  7 ("command not supported"), the way a residential gateway does.
* :func:`mismatched` - UDP ASSOCIATE is granted and STUN answers, but with an
  address DIFFERENT from the TCP exit: the disagreement a detector looks for.
* :func:`silent_udp` - UDP ASSOCIATE is granted and the relay carries datagrams, but
  no STUN server ever answers (a router that drops UDP behind a working handshake): the core's
  measurement ends in ``unmeasured``.

THE CORE MEASURES THE UDP EXIT of a SOCKS5 proxy before it declares it to the browser (``invisible_core._udp_exit``: a STUN
Binding through the proxy's ASSOCIATE, compared with the TCP exit). A test that wants the relay to be used hands the core
this gateway's measurement server, ``udp_stun_servers=gw.measure_servers`` (``MEASURE_NUMBER``: the fake STUN server by a
documentation NUMBER of its own, so the datagrams of the measurement are told apart from those of the page, which asks
``STUN_NUMBER``). The measurement is a UDP ASSOCIATE of its own: :attr:`Gateway.associates` and :attr:`Gateway.datagrams` count it.

Everything is on loopback, so the "exit address" is made observable and
controllable instead of real: the TCP exit is what the gateway's fake "what is
my address" endpoint (``api.ipify.org:80``, the one the product asks) answers,
and the UDP exit is what the fake STUN server puts in its XOR-MAPPED-ADDRESS.
Both are chosen by the test (``tcp_exit`` and ``udp_exit`` arguments).

Any other CONNECT is really forwarded, and any other UDP datagram is really
forwarded to its destination and the reply wrapped back, so a page, or an echo
server on 127.0.0.1, can be reached through the gateway and the test can
observe which road its bytes took (:attr:`Gateway.connects`,
:attr:`Gateway.datagrams`).

This is the ONE SOCKS5 gateway of the test suite. A road that needs more than the three above (a TURN server, a resolver,
a page, by NAME: ``tests/_turn_chain.py``) subclasses :class:`Gateway` and overrides the small hooks (:meth:`Gateway._route`,
:meth:`Gateway._target`, :attr:`Gateway.whoami_names`) instead of re-implementing the greeting, the authentication, the
ASSOCIATE, the relay or the round trip.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import threading
import time
from typing import List, Optional, Tuple

#: The name the product's STUN probe sends through the relay. The relay maps it
#: to the fake STUN server; every other destination is forwarded for real.
STUN_NAME = "stun.l.google.com"
STUN_PORT = 19302
#: The same STUN server by NUMBER. A page cannot resolve a name behind the session (the engine forbids local
#: resolution), so the wrapper's system test hands the page this address; like the name it is a documentation
#: address (RFC 5737, TEST-NET-2), unroutable: the only road to it is the relay, which maps it to the fake STUN
#: server. (A copy of invisible_core's tests/_socks_servers.py with this one addition.)
STUN_NUMBER = "198.51.100.5"
#: The same fake STUN server by ANOTHER number: where the core's own measurement of the UDP exit asks (``udp_stun_servers``),
#: so that what the measurement sent through the relay is never mistaken for what the page sent. Documentation address too.
MEASURE_NUMBER = "198.51.100.6"
#: A page the gateway serves itself, for a test that loads one through the proxy (a name nothing else resolves).
WEB_NAME = "page.test"
#: The same page by an address (RFC 5737 TEST-NET-3): since the browser resolves names itself and connects by address, a
#: test that loads the gateway's page through a SOCKS gateway names it by this one.
WEB_ADDRESS = "203.0.113.80"
#: The echo endpoints the product asks for its TCP exit.
WHOAMI4 = ("api.ipify.org", 80)
WHOAMI6 = ("api6.ipify.org", 80)

_MAGIC = b"\x21\x12\xa4\x42"


def _recv_exact(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


class FakeStun:
    """A UDP STUN server that answers every binding request with ``mapped_ip``."""

    def __init__(self, mapped_ip: str, mapped_port: int = 40000):
        self.mapped_ip = mapped_ip
        self.mapped_port = mapped_port
        self.requests = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self.address: Tuple[str, int] = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                data, peer = self._sock.recvfrom(2048)
            except OSError:
                return
            if len(data) < 20 or data[0:2] != b"\x00\x01":
                continue
            self.requests += 1
            txid = data[8:20]
            xport = struct.pack("!H", self.mapped_port ^ 0x2112)
            xaddr = bytes(a ^ b for a, b in zip(socket.inet_aton(self.mapped_ip), _MAGIC))
            attr = struct.pack("!HH", 0x0020, 8) + b"\x00\x01" + xport + xaddr
            resp = b"\x01\x01" + struct.pack("!H", len(attr)) + _MAGIC + txid + attr
            try:
                self._sock.sendto(resp, peer)
            except OSError:
                return

    def close(self) -> None:
        self._sock.close()


class Gateway:
    """A SOCKS5 server. ``udp`` is ``"relay"`` or ``"refuse"`` (reply code 7)."""

    def __init__(self, *, udp: str, tcp_exit: str, udp_exit: str,
                 user: Optional[str] = None, password: Optional[str] = None,
                 relay_host: str = "127.0.0.1", rtt_ms: int = 0,
                 exit_host: str = "127.0.0.1", tcp_data_delay: bool = False, stun_answers: bool = True):
        assert udp in ("relay", "refuse")
        self.udp = udp
        #: False: the relay carries the datagrams but the fake STUN server is mute (its requests are dropped).
        self.stun_answers = stun_answers
        #: The round trip to this gateway, in seconds: every answer on the control connection waits for it, and each
        #: datagram the relay carries waits half of it each way. A real gateway is not on loopback.
        self.rtt = rtt_ms / 1000.0
        #: The address of the UDP relay, the BND.ADDR of the association. A real gateway's is a routable address; the
        #: loopback default only suits a client whose UDP socket is not bound to an interface (an engine's ICE
        #: sockets are, and Windows does not send from an interface address to loopback).
        self.relay_host = relay_host
        #: The local address the relay's datagrams leave from (the source a server behind the gateway sees). Another
        #: loopback address makes a client that sent direct (from 127.0.0.1) tell apart from one that used the relay.
        self.exit_host = exit_host
        #: Whether each chunk of a forwarded TCP stream also waits half the round trip (a far gateway), as the datagrams do.
        self.tcp_data_delay = tcp_data_delay
        self.tcp_exit = tcp_exit
        self.user, self.password = user, password
        self.stun = FakeStun(udp_exit)
        #: Every CONNECT, as (host, port), in order.
        self.connects: List[Tuple[str, int]] = []
        #: Every UDP ASSOCIATE request, answered or refused.
        self.associates = 0
        #: When the gateway started: the origin of the times below.
        self.t0 = time.monotonic()
        #: The same instant on the wall clock (seconds since the epoch), to compare with what a page reads from Date.now().
        self.wall0 = time.time()
        #: Every TCP connection the gateway accepted (a SOCKS handshake starts with one), as seconds from t0.
        self.accepted: List[float] = []
        #: Every UDP ASSOCIATE that was granted, as [start, end] seconds from t0; end is None until it was answered.
        self.associations: List[List[Optional[float]]] = []
        #: Every datagram the relay carried out, as (host as sent, port, address type, seconds from t0).
        self.datagram_log: List[Tuple[str, int, int, float]] = []
        #: Every datagram the relay carried out, as (host, port, payload).
        self.datagrams: List[Tuple[str, int, bytes]] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self.port: int = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    # --- what the test hands to the product ---------------------------------
    @property
    def server(self) -> str:
        return "socks5://127.0.0.1:%d" % self.port

    @property
    def measure_servers(self) -> List[str]:
        """The ``udp_stun_servers`` of a launch behind this gateway: where the core measures its UDP exit, in the
        form the parameter takes (``"ip:port"``, an IPv4 literal)."""
        return ["%s:%d" % (MEASURE_NUMBER, STUN_PORT)]

    @property
    def credentials(self) -> dict:
        return ({"username": self.user, "password": self.password}
                if self.user is not None else {})

    @property
    def proxy(self) -> dict:
        """The endpoint dict the core takes: ``{"server": ..., [credentials]}``."""
        return dict({"server": self.server}, **self.credentials)

    def close(self) -> None:
        self._closed = True
        for s in (self._listener,):
            try:
                s.close()
            except OSError:
                pass
        self.stun.close()

    def __enter__(self) -> "Gateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- the TCP side --------------------------------------------------------
    def _send(self, c: socket.socket, data: bytes) -> None:
        if self.rtt:
            time.sleep(self.rtt)
        c.sendall(data)

    def _accept(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self.accepted.append(time.monotonic() - self.t0)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _greet(self, c: socket.socket) -> bool:
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
            u = _recv_exact(c, _recv_exact(c, 1)[0]).decode()
            p = _recv_exact(c, _recv_exact(c, 1)[0]).decode()
            ok = (u, p) == (self.user, self.password)
            self._send(c, b"\x01" + (b"\x00" if ok else b"\x01"))
            return ok
        self._send(c, b"\x05\x00" if 0 in methods else b"\x05\x02")
        if 0 not in methods:  # a client that only offers user/password: accept any
            _recv_exact(c, 1)
            _recv_exact(c, _recv_exact(c, 1)[0])
            _recv_exact(c, _recv_exact(c, 1)[0])
            self._send(c, b"\x01\x00")
        return True

    @staticmethod
    def _read_address(c: socket.socket) -> Tuple[str, int]:
        atyp = _recv_exact(c, 1)[0]
        if atyp == 1:
            host = socket.inet_ntoa(_recv_exact(c, 4))
        elif atyp == 3:
            host = _recv_exact(c, _recv_exact(c, 1)[0]).decode()
        else:
            host = socket.inet_ntop(socket.AF_INET6, _recv_exact(c, 16))
        return host, struct.unpack("!H", _recv_exact(c, 2))[0]

    @staticmethod
    def _reply(rep: int, host: str = "0.0.0.0", port: int = 0) -> bytes:
        return b"\x05" + bytes([rep]) + b"\x00\x01" + socket.inet_aton(host) + struct.pack("!H", port)

    def _handle(self, c: socket.socket) -> None:
        try:
            c.settimeout(30)
            if not self._greet(c):
                return
            ver, cmd, _rsv = _recv_exact(c, 3)
            host, port = self._read_address(c)
            if cmd == 1:
                self._connect(c, host, port)
            elif cmd == 3:
                self._associate(c)
            else:
                self._send(c, self._reply(7))
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass

    #: The names the product asks "what is my address" of (on port 80); each is answered with ``tcp_exit``.
    whoami_names: Tuple[str, ...] = (WHOAMI4[0],)

    def _target(self, host: str, port: int) -> Tuple[str, int]:
        """Where a CONNECT that is not a special one really goes. A subclass maps a name to a local server."""
        return host, port

    def _connect(self, c: socket.socket, host: str, port: int) -> None:
        self.connects.append((host, port))
        if host.lower() in self.whoami_names and port == WHOAMI4[1]:
            self._send(c, self._reply(0))
            _ = c.recv(4096)
            body = self.tcp_exit.encode()
            self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n"
                      b"Connection: close\r\n\r\n%s" % (len(body), body))
            return
        if (host, port) == WHOAMI6:
            self._send(c, self._reply(4))  # no IPv6 exit
            return
        if (host, port) in ((WEB_NAME, 80), (WEB_ADDRESS, 80)):  # a page that only the gateway can reach
            self._send(c, self._reply(0))
            _ = c.recv(4096)
            body = b"<html><body>behind the gateway</body></html>"
            self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n"
                      b"Connection: close\r\n\r\n%s" % (len(body), body))
            return
        try:
            up = socket.create_connection(self._target(host, port), timeout=10)
        except OSError:
            self._send(c, self._reply(5))
            return
        self._send(c, self._reply(0))
        c.settimeout(None)
        up.settimeout(None)

        def pump(a: socket.socket, b: socket.socket) -> None:
            try:
                while True:
                    d = a.recv(65536)
                    if not d:
                        break
                    if self.tcp_data_delay and self.rtt:
                        time.sleep(self.rtt / 2)
                    b.sendall(d)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=pump, args=(up, c), daemon=True)
        t.start()
        pump(c, up)
        t.join(timeout=2)
        up.close()

    # --- the UDP side --------------------------------------------------------
    def _later(self, fn) -> None:
        """Run ``fn`` after half the round trip (at once on loopback), ignoring a socket that is already closed."""
        def run() -> None:
            try:
                fn()
            except OSError:
                pass
        threading.Timer(self.rtt / 2, run).start() if self.rtt else run()

    def _deliver(self, relay: socket.socket, to: Tuple[str, int], host: str, port: int, payload: bytes) -> None:
        """Wrap ``payload`` as coming from ``host``:``port`` (an IPv4 literal) and send it back to the client."""
        data = b"\x00\x00\x00\x01" + socket.inet_aton(host) + struct.pack("!H", port) + payload
        self._later(lambda: relay.sendto(data, to))

    def _route(self, host: str, dport: int, atyp: int, payload: bytes, out_port: int,
               answer) -> Optional[Tuple[Tuple[str, int], Optional[Tuple[str, int]]]]:
        """Where one datagram of the client goes: ``(target, reported_as)``, or None when it is dropped.

        ``target`` is the real address the datagram is sent to from the exit socket (``out_port`` is that socket's port);
        ``reported_as`` is the address the client is told the reply came from (None: the real source). A subclass may also
        answer by itself with ``answer(host, port, data)``. By default the fake STUN server, by name or by number, is
        mapped and everything else is forwarded for real."""
        if (host, dport) in ((STUN_NAME, STUN_PORT), (STUN_NUMBER, STUN_PORT), (MEASURE_NUMBER, STUN_PORT)):
            if not self.stun_answers:
                return None
            # A real server answers from the address it was asked at; the fake STUN server is mapped, so its reply is
            # reported as from the asked address (only when that was a literal: a name has no address to report).
            return self.stun.address, ((host, dport) if atyp == 1 else None)
        return (host, dport), None

    def _associate(self, c: socket.socket) -> None:
        self.associates += 1
        if self.udp == "refuse":
            self._send(c, self._reply(7))
            return
        record: List[Optional[float]] = [time.monotonic() - self.t0, None]
        self.associations.append(record)
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        relay.bind((self.relay_host, 0))
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out.bind((self.exit_host, 0))
        client: List[Optional[Tuple[str, int]]] = [None]
        #: Where a mapped destination really is -> the address the client is told it answered from.
        asked_at: dict = {}
        self._send(c, self._reply(0, self.relay_host, relay.getsockname()[1]))
        record[1] = time.monotonic() - self.t0

        def answer(host: str, port: int, data: bytes) -> None:
            self._deliver(relay, client[0], host, port, data)

        def inbound() -> None:  # client -> world
            while True:
                try:
                    d, peer = relay.recvfrom(65536)
                except OSError:
                    return
                if len(d) < 10 or d[2] != 0:
                    continue
                client[0] = peer
                atyp, i = d[3], 4
                if atyp == 1:
                    host, i = socket.inet_ntoa(d[i:i + 4]), i + 4
                elif atyp == 3:
                    n = d[i]
                    host, i = d[i + 1:i + 1 + n].decode(), i + 1 + n
                else:
                    host, i = socket.inet_ntop(socket.AF_INET6, d[i:i + 16]), i + 16
                dport = struct.unpack("!H", d[i:i + 2])[0]
                payload = d[i + 2:]
                self.datagrams.append((host, dport, payload))
                self.datagram_log.append((host, dport, atyp, time.monotonic() - self.t0))
                routed = self._route(host, dport, atyp, payload, out.getsockname()[1], answer)
                if routed is None:
                    continue
                target, reported = routed
                if reported is not None:
                    asked_at[target] = reported
                self._later(lambda p=payload, t=target: out.sendto(p, t))

        def outbound() -> None:  # world -> client
            while True:
                try:
                    d, src = out.recvfrom(65536)
                except OSError:
                    return
                if client[0] is None:
                    continue
                host, port = asked_at.get(src, src)
                self._deliver(relay, client[0], host, port, d)

        threading.Thread(target=inbound, daemon=True).start()
        threading.Thread(target=outbound, daemon=True).start()
        try:  # the association lives as long as the control connection
            c.settimeout(None)
            while c.recv(1024):
                pass
        except OSError:
            pass
        finally:
            relay.close()
            out.close()


#: Documentation addresses (RFC 5737): never a real host, so nothing in a test
#: can be mistaken for the machine's own address.
EXIT = "203.0.113.7"
OTHER_EXIT = "198.51.100.4"


def coherent(*, exit_ip: str = EXIT, user: Optional[str] = None,
             password: Optional[str] = None, relay_host: str = "127.0.0.1", rtt_ms: int = 0) -> Gateway:
    """TCP and UDP both work, and both leave from ``exit_ip``."""
    return Gateway(udp="relay", tcp_exit=exit_ip, udp_exit=exit_ip,
                   user=user, password=password, relay_host=relay_host, rtt_ms=rtt_ms)


def tcp_only(*, exit_ip: str = EXIT, user: Optional[str] = None,
             password: Optional[str] = None, relay_host: str = "127.0.0.1") -> Gateway:
    """TCP works; UDP ASSOCIATE is refused with reply code 7."""
    return Gateway(udp="refuse", tcp_exit=exit_ip, udp_exit=exit_ip,
                   user=user, password=password, relay_host=relay_host)


def mismatched(*, tcp_exit: str = EXIT, udp_exit: str = OTHER_EXIT,
               user: Optional[str] = None, password: Optional[str] = None, relay_host: str = "127.0.0.1") -> Gateway:
    """UDP is granted and STUN answers, with an address that is not the TCP exit."""
    assert udp_exit != tcp_exit
    return Gateway(udp="relay", tcp_exit=tcp_exit, udp_exit=udp_exit,
                   user=user, password=password, relay_host=relay_host)


def silent_udp(*, exit_ip: str = EXIT, user: Optional[str] = None, password: Optional[str] = None,
               relay_host: str = "127.0.0.1") -> Gateway:
    """TCP works and UDP ASSOCIATE is granted, but no STUN request is ever answered: the core's measurement is
    ``unmeasured``."""
    return Gateway(udp="relay", tcp_exit=exit_ip, udp_exit=exit_ip, user=user, password=password,
                   relay_host=relay_host, stun_answers=False)


# ----------------------------------------------------------------------------------------------------------------
#  A tiny TCP-only SOCKS5 server for the e2e tests (the fixture `socks5_tcp_only` of conftest.py makes one).
# ----------------------------------------------------------------------------------------------------------------
class Socks5TcpOnly:
    """Minimal SOCKS5: no-auth, CONNECT (TCP) relayed, UDP ASSOCIATE refused.

    Reproduces a residential TCP-only proxy: pages load over TCP, but WebRTC's
    UDP path is dead - which (for a no-camera page in default_address_only mode)
    is exactly what made the default-route probe fail and ICE return zero
    candidates before Fix C.
    """

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        self.udp_associate_attempts = 0
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _recv_exact(self, sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle(self, conn):
        try:
            head = self._recv_exact(conn, 2)
            if not head or head[0] != 0x05:
                conn.close()
                return
            nmethods = head[1]
            self._recv_exact(conn, nmethods)
            conn.sendall(b"\x05\x00")  # no-auth
            req = self._recv_exact(conn, 4)
            if not req:
                conn.close()
                return
            ver, cmd, _, atyp = req
            if atyp == 0x01:
                addr = socket.inet_ntoa(self._recv_exact(conn, 4))
            elif atyp == 0x03:
                ln = self._recv_exact(conn, 1)[0]
                addr = self._recv_exact(conn, ln).decode("ascii", "ignore")
            elif atyp == 0x04:
                addr = socket.inet_ntop(socket.AF_INET6, self._recv_exact(conn, 16))
            else:
                conn.close()
                return
            port = struct.unpack("!H", self._recv_exact(conn, 2))[0]
            if cmd != 0x01:  # not CONNECT (e.g. UDP ASSOCIATE) → refuse
                self.udp_associate_attempts += 1
                conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # cmd not supported
                conn.close()
                return
            try:
                upstream = socket.create_connection((addr, port), timeout=15)
            except OSError:
                conn.sendall(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")  # host unreachable
                conn.close()
                return
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")  # success
            self._relay(conn, upstream)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def _relay(self, a, b):
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], 30)
                if not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        finally:
            for s in (a, b):
                try:
                    s.close()
                except Exception:
                    pass

    def close(self):
        self._stop = True
        try:
            self._srv.close()
        except Exception:
            pass
