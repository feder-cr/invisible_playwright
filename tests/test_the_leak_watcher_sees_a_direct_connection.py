"""The traps of the DoH e2e (`_leaks.py`): the connection watcher and the resolver trap, tested on what they promise.

A trap that cannot see is worse than none: the e2e asserts "nothing left the host", and an empty Watcher reads the
same whether nothing left or the Watcher was blind. So the Watcher is shown a connection that does leave (a connect in
flight to a blackholed documentation address, from a child process) and shown that it ignores loopback. The resolver
trap is shown on a free port without touching any file; the rewrite of /etc/resolv.conf is tested only where it can
run (Linux, root).
"""
from __future__ import annotations

import errno
import socket
import subprocess
import sys
import threading
import time

import pytest

import _doh_servers as ds
from _leaks import ResolverTrap, Watcher

pytestmark = pytest.mark.unit

#: RFC 5737 TEST-NET-3: never routed, so a SYN to it stays in flight (or is refused at once on a host with no route).
BLACKHOLE = ("203.0.113.1", 9)

_IN_FLIGHT = {errno.EINPROGRESS, errno.EWOULDBLOCK, getattr(errno, "WSAEWOULDBLOCK", errno.EWOULDBLOCK), 10035}

CHILD_SYN = """
import errno, socket, sys, time
s = socket.socket()
s.setblocking(False)
code = s.connect_ex(("%s", %d))
print("in-flight" if code in (%s) else "failed:%%d" %% code, flush=True)
time.sleep(float(sys.argv[1]))
""" % (BLACKHOLE[0], BLACKHOLE[1], ", ".join(str(c) for c in sorted(_IN_FLIGHT)))


def _child(code, *args):
    return subprocess.Popen([sys.executable, "-c", code, *map(str, args)], stdout=subprocess.PIPE, text=True)


def test_the_watcher_flags_a_connection_in_flight_to_a_non_loopback_address():
    with Watcher(match="python") as watcher:
        child = _child(CHILD_SYN, 4)
        try:
            state = child.stdout.readline().strip()
            if state != "in-flight":
                pytest.skip("this host refuses a connect to %s:%d at once (%s): a SYN_SENT cannot be observed here"
                            % (BLACKHOLE + (state,)))
            seen = ds.wait_until(lambda: any(c.remote == "%s:%d" % BLACKHOLE for c in watcher.direct), 3)
        finally:
            child.kill()
            child.wait()
    if not seen and sys.platform != "win32" and not watcher.watched_pids:
        pytest.skip("the child process was never listed as a descendant")
    assert seen, "a connect in flight to %s:%d was not reported; the watcher saw: %s (processes: %s)" % (
        BLACKHOLE + (watcher.describe(), watcher.process_names))
    first = [c for c in watcher.direct if c.remote == "%s:%d" % BLACKHOLE][0]
    assert first.protocol == "tcp" and first.pid in watcher.watched_pids


def test_the_watcher_ignores_loopback_connections_tcp_and_udp():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    code = """
import socket, sys, time
s = socket.create_connection(("127.0.0.1", %d))
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
u.connect(("127.0.0.1", %d))
print("up", flush=True)
time.sleep(1.5)
""" % (listener.getsockname()[1], udp.getsockname()[1])
    with Watcher(match="python") as watcher:
        child = _child(code)
        try:
            assert child.stdout.readline().strip() == "up"
            time.sleep(0.8)
        finally:
            child.kill()
            child.wait()
    listener.close()
    udp.close()
    assert watcher.direct == [], watcher.describe()
    assert watcher.samples > 5


def test_the_watcher_lists_the_names_of_the_descendants():
    with Watcher(match="no-such-process-name") as watcher:
        child = _child("import time; print('x', flush=True); time.sleep(2)")
        try:
            child.stdout.readline()
            assert ds.wait_until(lambda: watcher.process_names, 3)
        finally:
            child.kill()
            child.wait()
    assert any("python" in n.lower() for n in watcher.process_names), watcher.process_names
    assert watcher.direct == [] and not watcher.watched_pids, "an unmatched name is listed but not judged"


def test_helpers_are_matched_on_the_stem_case_insensitively():
    watcher = Watcher()
    watcher.process_names[:] = ["firefox.exe", "CrashReporter.exe", "default-browser-agent.exe", "updater",
                                "python3", "Updater.EXE", "pingsender"]
    assert watcher.helpers_seen(["crashreporter", "default-browser-agent", "maintenanceservice", "updater",
                                 "pingsender"]) == ["CrashReporter.exe", "default-browser-agent.exe", "updater",
                                                    "Updater.EXE", "pingsender"]
    assert watcher.helpers_seen(["exe"]) == [], "the extension is not part of the stem"


# ---------------------------------------------------------------------------------------------- the resolver trap
def _udp_ask(port, name, qtype=ds.QTYPE_A):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.sendto(ds.encode_query(name, qtype, ident=77), ("127.0.0.1", port))
    data, _ = s.recvfrom(4096)
    s.close()
    return data


def _tcp_ask(port, name):
    import struct
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    message = ds.encode_query(name, ds.QTYPE_A, ident=78)
    s.sendall(struct.pack("!H", len(message)) + message)
    size = struct.unpack("!H", s.recv(2))[0]
    data = b""
    while len(data) < size:
        data += s.recv(size - len(data))
    s.close()
    return data


def _udp_ask_at(address: str, port: int, name: str) -> bytes:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(ds.encode_query(name, ds.QTYPE_A, 0x004D), (address, port))
        return s.recvfrom(4096)[0]
    finally:
        s.close()


def test_the_trap_counts_a_query_it_is_sent_and_refuses_what_it_cannot_answer():
    with ResolverTrap(port=0, rewrite_resolv_conf=False) as trap:
        assert trap.port > 0
        refused = _udp_ask(trap.port, "leak.test")
        assert refused[0:2] == b"\x00\x4d" and refused[3] & 0xF == ds.RCODE_REFUSED
        _tcp_ask(trap.port, "leak-tcp.test")
        assert ds.wait_until(lambda: len(trap.queries) == 2, 3)
        assert [(q.name, q.qtype, q.via) for q in trap.queries] == [
            ("leak.test", ds.QTYPE_A, "udp"), ("leak-tcp.test", ds.QTYPE_A, "tcp")]
        assert trap.asked("LEAK.test.") and trap.names() == ["leak-tcp.test", "leak.test"]


def test_the_trap_goes_to_the_next_loopback_address_when_the_first_has_its_port_taken():
    """On a hosted runner something can hold 127.0.0.1:53 (or a wildcard of it): the trap takes the first loopback address
    of TRAP_ADDRESSES it can bind, says which, and that is the address a resolv.conf is written with (run 43146661635: the
    trap could not bind and eight tests ended in error at setup)."""
    import _leaks

    holder_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        holder_tcp.bind(("127.0.0.1", 0))
        port = holder_tcp.getsockname()[1]
        try:
            holder_udp.bind(("127.0.0.1", port))
        except OSError:
            raise
        holder_tcp.listen(1)
        # port != 0 is the case of port 53: the addresses are tried in order
        with ResolverTrap(port=port, rewrite_resolv_conf=False) as trap:
            assert trap.address != "127.0.0.1" and trap.address in _leaks.TRAP_ADDRESSES, trap.address
            assert _udp_ask_at(trap.address, port, "leak.test")[3] & 0xF == ds.RCODE_REFUSED
    finally:
        holder_tcp.close()
        holder_udp.close()


def test_the_trap_answers_the_names_it_is_given_over_udp_and_tcp():
    with ResolverTrap({"page-d.test": "127.0.0.1"}, port=0, rewrite_resolv_conf=False) as trap:
        for data in (_udp_ask(trap.port, "page-d.test"), _tcp_ask(trap.port, "page-d.test")):
            assert data[3] & 0xF == 0 and data[6:8] == b"\x00\x01", "NOERROR with one answer"
            assert data.endswith(socket.inet_aton("127.0.0.1"))
        aaaa = _udp_ask(trap.port, "page-d.test", ds.QTYPE_AAAA)
        assert aaaa[3] & 0xF == 0 and aaaa[6:8] == b"\x00\x00", "empty NOERROR for AAAA"


# A test that does not apply to a host is not collected there, never skipped (ci/required-suites.json: no skip is
# allowed in a required suite). Exactly one of the two below exists on every host, so the count is the same everywhere.
if ResolverTrap.available:
    def test_the_trap_points_resolv_conf_at_itself_and_restores_it_byte_for_byte():
        from pathlib import Path
        original = Path("/etc/resolv.conf").read_bytes()
        with ResolverTrap({"page-d.test": "127.0.0.1"}) as trap:
            assert b"nameserver 127.0.0.1" in Path("/etc/resolv.conf").read_bytes()
            assert socket.gethostbyname("page-d.test") == "127.0.0.1"
            assert trap.asked("page-d.test")
        assert Path("/etc/resolv.conf").read_bytes() == original
else:
    def test_a_trap_that_cannot_rewrite_resolv_conf_says_why_and_does_not_start():
        assert ResolverTrap.reason
        with pytest.raises(RuntimeError):
            ResolverTrap().__enter__()
