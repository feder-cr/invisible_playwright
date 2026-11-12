"""A proxy given to a context declares where its UDP leaves from, MEASURED before the command is sent.

The core measures the UDP exit of a SOCKS5 proxy (`invisible_core._udp_exit`: UDP ASSOCIATE, then a STUN Binding request
with ICE's retransmissions, compared with the TCP exit it discovers through the same proxy) and the answer is the
optional field ``udp`` of ``Browser.setContextProxy``: ``coherent``, ``no`` or ``unmeasured``. Without it the engine
drops the UDP of every SOCKS5 proxy, so a context that is given its own proxy would never get a server-reflexive
candidate. The caller never declares it: a ``udp`` key in the dict of a proxy is not read.

``new_context(proxy=...)`` of the sync client, of the async client and of a remote one all end in
``BrowserDispatcher._apply_context_options``: one place, tested here with a recorder in place of the engine. The
gateway is the fake of ``_socks_servers`` (a SOCKS5 server with a UDP relay and a STUN server only the relay reaches).

THE STUN SERVERS ARE A PARAMETER OF THE LAUNCH: ``InvisiblePlaywright(..., udp_stun_servers=[...])`` and
``launch_server(..., udp_stun_servers=[...])`` ("ip:port" IPv4 literals; None = the two public defaults). The session
keeps the list, the launch's geo step and every later context proxy use it, and none of it reaches the browser: not its
environment, argv, prefs or pipe.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

from _socks_servers import EXIT, STUN_NUMBER, STUN_PORT, coherent, mismatched, tcp_only
from invisible_core import _geo, _udp_exit
from invisible_core.juggler.connection import EventListeners
from invisible_playwright import _session
from invisible_playwright._juggler.server import (BrowserDispatcher, Server, UDP_STUN_PREF,
                                                  session_udp_stun_servers, take_session_motion)

pytestmark = pytest.mark.unit

#: The fake STUN server only the gateway's relay reaches, in the form the launch parameter takes.
STUN = ["%s:%d" % (STUN_NUMBER, STUN_PORT)]


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
def _fresh(monkeypatch):
    monkeypatch.setattr(_udp_exit, "_cache", {})
    # The fake gateway answers the exit question the way the product asks it (api.ipify.org through socks5h), but
    # these tests are about the measurement, so the TCP exit is the gateway's own declaration.
    monkeypatch.setattr(_geo, "discover_egress_ip", lambda proxy=None, **kw: EXIT)


def _apply(options, gateways=(), stun=STUN):
    conn = _Connection(gateways)
    dispatcher = BrowserDispatcher(Server(), None, conn, "151.0", udp_stun_servers=stun)
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


def test_the_caller_never_declares_udp_on_a_context_proxy():
    """Red on the code that read the key: a caller-written `coherent` for a proxy whose UDP exits elsewhere (or
    is refused) must come out as what was MEASURED."""
    with tcp_only() as gw:
        assert _context_proxy(_apply({"proxy": dict(gw.proxy, udp="coherent")}, [gw]))[1]["udp"] == "no"
    with mismatched() as gw:
        assert _context_proxy(_apply({"proxy": dict(gw.proxy, udp="coherent")}, [gw]))[1]["udp"] == "no"


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


def test_a_browser_built_without_a_list_measures_from_the_two_defaults():
    conn = _Connection()
    assert BrowserDispatcher(Server(), None, conn, "151.0").udp_stun_servers is None


# the launch: the list is a parameter of the session, kept on it, never given to the browser ------------------
def test_the_session_keeps_the_list_the_launch_named_and_the_server_gets_it():
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright
    expected = ["198.51.100.5:19302", "203.0.113.9:3478"]
    for cls in (InvisiblePlaywright, AsyncInvisiblePlaywright):
        session = cls(seed=1, udp_stun_servers=["198.51.100.5:19302", ("203.0.113.9", 3478)])
        assert session._udp_stun_servers == expected
        assert cls(seed=1)._udp_stun_servers is None


def test_a_bad_list_is_refused_by_every_public_entry_point():
    import inspect

    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright._juggler.remote import launch_server
    from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright
    for cls in (InvisiblePlaywright, AsyncInvisiblePlaywright):
        with pytest.raises(ValueError, match="udp_stun_servers"):
            cls(seed=1, udp_stun_servers=["stun.example.org:3478"])
    assert "udp_stun_servers" in inspect.signature(launch_server).parameters


def test_the_server_takes_the_list_out_of_the_prefs_before_the_profile():
    prefs = {"a.b": 1, UDP_STUN_PREF: "198.51.100.5:19302,203.0.113.9:3478"}
    assert session_udp_stun_servers(prefs) == ["198.51.100.5:19302", "203.0.113.9:3478"]
    rest, _seed, _budget = take_session_motion(prefs)
    assert UDP_STUN_PREF not in rest and rest == {"a.b": 1}
    assert session_udp_stun_servers({"a.b": 1}) is None


def test_op_launch_reads_the_list_before_the_prefs_are_cleaned_and_writes_a_profile_without_it():
    import inspect

    from invisible_playwright._juggler import server
    source = inspect.getsource(server.BrowserTypeDispatcher.op_launch)
    assert source.index("session_udp_stun_servers(launch_prefs)") < source.index("take_session_motion(launch_prefs)")
    assert source.index("take_session_motion(launch_prefs)") < source.index("write_user_js(")
    assert "launch_proxy_command(params[\"proxy\"], udp_stun_servers)" in source
    assert "parse_proxy(params[\"proxy\"]).as_engine_command()" not in source


def test_every_entry_point_hands_the_list_to_the_geo_step():
    """The sync launcher, the async launcher and `launch_server` call `prepare_session_geo` with the session's list."""
    import ast
    import importlib
    import inspect
    for module in ("launcher", "async_api", "_juggler.remote"):
        source = inspect.getsource(importlib.import_module("invisible_playwright." + module))
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "prepare_session_geo"]
        # (the async launcher passes the function to `asyncio.to_thread`: its keywords are on that call)
        calls += [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) == "to_thread"
                  and any(getattr(a, "id", None) == "prepare_session_geo" for a in n.args)]
        assert calls, module
        for call in calls:
            assert "udp_stun_servers" in [k.arg for k in call.keywords], module


def test_no_stun_address_reaches_the_browsers_environment_argv_prefs_or_pipe():
    from invisible_core import decide_session_locale
    from invisible_playwright import InvisiblePlaywright
    servers = ["198.51.100.5:19302", "203.0.113.9:3478"]
    session = InvisiblePlaywright(seed=7, udp_stun_servers=servers, binary_path="firefox")
    session._timezone, session._locale = "Asia/Tokyo", decide_session_locale("en-US")
    prefs = session._build_prefs()
    assert session_udp_stun_servers(prefs) == servers        # the server's own key is there...
    env = _session.build_env(network_declared="v4=198.51.100.7;v6=0", profile=session._profile,
                             executable=None, base_env={})
    # ...and what the engine is given: the prefs after the server took its own keys out, the environment, the pipe.
    browser_prefs, _seed, _budget = take_session_motion(prefs)
    pipe = repr(_udp_exit.launch_proxy_command({"server": "socks5://10.0.0.1:1080"}, servers))
    given = repr(browser_prefs) + repr(env) + pipe
    for needle in servers + ["198.51.100.5", "203.0.113.9"]:
        assert needle not in given, needle
    assert not [k for k in env if "STUN" in k.upper()]


# the launch of a client that composed no session ----------------------------------------------------------
def test_a_hand_made_launch_proxy_cannot_claim_a_coherent_udp():
    """`launch(proxy={..., "udp": "coherent"})` through a bare client: nothing was measured, so `unmeasured`."""
    with mismatched() as gw:
        command = _udp_exit.launch_proxy_command(dict(gw.proxy, udp="coherent"), STUN)
    assert command["udp"] == "unmeasured"
