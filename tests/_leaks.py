"""Two traps for what a browser does that it should not: a connection that does not go through the gateway, and a
question put to the resolver of the machine.

* :class:`Watcher` samples, from before a launch until it is stopped, the network connections of every process that
  is a DESCENDANT of this test process and whose executable name contains a given word (``firefox`` by default).
  An inet connection - TCP in any state, SYN_SENT included, or a connected UDP socket - whose remote address is not
  loopback is a DIRECT connection: the browser did not go through the proxy (the proxy of a test is on 127.0.0.1).
  It also lists the names of every descendant it saw, which is how a test shows that no helper process
  (crash reporter, updater...) was ever started. Descendants of THIS process only, so parallel workers do not see
  each other's browsers.
* :class:`ResolverTrap` listens on 127.0.0.1:53 (UDP and TCP, and ::1 when it can) and points ``/etc/resolv.conf``
  at it for as long as it runs, restoring the file byte for byte at the end, so every question that reaches the
  resolver of the machine is counted as ``(name, qtype)``. It needs Linux, root and a writable resolv.conf:
  ``ResolverTrap.available`` says so and ``ResolverTrap.reason`` says why not. The trap answers from ``answers``
  (name -> IPv4) and REFUSES everything else at once, so nothing waits for it. Anything on the machine that resolves
  a name while it runs is counted too, the test process included: a test must not resolve names itself.

  ``ResolverTrap(port=0, rewrite_resolv_conf=False)`` is the same trap on a free port with no file touched, for the
  unit tests of the trap itself and for a client that is told where to ask.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from _doh_servers import RCODE_REFUSED, DohQuery, answer_for, build_response, parse_query

RESOLV_CONF = Path("/etc/resolv.conf")


# --------------------------------------------------------------------------------------------------------- Watcher
class DirectConnection(NamedTuple):
    pid: int
    process: str          # the name of the process
    protocol: str         # "tcp" or "udp"
    local: str
    remote: str           # "ip:port"
    status: str


def _is_loopback(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _connections_of(process) -> list:
    getter = getattr(process, "net_connections", None) or process.connections
    return getter(kind="inet")


class Watcher:
    """Samples every ``interval`` seconds. Use as a context manager, or ``start()`` and ``stop()``.

    ``match`` is a word (or several) searched, case-insensitively, in the name of a process: only the connections of
    the descendants whose name contains one of them are judged. Every descendant, matching or not, is listed in
    :attr:`process_names`.
    """

    def __init__(self, *, interval: float = 0.02, match: Iterable[str] | str = "firefox", root_pid: Optional[int] = None):
        self.interval = interval
        self.match = tuple(m.lower() for m in ([match] if isinstance(match, str) else match))
        self.root_pid = root_pid if root_pid is not None else os.getpid()
        #: Every direct connection seen, once per (pid, remote, status).
        self.direct: List[DirectConnection] = []
        #: Names of the descendants seen, in the order they were first seen (one entry per name).
        self.process_names: List[str] = []
        #: pids of the matching descendants seen.
        self.watched_pids: Set[int] = set()
        self.samples = 0
        self._seen_keys: Set[tuple] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- control -------------------------------------------------------------------------------------------------
    def start(self) -> "Watcher":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="leak-watcher")
            self._thread.start()
        return self

    def stop(self) -> "Watcher":
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        return self

    def __enter__(self) -> "Watcher":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- what a test asks ----------------------------------------------------------------------------------------
    def describe(self) -> str:
        return "; ".join("%s(pid %d) %s %s -> %s %s" % (c.process, c.pid, c.protocol, c.local, c.remote, c.status)
                         for c in self.direct) or "nothing"

    def helpers_seen(self, forbidden: Iterable[str]) -> List[str]:
        """The process names whose stem (the name without its extension) contains one of ``forbidden``."""
        words = [w.lower() for w in forbidden]
        return [n for n in self.process_names if any(w in os.path.splitext(n)[0].lower() for w in words)]

    # --- the sampling --------------------------------------------------------------------------------------------
    def _run(self) -> None:
        import psutil

        root = None
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                root = root or psutil.Process(self.root_pid)
                children = root.children(recursive=True)
            except psutil.Error:
                children = []
            for child in children:
                try:
                    name = child.name()
                except psutil.Error:
                    continue
                if name not in self.process_names:
                    self.process_names.append(name)
                if not any(m in name.lower() for m in self.match):
                    continue
                self.watched_pids.add(child.pid)
                try:
                    connections = _connections_of(child)
                except (psutil.Error, OSError):
                    continue
                for c in connections:
                    if not c.raddr or _is_loopback(c.raddr.ip):
                        continue
                    protocol = "udp" if c.type == socket.SOCK_DGRAM else "tcp"
                    key = (child.pid, protocol, c.raddr.ip, c.raddr.port, c.status)
                    if key in self._seen_keys:
                        continue
                    self._seen_keys.add(key)
                    self.direct.append(DirectConnection(
                        child.pid, name, protocol, "%s:%s" % (c.laddr.ip, c.laddr.port) if c.laddr else "",
                        "%s:%s" % (c.raddr.ip, c.raddr.port), c.status))
            self.samples += 1
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))


# ---------------------------------------------------------------------------------------------------- ResolverTrap
def _trap_capability() -> Tuple[bool, str]:
    if not sys.platform.startswith("linux"):
        return False, "the resolver trap needs Linux (it points /etc/resolv.conf at itself)"
    if os.geteuid() != 0:
        return False, "the resolver trap needs root (port 53 and /etc/resolv.conf)"
    if not os.access(RESOLV_CONF, os.W_OK):
        return False, "/etc/resolv.conf is not writable"
    return True, ""


class ResolverTrap:
    """A resolver on loopback that counts what it is asked. See the module docstring."""

    available, reason = _trap_capability()

    def __init__(self, answers: Optional[Dict[str, str]] = None, *, port: int = 53,
                 rewrite_resolv_conf: bool = True):
        self.answers = {k.lower(): v for k, v in (answers or {}).items()}
        self.port = port
        self.rewrite_resolv_conf = rewrite_resolv_conf
        #: Every question: (name, qtype, "udp"|"tcp").
        self.queries: List[DohQuery] = []
        self._lock = threading.Lock()
        self._sockets: List[socket.socket] = []
        self._original: Optional[bytes] = None
        self._running = False
        #: Why the last bind attempt failed (the error of the socket), for the message when none succeeds.
        self._bind_error: Optional[OSError] = None

    # --- what a test reads ---------------------------------------------------------------------------------------
    def names(self) -> List[str]:
        with self._lock:
            return sorted({q.name for q in self.queries})

    def asked(self, name: str, qtype: Optional[int] = None) -> List[DohQuery]:
        name = name.lower().rstrip(".")
        with self._lock:
            return [q for q in self.queries if q.name == name and (qtype is None or q.qtype == qtype)]

    def describe(self) -> str:
        with self._lock:
            return ", ".join("%s/%d (%s)" % q for q in self.queries) or "nothing"

    # --- control -------------------------------------------------------------------------------------------------
    def __enter__(self) -> "ResolverTrap":
        if self.rewrite_resolv_conf and not self.available:
            raise RuntimeError(self.reason)
        try:
            self._bind()
            if self.rewrite_resolv_conf:
                self._original = RESOLV_CONF.read_bytes()
                # In place, not by rename: in a container the file is often a bind mount.
                with open(RESOLV_CONF, "wb") as f:
                    f.write(b"nameserver 127.0.0.1\noptions timeout:1 attempts:1\n")
        except BaseException:
            self._restore()
            raise
        self._running = True
        return self

    def __exit__(self, *exc) -> None:
        self._running = False
        self._restore()

    def _restore(self) -> None:
        if self._original is not None:
            with open(RESOLV_CONF, "wb") as f:
                f.write(self._original)
            self._original = None
        for s in self._sockets:
            try:
                s.close()
            except OSError:
                pass
        self._sockets = []

    def _bind(self) -> None:
        """UDP and TCP on 127.0.0.1 (required) and on ::1 (when it can): the same port for all of them."""
        discarded: List[socket.socket] = []   # closed at the end, so a failed attempt's port is not handed out again
        made: List[socket.socket] = []
        try:
            for attempt in range(200):
                made = self._bind_once(discarded)
                if made:
                    break
                if self.port != 0:
                    break
            if not made:
                raise OSError("the resolver trap found no port it could bind for TCP and UDP (port %d on 127.0.0.1; the last error: %s)"
                              % (self.port, self._bind_error))
        finally:
            for s in discarded:
                s.close()
        self._sockets += made
        for s in made:
            target = self._serve_udp if s.type == socket.SOCK_DGRAM else self._serve_tcp
            threading.Thread(target=target, args=(s,), daemon=True).start()

    def _bind_once(self, discarded: List[socket.socket]) -> List[socket.socket]:
        """One attempt: the sockets bound, or [] after putting what it opened in ``discarded``."""
        made: List[socket.socket] = []
        port = self.port
        for family, host, required in ((socket.AF_INET, "127.0.0.1", True), (socket.AF_INET6, "::1", False)):
            pair: List[socket.socket] = []    # this family's TCP and UDP, kept or dropped together
            try:
                # TCP first: Windows keeps big excluded ranges and hands out ephemeral ports in sequence, so a
                # UDP-first pick can land in a range TCP may not bind, again and again.
                tcp = socket.socket(family, socket.SOCK_STREAM)
                pair.append(tcp)
                if sys.platform != "win32":   # on Windows SO_REUSEADDR is "share the port": it breaks bind
                    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp.bind((host, port))
                if port == 0:
                    port = tcp.getsockname()[1]
                tcp.listen(16)
                udp = socket.socket(family, socket.SOCK_DGRAM)
                pair.append(udp)
                udp.bind((host, port))
            except OSError as exc:
                self._bind_error = exc
                discarded += pair
                if required:
                    discarded += made
                    return []
                continue
            made += pair
        self.port = port
        return made

    # --- answering -----------------------------------------------------------------------------------------------
    def _respond(self, message: bytes, protocol: str) -> Optional[bytes]:
        try:
            query = parse_query(message)
        except ValueError:
            return None
        with self._lock:
            self.queries.append(DohQuery(query.name, query.qtype, protocol))
        if query.name in self.answers:
            return answer_for(self.answers, query)
        return build_response(query, RCODE_REFUSED)

    def _serve_udp(self, sock: socket.socket) -> None:
        while True:
            try:
                data, peer = sock.recvfrom(4096)
            except OSError:
                return
            response = self._respond(data, "udp")
            if response is not None:
                try:
                    sock.sendto(response, peer)
                except OSError:
                    pass

    def _serve_tcp(self, listener: socket.socket) -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=self._tcp_conversation, args=(conn,), daemon=True).start()

    def _tcp_conversation(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5)
            while True:
                head = conn.recv(2)
                if len(head) < 2:
                    return
                size = struct.unpack("!H", head)[0]
                message = b""
                while len(message) < size:
                    chunk = conn.recv(size - len(message))
                    if not chunk:
                        return
                    message += chunk
                response = self._respond(message, "tcp")
                if response is None:
                    return
                conn.sendall(struct.pack("!H", len(response)) + response)
        except OSError:
            pass
        finally:
            conn.close()


__all__ = ["DirectConnection", "ResolverTrap", "Watcher"]
