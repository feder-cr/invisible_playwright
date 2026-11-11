"""A proxy given to a context declares where its UDP leaves from, MEASURED before the command is sent.

The core measures the UDP exit of a SOCKS5 proxy (`invisible_core._udp_exit`: UDP ASSOCIATE, then a STUN Binding request
with ICE's retransmissions, compared with the TCP exit it discovers through the same proxy) and the answer is the
optional field ``udp`` of ``Browser.setContextProxy``: ``coherent``, ``no`` or ``unmeasured``. Without it the engine
drops the UDP of every SOCKS5 proxy, so a context that is given its own proxy would never get a server-reflexive
candidate.

``new_context(proxy=...)`` of the sync client, of the async client and of a remote one all end in
``BrowserDispatcher._apply_context_options``: one place, tested here with a recorder in place of the engine. The
gateway is the fake of ``_socks_servers`` (a SOCKS5 server with a UDP relay and a STUN server only the relay reaches);
the STUN server of the measurement is DECLARED through ``STEALTHFOX_UDP_STUN_SERVERS`` as that unroutable address.

Credentials: used on the socket, never in a log record, the environment or argv.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

from _socks_servers import EXIT, STUN_NUMBER, STUN_PORT, coherent, mismatched, tcp_only
from invisible_core import _geo, _udp_exit, environment
from invisible_core.juggler.connection import EventListeners
from invisible_playwright._juggler.server import BrowserDispatcher, Server

pytestmark = pytest.mark.unit


class _Connection(EventListeners):
    def __init__(self, gateways=()):
        super().__init__()
        self.sent = []
        self.datagrams_before = {}
        self._gateways = gateways

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout=30):
        # How many datagrams the relays had carried when each command was sent: the measure comes BEFORE it.
        self.datagrams_before[len(self.sent)] = sum(len(g.datagrams) for g in self._gateways)
        self.sent.append((method, params))
        return {"browserContextId": "CTX"}


@pytest.fixture(autouse=True)
def _declared(monkeypatch):
    monkeypatch.setenv(environment.UDP_STUN_SERVERS, "%s:%d" % (STUN_NUMBER, STUN_PORT))
    monkeypatch.setattr(_udp_exit, "_cache", {})
    # The fake gateway answers the exit question the way the product asks it (api.ipify.org through socks5h), but
    # these tests are about the measurement, so the TCP exit is the gateway's own declaration.
    monkeypatch.setattr(_geo, "discover_egress_ip", lambda proxy=None, **kw: EXIT)


def _apply(options, gateways=()):
    conn = _Connection(gateways)
    dispatcher = BrowserDispatcher(Server(), None, conn, "151.0")
    conn.sent.clear()
    dispatcher._apply_context_options("CTX", options)
    return conn


def _context_proxy(conn):
    found = [(i, p) for i, (m, p) in enumerate(conn.sent) if m == "Browser.setContextProxy"]
    assert len(found) == 1, conn.sent
    return found[0]


def test_a_coherent_socks5_context_proxy_declares_coherent_after_it_was_measured():
    with coherent() as gw:
        conn = _apply({"proxy": gw.proxy}, [gw])
        index, params = _context_proxy(conn)
        assert params["udp"] == "coherent"
        assert params["type"] == "socks" and params["browserContextId"] == "CTX"
        assert conn.datagrams_before[index] >= 1, "the Binding request was sent before the command"
        assert gw.associates == 1


def test_a_udp_that_leaves_from_another_address_declares_no():
    with mismatched() as gw:
        assert _context_proxy(_apply({"proxy": gw.proxy}, [gw]))[1]["udp"] == "no"


def test_a_proxy_that_refuses_udp_declares_no():
    with tcp_only() as gw:
        assert _context_proxy(_apply({"proxy": gw.proxy}, [gw]))[1]["udp"] == "no"


@pytest.mark.parametrize("scheme", ["http", "https", "socks4"])
def test_a_proxy_that_is_not_socks5_declares_no_field_and_opens_no_connection(scheme):
    with coherent() as gw:
        proxy = {"server": "%s://127.0.0.1:%d" % (scheme, gw.port)}
        params = _context_proxy(_apply({"proxy": proxy}, [gw]))[1]
        assert "udp" not in params
        assert gw.associates == 0 and not gw.connects


def test_what_the_caller_wrote_under_udp_is_not_declared():
    with tcp_only() as gw:
        params = _context_proxy(_apply({"proxy": dict(gw.proxy, udp="coherent")}, [gw]))[1]
    assert params["udp"] == "no"


def test_one_proxy_is_measured_once_however_many_contexts_are_given_it():
    with coherent() as gw:
        for _ in range(3):
            assert _context_proxy(_apply({"proxy": dict(gw.proxy)}, [gw]))[1]["udp"] == "coherent"
        assert gw.associates == 1


def test_the_cache_is_keyed_without_the_secret():
    with coherent(user="a", password="hunter2-zzz") as gw:
        _apply({"proxy": gw.proxy}, [gw])
    assert _udp_exit._cache
    for key in _udp_exit._cache:
        assert "hunter2-zzz" not in repr(key)


def test_the_credentials_are_used_on_the_socket_and_nowhere_else(caplog):
    user, password = "u-7c1e4b9a02", "p-3d5f8a6e41"
    env_before = dict(os.environ)
    with coherent(user=user, password=password) as gw:
        with caplog.at_level(logging.DEBUG):
            params = _context_proxy(_apply({"proxy": gw.proxy}, [gw]))[1]
        assert params["udp"] == "coherent"
    text = "\n".join(r.getMessage() + repr(r.args) + str(r.exc_info) for r in caplog.records)
    assert user not in text and password not in text
    assert dict(os.environ) == env_before
    assert not [a for a in sys.argv if user in a or password in a]


def test_a_context_without_its_own_proxy_measures_nothing():
    with coherent() as gw:
        conn = _apply({}, [gw])
    assert not [m for m, _ in conn.sent if m == "Browser.setContextProxy"]
    assert gw.associates == 0


def test_a_context_proxy_that_cannot_be_read_is_refused_and_nothing_is_measured():
    from invisible_playwright._juggler.server import ProtocolException as Refusal
    with coherent() as gw:
        with pytest.raises(Refusal):
            _apply({"proxy": {"server": "ftp://127.0.0.1:%d" % gw.port}}, [gw])
        assert gw.associates == 0


# the launch: the proxy the launcher composes carries what its geo step measured --------------------------
def test_the_launchers_proxy_carries_the_measure_of_its_geo_step():
    from invisible_core import configure_proxy
    from invisible_core._geo import SessionGeo
    geo = SessionGeo("Asia/Tokyo", EXIT, udp_exit="coherent")
    declared = configure_proxy({"server": "socks5://10.0.0.1:1080"}, {}, udp=geo.udp_exit)
    from invisible_core import parse_proxy
    assert parse_proxy(declared).as_engine_command()["udp"] == "coherent"


@pytest.mark.parametrize("module", ["launcher", "async_api", "_juggler.remote"])
def test_every_entry_point_hands_the_geo_measure_to_configure_proxy(module):
    """The three places a session is composed (sync launcher, async launcher, `launch_server`) call
    `configure_proxy` with the udp their geo step measured: a call without it would drop it for that entry point."""
    import ast
    import importlib
    import inspect
    source = inspect.getsource(importlib.import_module("invisible_playwright." + module))
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
             and (getattr(n.func, "id", None) in ("_configure_proxy_shared", "configure_proxy"))]
    assert calls, module
    for call in calls:
        assert "udp" in [k.arg for k in call.keywords], (module, ast.dump(call)[:120])


def test_the_launch_and_the_context_of_the_server_both_declare_the_measure():
    """`op_launch` (a client that composed no session) and the context options go through the core's declaration of
    the proxy: a command built from the bare `parse_proxy(...)` would drop the field for that road."""
    import inspect

    from invisible_playwright._juggler import server
    source = inspect.getsource(server)
    assert "declare_launch_proxy(params[\"proxy\"])" in source
    assert "declare_context_proxy(params[\"proxy\"])" in source
    assert "parse_proxy(params[\"proxy\"]).as_engine_command()" not in source
