"""Two traps for what a browser does that it should not: a connection that does not go through the gateway, and a
question put to the resolver of the machine.

* :class:`Watcher` samples, from before a launch until it is stopped, the network connections of every process that
  is a DESCENDANT of this test process and whose executable name contains a given word (``firefox`` by default).
  An inet connection - TCP in any state, SYN_SENT included, or a connected UDP socket - whose remote address is not
  loopback is a DIRECT connection: the browser did not go through the proxy (the proxy of a test is on 127.0.0.1).
  It also lists the names of every descendant it saw, which is how a test shows that no helper process
  (crash reporter, updater...) was ever started. Descendants of THIS process only, so parallel workers do not see
  each other's browsers.
* :class:`ResolverTrap` listens on 127.0.0.1:53 (or the first of 127.0.0.2... it can bind: see TRAP_ADDRESSES) (UDP and TCP, and ::1 when it can) and points ``/etc/resolv.conf``
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


#: The loopback addresses the trap tries for port 53, in order. A hosted runner can have something on 127.0.0.1:53 or on the
#: wildcard of that port (systemd-resolved holds 127.0.0.53 and 127.0.0.54): every 127.x.x.x is loopback on Linux, so the trap
#: takes the first one it can bind and writes THAT address into /etc/resolv.conf (a resolv.conf names no port). With a free
#: port (port=0) only 127.0.0.1 is used.
TRAP_ADDRESSES = ("127.0.0.1", "127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.6", "127.0.0.7", "127.0.0.8")


def _who_holds(port: int) -> str:
    """What the machine says about the sockets of `port` (Linux: ss), for the message of a trap that could not bind."""
    import subprocess
    for command in (["ss", "-H", "-lntup"], ["netstat", "-lntup"]):
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        lines = [line for line in done.stdout.splitlines() if (":%d " % port) in line]
        return "; held by: " + (" | ".join(lines) if lines else "nothing listed by %s" % command[0])
    return "; no ss or netstat to say who holds it"


class Asker(NamedTuple):
    """Who sent a question to the trap: the user, the process (None when its socket could not be traced) and its command."""
    uid: int
    pid: Optional[int]
    command: str


def row_from_proc_net(text: str, port: int) -> Optional[Tuple[int, int]]:
    """``(uid, inode)`` of the socket bound to local port ``port`` in the text of one of /proc/net/{udp,udp6,tcp,tcp6}, or None.

    A row is ``sl local_address rem_address st tx:rx tr:tm retrnsmt uid timeout inode ...``; the local address is
    ``HEX_ADDRESS:HEX_PORT``. The client of a DNS question is a socket still open (it waits for the answer), on an
    ephemeral port of its own."""
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or ":" not in fields[1]:
            continue
        try:
            if int(fields[1].rsplit(":", 1)[1], 16) == port:
                return int(fields[7]), int(fields[9])
        except ValueError:
            continue
    return None


def uid_from_proc_net(text: str, port: int) -> Optional[int]:
    """The uid of that socket (see :func:`row_from_proc_net`)."""
    row = row_from_proc_net(text, port)
    return None if row is None else row[0]


def pid_of_socket_inode(inode: int, proc: str = "/proc") -> Optional[int]:
    """The pid of a process that holds the socket with this inode open (root sees every process), or None."""
    needle = "socket:[%d]" % inode
    try:
        entries = os.listdir(proc)
    except OSError:
        return None
    for name in entries:
        if not name.isdigit():
            continue
        fd_dir = os.path.join(proc, name, "fd")
        try:
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(os.path.join(fd_dir, fd)) == needle:
                        return int(name)
                except OSError:
                    continue
        except OSError:
            continue
    return None


def parent_of(pid: int, proc: str = "/proc") -> Optional[int]:
    """The parent of a process, from /proc/<pid>/stat (the name between parentheses may hold spaces), or None."""
    try:
        with open(os.path.join(proc, str(pid), "stat"), encoding="utf-8", errors="replace") as handle:
            return int(handle.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def command_of(pid: int, proc: str = "/proc") -> str:
    """What a process is, for a message: its name and the start of its command line."""
    try:
        with open(os.path.join(proc, str(pid), "comm"), encoding="utf-8", errors="replace") as handle:
            name = handle.read().strip()
        with open(os.path.join(proc, str(pid), "cmdline"), "rb") as handle:
            line = b" ".join(handle.read().split(b"\0")).decode("utf-8", "replace").strip()
        return ("%s: %s" % (name, line))[:120]
    except OSError:
        return "(gone)"


def descends_from(pid: int, root: int, parent=parent_of) -> bool:
    """Whether ``pid`` is ``root`` or one of its descendants, going up the parents (at most 64 steps)."""
    for _ in range(64):
        if pid == root:
            return True
        up = parent(pid)
        if up is None or up <= 1 or up == pid:
            return False
        pid = up
    return False


def is_a_browser_process(pid: int, proc: str = "/proc") -> bool:
    """Whether a process is the browser's own (the main process, ``firefox``, or ``firefox-bin``), by its name in /proc/<pid>/comm.
    Its children (content, socket, forkserver) are found by their parents."""
    try:
        with open(os.path.join(proc, str(pid), "comm"), encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() in ("firefox", "firefox-bin", "firefox.exe")
    except OSError:
        return False


def under_a_browser(pid: int, root: int, parent=parent_of, is_browser=is_a_browser_process) -> bool:
    """Whether ``pid`` is a browser process or one of its descendants, going up the parents and stopping at ``root`` (the test
    process: it is NOT the browser, and neither is anything it runs that is not a browser, like the core's own download of a
    database) or at a process with no parent known (at most 64 steps)."""
    for _ in range(64):
        if is_browser(pid):
            return True
        if pid == root:
            return False
        up = parent(pid)
        if up is None or up <= 1 or up == pid:
            return False
        pid = up
    return False


def who_asked(protocol: str, peer) -> Optional[Asker]:
    """Who sent a question to the trap, read where the kernel says it (Linux): the row of the client's socket in /proc/net
    gives the user and the inode, and the inode gives the process that holds it. None off Linux or when the socket is
    gone. ``peer`` is the (address, port...) of the client."""
    if not sys.platform.startswith("linux"):
        return None
    six = ":" in str(peer[0])
    names = ("udp6" if six else "udp",) if protocol == "udp" else ("tcp6" if six else "tcp",)
    for name in names:
        try:
            with open("/proc/net/" + name, encoding="utf-8") as table:
                row = row_from_proc_net(table.read(), int(peer[1]))
        except OSError:
            continue
        if row is not None:
            uid, inode = row
            pid = pid_of_socket_inode(inode) if inode else None
            return Asker(uid, pid, command_of(pid) if pid is not None else "")
    return None


def owner_of_the_peer(protocol: str, peer) -> Optional[int]:
    """The uid of the process that sent a question to the trap (see :func:`who_asked`), or None."""
    asker = who_asked(protocol, peer)
    return None if asker is None else asker.uid


class ResolverTrap:
    """A resolver on loopback that counts what it is asked. See the module docstring.

    It counts what the BROWSER asks, and the test says "the browser asked the resolver of the machine". The browser is found from
    the socket the question came from (its inode, the process that holds it, its parents up to ``root_pid``, by default the
    test process): a question counts if that process is the browser or descends from it. A question from any other process is
    answered and listed in :attr:`foreign` with its pid and command, not counted: /etc/resolv.conf is the machine's, and on a
    CI runner the agent of the runner, even as root, asks for github.com through it; and the test process's own python (the
    core, which asks github.com for the latest GeoIP tag when no database is given) is not the browser either (runs
    43188313516, 43211837926: the browser was blamed for names it never asked, and a user filter cannot tell them apart). There
    is no list of names. A question whose process cannot be traced is judged by its user (``only_uid``, by default the
    effective uid), and one whose socket cannot be found any more counts as the browser's."""

    available, reason = _trap_capability()

    def __init__(self, answers: Optional[Dict[str, str]] = None, *, port: int = 53,
                 rewrite_resolv_conf: bool = True, only_uid: Optional[int] = None, owner_of=None,
                 root_pid: Optional[int] = None, parent_of_pid=None, is_browser=None):
        self.answers = {k.lower(): v for k, v in (answers or {}).items()}
        self.port = port
        #: The loopback address the trap listens on once it is bound (see TRAP_ADDRESSES).
        self.address = "127.0.0.1"
        self.rewrite_resolv_conf = rewrite_resolv_conf
        #: Every question: (name, qtype, "udp"|"tcp").
        self.queries: List[DohQuery] = []
        #: The questions of other users' processes: (query, uid). Answered, not counted.
        self.foreign: List[Tuple[DohQuery, Asker]] = []
        self.root_pid = root_pid if root_pid is not None else os.getpid()
        self._parent_of = parent_of_pid or parent_of
        self._is_browser = is_browser or is_a_browser_process
        self.only_uid = only_uid if only_uid is not None else (os.geteuid() if hasattr(os, "geteuid") else None)
        self._owner_of = owner_of or who_asked
        self._lock = threading.Lock()
        self._sockets: List[socket.socket] = []
        #: The threads that answer on `_sockets`, and the flag that ends them: see `_restore`.
        self._threads: List[threading.Thread] = []
        self._ending = threading.Event()
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
            seen = ", ".join("%s/%d (%s)" % q for q in self.queries) or "nothing"
            if self.foreign:
                seen += " [and %d question(s) of other processes, not counted: %s]" % (
                    len(self.foreign), "; ".join(sorted({"%s from pid %s (uid %d) %s" % (
                        q.name, a.pid if a.pid is not None else "?", a.uid, a.command or "") for q, a in self.foreign})))
            return seen

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
                    f.write(("nameserver %s\noptions timeout:1 attempts:1\n" % self.address).encode("ascii"))
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
        # ⛔ THE THREADS END BEFORE THE SOCKETS ARE CLOSED. A thread blocked in accept() or recvfrom() is not woken by a close()
        # of its socket from another thread (Linux): the kernel keeps the socket, still bound, until the call returns, and the
        # call never returns. The port stayed taken after the test, and the NEXT trap found 127.0.0.1-8:53 "already in use"
        # one address per test, until all eight were gone (run 43164943499; here: the eighth trap of a run of 19 tests).
        # The answering loops therefore wake up every 0.2 s to look at `_ending`, and the socket is closed once they are gone.
        self._ending.set()
        for t in self._threads:
            t.join(2.0)
        self._threads = []
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
            addresses = TRAP_ADDRESSES if self.port != 0 else TRAP_ADDRESSES[:1]
            for address in addresses:
                for attempt in range(200):
                    made = self._bind_once(discarded, address)
                    if made:
                        break
                    if self.port != 0:
                        break
                if made:
                    self.address = address
                    break
            if not made:
                raise OSError("the resolver trap found no port it could bind for TCP and UDP (port %d on %s; the last error: %s)%s"
                              % (self.port, ", ".join(addresses), self._bind_error, _who_holds(self.port)))
        finally:
            for s in discarded:
                s.close()
        self._sockets += made
        for s in made:
            s.settimeout(0.2)
            target = self._serve_udp if s.type == socket.SOCK_DGRAM else self._serve_tcp
            t = threading.Thread(target=target, args=(s,), daemon=True)
            self._threads.append(t)
            t.start()

    def _bind_once(self, discarded: List[socket.socket], address: str = "127.0.0.1") -> List[socket.socket]:
        """One attempt: the sockets bound, or [] after putting what it opened in ``discarded``."""
        made: List[socket.socket] = []
        port = self.port
        for family, host, required in ((socket.AF_INET, address, True), (socket.AF_INET6, "::1", False)):
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
    def _respond(self, message: bytes, protocol: str, peer=None) -> Optional[bytes]:
        try:
            query = parse_query(message)
        except ValueError:
            return None
        asker = self._owner_of(protocol, peer) if peer is not None else None
        if isinstance(asker, int):    # an owner given as a bare uid
            asker = Asker(asker, None, "")
        ours = True
        if asker is not None:
            if asker.pid is not None:
                ours = under_a_browser(asker.pid, self.root_pid, self._parent_of, self._is_browser)
            elif self.only_uid is not None:
                ours = asker.uid == self.only_uid
        with self._lock:
            if ours:
                self.queries.append(DohQuery(query.name, query.qtype, protocol))
            else:
                self.foreign.append((DohQuery(query.name, query.qtype, protocol), asker))
        if query.name in self.answers:
            return answer_for(self.answers, query)
        return build_response(query, RCODE_REFUSED)

    def _serve_udp(self, sock: socket.socket) -> None:
        while not self._ending.is_set():
            try:
                data, peer = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            response = self._respond(data, "udp", peer)
            if response is not None:
                try:
                    sock.sendto(response, peer)
                except OSError:
                    pass

    def _serve_tcp(self, listener: socket.socket) -> None:
        while not self._ending.is_set():
            try:
                conn, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._tcp_conversation, args=(conn, peer), daemon=True).start()

    def _tcp_conversation(self, conn: socket.socket, peer=None) -> None:
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
                response = self._respond(message, "tcp", peer)
                if response is None:
                    return
                conn.sendall(struct.pack("!H", len(response)) + response)
        except OSError:
            pass
        finally:
            conn.close()


__all__ = ["DirectConnection", "ResolverTrap", "Watcher"]
