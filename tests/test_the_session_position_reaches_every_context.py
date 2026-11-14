"""The position the core declares for a session reaches the engine for EVERY context, and the user's own wins.

The core computes the position (``SessionGeo.geolocation_override()``: latitude, longitude, accuracy) from the exit
and the seed. This package carries it as the standard ``geolocation`` context option, in the session's context defaults,
so ``Browser.setGeolocationOverride`` is sent for the default context, for each ``new_context``/``new_page`` and for each
context a remote client opens; a ``geolocation=`` given by the caller replaces it; and the permission is never granted
here (the engine answers only a page that was granted).

Hermetic: the engine is a recording connection, the geo step answers a made-up exit at 45.0 / 9.0.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from invisible_core import SessionGeo, decide_session_locale
from invisible_core._geoposition import declared_position
from invisible_core.juggler.connection import EventListeners
from invisible_playwright import InvisiblePlaywright, _session
from invisible_playwright._juggler import remote
from invisible_playwright._juggler.server import BrowserDispatcher, Server
from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright
from _browser_census import closes_with_the_test

_PKG = pathlib.Path(__file__).resolve().parent.parent / "src" / "invisible_playwright"
EXIT = (45.0, 9.0)


def _geo_of(seed):
    p = declared_position(seed, *EXIT)
    return SessionGeo("Europe/Rome", "203.0.113.7", p.latitude, p.longitude,
                      decide_session_locale("it-IT"), p.accuracy)


def _past_geo(cls, seed=42, geo=None):
    """A session past the geo step of __enter__, without the network."""
    obj = cls(seed=seed, timezone="Europe/Rome", locale="it-IT")
    geo = geo if geo is not None else _geo_of(seed)
    obj._timezone, obj._locale = geo.timezone, geo.locale
    obj._geolocation = geo.geolocation_override()
    return obj


class _Connection(EventListeners):
    def __init__(self):
        super().__init__()
        closes_with_the_test(self)
        self.sent = []

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params))
        return {"browserContextId": "CTX-%d" % len(self.sent)}


def _dispatcher():
    conn = _Connection()
    return conn, BrowserDispatcher(Server(), None, conn, "151.0")


def _overrides(conn):
    return [p for m, p in conn.sent if m == "Browser.setGeolocationOverride"]


def _protocol_params(kwargs):
    """The session's defaults in the protocol's spelling, as the remote server and the Playwright client both give them."""
    return {("timezoneId" if k == "timezone_id" else k): v for k, v in kwargs.items()}


# -- the defaults carry the declaration --------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("cls", [InvisiblePlaywright, AsyncInvisiblePlaywright])
def test_the_context_defaults_carry_the_declared_position(cls):
    session = _past_geo(cls)
    want = declared_position(42, *EXIT).as_override()
    assert session._default_context_kwargs()["geolocation"] == want


@pytest.mark.unit
@pytest.mark.parametrize("cls", [InvisiblePlaywright, AsyncInvisiblePlaywright])
def test_when_the_coordinates_are_unavailable_the_defaults_carry_nothing(cls):
    no_point = SessionGeo("Europe/Rome", "203.0.113.7", locale=decide_session_locale("it-IT"))
    session = _past_geo(cls, geo=no_point)
    assert "geolocation" not in session._default_context_kwargs()


@pytest.mark.unit
def test_a_session_that_has_not_decided_yet_declares_nothing():
    assert "geolocation" not in InvisiblePlaywright(seed=42)._default_context_kwargs()


@pytest.mark.unit
def test_two_seeds_get_different_positions_and_one_seed_the_same():
    a1 = _past_geo(InvisiblePlaywright, 1)._default_context_kwargs()["geolocation"]
    a2 = _past_geo(InvisiblePlaywright, 1)._default_context_kwargs()["geolocation"]
    b = _past_geo(InvisiblePlaywright, 2)._default_context_kwargs()["geolocation"]
    assert a1 == a2 and a1 != b


# -- the command: the default context and every new context ----------------------------------------------------

@pytest.mark.unit
def test_the_default_context_gets_the_override_with_exactly_three_keys():
    """`launch_persistent_context` takes the defaults as context options and addresses the default context by absence."""
    conn, browser = _dispatcher()
    session = _past_geo(InvisiblePlaywright)
    browser._apply_context_options(None, _protocol_params(session._default_context_kwargs()))
    (params,) = _overrides(conn)
    assert "browserContextId" not in params, "the default context is addressed by absence"
    assert set(params) == {"geolocation"}
    assert set(params["geolocation"]) == {"latitude", "longitude", "accuracy"}
    assert params["geolocation"] == declared_position(42, *EXIT).as_override()


@pytest.mark.unit
def test_a_new_context_gets_the_override_for_its_own_id():
    conn, browser = _dispatcher()
    session = _past_geo(InvisiblePlaywright)
    browser.op_new_context(_protocol_params(session._default_context_kwargs()))
    (params,) = _overrides(conn)
    assert params["browserContextId"].startswith("CTX-")
    assert params["geolocation"] == declared_position(42, *EXIT).as_override()


@pytest.mark.unit
def test_the_permission_is_not_granted_by_us():
    conn, browser = _dispatcher()
    session = _past_geo(InvisiblePlaywright)
    browser.op_new_context(_protocol_params(session._default_context_kwargs()))
    assert not [m for m, _ in conn.sent if m in ("Browser.grantPermissions", "Browser.setPermissions")]
    assert "permissions" not in session._default_context_kwargs()


# -- the sync and the async wrappers: new_context and new_page ---------------------------------------------------

class _StubContext:
    def new_page(self, **kw):
        return object()

    def add_cookies(self, cookies):
        pass


class _StubBrowser:
    def __init__(self):
        self.context_kwargs, self.page_kwargs = [], []

    def new_context(self, **kw):
        self.context_kwargs.append(kw)
        return _StubContext()

    def new_page(self, **kw):
        self.page_kwargs.append(kw)
        page = type("_Page", (), {})()
        page.context = _StubContext()
        return page


@pytest.mark.unit
def test_new_context_and_new_page_get_the_declared_position():
    session = _past_geo(InvisiblePlaywright)
    browser = _StubBrowser()
    session._patch_new_context_defaults(browser)
    browser.new_context()
    browser.new_page()
    want = declared_position(42, *EXIT).as_override()
    assert browser.context_kwargs[0]["geolocation"] == want
    assert browser.page_kwargs[0]["geolocation"] == want


@pytest.mark.unit
def test_a_geolocation_given_by_the_user_wins():
    session = _past_geo(InvisiblePlaywright)
    browser = _StubBrowser()
    session._patch_new_context_defaults(browser)
    mine = {"latitude": 10.0, "longitude": 20.0}
    browser.new_context(geolocation=mine)
    browser.new_page(geolocation=mine)
    assert browser.context_kwargs[0]["geolocation"] == mine
    assert browser.page_kwargs[0]["geolocation"] == mine
    # ... and the next context, which names none, is back on the declared one: nothing leaks from the previous.
    browser.new_context()
    assert browser.context_kwargs[1]["geolocation"] == declared_position(42, *EXIT).as_override()


@pytest.mark.unit
def test_a_user_geolocation_reaches_the_engine_instead_of_the_declared_one():
    conn, browser = _dispatcher()
    session = _past_geo(InvisiblePlaywright)
    params = _protocol_params(session._default_context_kwargs())
    params["geolocation"] = {"latitude": 10.0, "longitude": 20.0, "accuracy": 5}
    browser.op_new_context(params)
    (sent,) = _overrides(conn)
    assert sent["geolocation"] == {"latitude": 10.0, "longitude": 20.0, "accuracy": 5}


@pytest.mark.unit
def test_a_context_that_closes_leaves_no_override_for_the_next():
    """Each context carries its own id: the first (user's) and the second (declared) never share a command."""
    conn, browser = _dispatcher()
    session = _past_geo(InvisiblePlaywright)
    mine = {"latitude": 10.0, "longitude": 20.0, "accuracy": 5}
    first = _protocol_params(session._default_context_kwargs())
    first["geolocation"] = mine
    browser.op_new_context(first)
    browser.op_new_context(_protocol_params(session._default_context_kwargs()))
    one, two = _overrides(conn)
    assert one["browserContextId"] != two["browserContextId"]
    assert one["geolocation"] == mine
    assert two["geolocation"] == declared_position(42, *EXIT).as_override()


# -- the served browser: a remote client's context gets the session defaults ------------------------------------------

@pytest.mark.unit
def test_a_remote_clients_context_gets_the_declared_position_and_its_own_wins():
    session = _past_geo(InvisiblePlaywright)
    defaults = remote._context_defaults(session, None)
    assert defaults["geolocation"] == declared_position(42, *EXIT).as_override()

    class _Browser:
        guid = "browser"

    endpoint = remote.RemoteEndpoint.__new__(remote.RemoteEndpoint)
    endpoint.context_defaults = dict(defaults)
    endpoint.context_options_override = False
    endpoint.browser = _Browser()
    endpoint._pages = iter(range(10))
    plain = endpoint._prepared({"guid": "browser", "method": "newContext", "params": {}})
    assert plain["params"]["geolocation"] == defaults["geolocation"]
    mine = {"latitude": 10.0, "longitude": 20.0}
    own = endpoint._prepared({"guid": "browser", "method": "newContext", "params": {"geolocation": mine}})
    assert own["params"]["geolocation"] == mine
    page = endpoint._prepared({"guid": "browser", "method": "newPage", "params": {}})
    assert page["params"]["geolocation"] == defaults["geolocation"]


# -- the geo step is asked WITH the seed, in every entry --------------------------------------------------------

@pytest.mark.unit
def test_every_call_to_the_geo_step_hands_it_the_session_seed():
    """Without the seed the core declares nothing, so a call site that forgets it is a session with no position."""
    sites = []
    for path in _PKG.rglob("*.py"):
        if "_pw" in path.relative_to(_PKG).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in ("prepare_session_geo", "session_geo"):
                sites.append((path.name, node.lineno, any(k.arg == "seed" for k in node.keywords)))
            # asyncio.to_thread(prepare_session_geo, ...): the function is an argument
            if name == "to_thread" and node.args and getattr(node.args[0], "id", "") == "prepare_session_geo":
                sites.append((path.name, node.lineno, any(k.arg == "seed" for k in node.keywords)))
    assert len(sites) >= 4, sites
    assert [s for s in sites if not s[2]] == [], sites


@pytest.mark.unit
def test_the_session_geo_step_passes_the_seed_to_the_core(monkeypatch):
    import invisible_core
    seen = {}

    def fake(timezone, proxy, locale, *, seed=None):
        seen["seed"] = seed
        return _geo_of(7)

    # conftest's autouse patch replaced `_session.session_geo` with a fixed answer: undo it, the real one is under test.
    monkeypatch.undo()
    monkeypatch.setattr(invisible_core, "prepare_session_geo", fake)
    _session.session_geo("auto", None, "auto", seed=7)
    assert seen["seed"] == 7
