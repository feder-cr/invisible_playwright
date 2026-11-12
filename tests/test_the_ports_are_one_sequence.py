"""The harness has ONE way to find a port number where TCP and UDP both bind (tests/_ports.py): the HTTP/3 server, the witness and the resolver trap walk it, and it says why when it finds none.

The known-bad case (run 43230437094, wrapper.e2e.web on Windows, three tests of tests/test_http3_through_the_proxy_e2e.py): ``H3Server`` asked the system for a TCP
port fifty times, took the same number for a UDP probe, swallowed every refusal and raised "no port free for both TCP and UDP". Windows hands out ephemeral TCP
ports in sequence and keeps excluded ranges of a hundred ports for UDP in the same zone (tests/test_the_witness_picks_a_port_both_sockets_bind.py has the same case
for the witness): fifty consecutive numbers can all be refused. A fake socket layer does that here, without the machine. The old server is red on it (it has no
picker to call and, as it was written, raised after fifty refusals); this one finds a number outside the range, and when it finds none it lists the codes.
"""
from __future__ import annotations

import types

import pytest

import _h3_server
import _ports

pytestmark = pytest.mark.unit

REFUSED = range(50000, 50200)


class _Udp:
    def __init__(self, layer):
        self.layer = layer

    def bind(self, pair):
        self.layer.udp_tries.append(pair[1])
        if self.layer.refuse_all or pair[1] in REFUSED:
            raise OSError(10013, "An attempt was made to access a socket in a way forbidden by its access permissions")

    def close(self):
        self.layer.udp_closed += 1


class _Layer:
    AF_INET, SOCK_DGRAM = 2, 2

    def __init__(self, refuse_all=False):
        self.refuse_all = refuse_all
        self.udp_tries = []
        self.udp_closed = 0
        self.next_pick = 50000
        self.tcp_open = []

    def socket(self, family, kind):
        return _Udp(self)

    def make_tcp(self, address, port):
        if port == 0:
            port = self.next_pick
            self.next_pick += 1
        layer = self

        class Tcp:
            server_address = (address, port)

            def server_close(self):
                layer.tcp_open.remove(self)

        tcp = Tcp()
        self.tcp_open.append(tcp)
        return tcp


def test_the_picker_leaves_the_range_that_the_systems_sequential_picks_fall_in():
    layer = _Layer()
    tcp, port = _ports.bind_tcp_and_probe_udp("10.1.0.111", layer.make_tcp, socket_module=layer)
    assert port not in REFUSED and 20000 <= port <= 32767, port
    assert tcp.server_address == ("10.1.0.111", port)
    assert layer.tcp_open == [tcp], "the refused TCP servers were closed and the chosen one is kept"
    assert layer.udp_tries[0] in REFUSED, "the first try is the system's own pick"
    assert layer.udp_closed == len(layer.udp_tries), "every probe was closed again"


def test_the_picker_that_finds_none_says_how_many_tries_and_the_codes_of_the_last_refusals(monkeypatch):
    layer = _Layer(refuse_all=True)
    asked = []
    monkeypatch.setattr(_ports, "who_holds", lambda port: asked.append(port) or "; held by: a process")
    with pytest.raises(RuntimeError) as caught:
        _ports.bind_tcp_and_probe_udp("10.1.0.111", layer.make_tcp, tries=7, socket_module=layer)
    message = str(caught.value)
    assert "10.1.0.111" in message and "7 tries" in message and message.count("10013") == 3, message
    # The last number tried is named and the machine is asked who holds it (ss or netstat: Linux says, the message carries it).
    assert len(asked) == 1 and ("the last number tried: %d" % asked[0]) in message and "held by: a process" in message, message
    assert layer.tcp_open == [], "nothing is left open"


def test_a_tcp_bind_that_is_refused_is_a_refusal_too_and_the_next_number_is_tried():
    layer = _Layer()
    real = layer.make_tcp
    calls = []

    def flaky(address, port):
        calls.append(port)
        if len(calls) == 1:
            raise OSError(10048, "address in use")
        return real(address, port)

    tcp, port = _ports.bind_tcp_and_probe_udp("10.1.0.111", flaky, socket_module=layer)
    assert len(calls) >= 2 and tcp.server_address[1] == port


def test_the_h3_server_takes_its_port_from_the_shared_picker(monkeypatch):
    layer = _Layer()
    monkeypatch.setattr(_h3_server, "socket", layer)
    monkeypatch.setattr(_h3_server, "_TcpServer", lambda pair, handler: layer.make_tcp(pair[0], pair[1]))
    monkeypatch.setattr(_h3_server, "bind_tcp_and_probe_udp", _ports.bind_tcp_and_probe_udp)
    tcp, port = _h3_server._tcp_server_on_a_port_udp_binds("10.1.0.111")
    assert port not in REFUSED and tcp.server_address == ("10.1.0.111", port)
    import inspect
    assert "_ports" in inspect.getsource(_h3_server) and "randint" not in inspect.getsource(_h3_server), "a copy of the picker is back in the server"


def test_the_tcp_server_of_the_h3_server_does_not_share_a_port_on_windows():
    import sys

    assert _h3_server._TcpServer.allow_reuse_address is (sys.platform != "win32")


# --- the same sequence for the witness and the resolver trap -----------------------------------------------------------------------------------

def test_the_sequence_is_the_system_pick_then_numbers_below_the_ephemeral_range():
    numbers = list(_ports.candidates(30))
    assert numbers[0] == 0 and len(numbers) == 30
    assert all(_ports.DRAW_FROM[0] <= n <= _ports.DRAW_FROM[1] for n in numbers[1:]) and _ports.DRAW_FROM[1] < 32768
    assert len(set(numbers[1:])) > 1, "the numbers are drawn, not repeated"


def test_the_witness_and_the_trap_walk_the_same_sequence_and_the_same_limits():
    import _leaks
    import _witness

    assert _witness.candidates is _ports.candidates and _leaks.candidates is _ports.candidates
    assert (_witness.TRIES, _witness.DRAW_FROM) == (_ports.TRIES, _ports.DRAW_FROM)


class _TrapLayer(_Layer):
    """The socket module of the trap: IPv6 is not there (::1 is optional), TCP picks are sequential, UDP refuses REFUSED."""

    AF_INET6, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR = 10, 1, 1, 2

    def __init__(self, refuse_all=False):
        super().__init__(refuse_all)
        self.tcp_ports = []

    def socket(self, family, kind):
        return _TrapSocket(self, family, kind)


class _TrapSocket:
    def __init__(self, layer, family, kind):
        self.layer, self.family, self.type, self.port = layer, family, kind, None

    def setsockopt(self, *args):
        pass

    def settimeout(self, seconds):
        pass

    def bind(self, where):
        address, port = where
        if self.family == self.layer.AF_INET6:
            raise OSError(99, "no ::1 here")
        if self.type == self.layer.SOCK_STREAM:
            if port == 0:
                port = self.layer.next_pick
                self.layer.next_pick += 1
            self.port = port
            self.layer.tcp_ports.append(port)
            return
        self.layer.udp_tries.append(port)
        if self.layer.refuse_all or port in REFUSED:
            raise OSError(10013, "An attempt was made to access a socket in a way forbidden by its access permissions")
        self.port = port

    def getsockname(self):
        return ("127.0.0.1", self.port)

    def listen(self, backlog):
        pass

    def close(self):
        pass


class _NoThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


def _trap_with(monkeypatch, layer, port):
    import threading

    import _leaks

    monkeypatch.setattr(_leaks, "socket", layer)
    monkeypatch.setattr(_leaks, "threading", types.SimpleNamespace(Thread=_NoThread, Lock=threading.Lock, Event=threading.Event))
    return _leaks.ResolverTrap(port=port, rewrite_resolv_conf=False)


def test_the_resolver_trap_with_port_0_leaves_the_block_that_the_systems_sequential_picks_fall_in(monkeypatch):
    layer = _TrapLayer()
    trap = _trap_with(monkeypatch, layer, 0)
    trap._bind()
    assert trap.port not in REFUSED and trap.address == "127.0.0.1", (trap.port, trap.address)
    assert layer.udp_tries[0] in REFUSED, "the first try is the system's own pick"
    assert len(trap._sockets) == 2, "TCP and UDP of 127.0.0.1 (::1 is not there)"


def test_the_resolver_trap_with_a_port_asked_for_tries_it_once_per_address_and_says_the_last_error(monkeypatch):
    import _leaks

    layer = _TrapLayer(refuse_all=True)
    trap = _trap_with(monkeypatch, layer, 5300)
    with pytest.raises(OSError) as caught:
        trap._bind()
    assert layer.udp_tries == [5300] * len(_leaks.TRAP_ADDRESSES), "a port asked for (53 on the root leg) is not replaced by another"
    assert "10013" in str(caught.value) and "port 5300" in str(caught.value), str(caught.value)
