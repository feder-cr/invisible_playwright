"""A launch has one deadline, the caller's, and a silence is not a refusal.

⛔ THE KNOWN-BAD INPUT, from the firefox-41 gate on Linux under load: the launch
waited for the browser's readiness within the caller's `launch(timeout=)`, then
sent `Browser.setBrowserProxy` with 10 s of its own and `Browser.enable` with 30.
The proxy command went unanswered for 10 s and the caller read "the engine
refused the proxy, so the browser was closed", while its 60 s still had time and
the engine had refused nothing.

The deadline is the core's now (`connection.launch` sets the proxy and keeps
it, `Connection.startup_send` spends what is left; its tests are there). What
is pinned here is this side of it: the caller's timeout reaches the core,
`0` as no limit; the proxy goes with it; `Browser.enable` is a startup command;
a launch that fails after the browser started closes it; and a startup past
its deadline reaches the client as Playwright's TimeoutError.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners, ReplyTimeout
from invisible_playwright._juggler import server as module
from invisible_playwright._juggler.server import (
    BrowserTypeDispatcher, JugglerServer)
from invisible_playwright._juggler.transport import _translate_exception
from invisible_playwright.sync_api import TimeoutError as PlaywrightTimeout

PROXY = {"server": "socks5://127.0.0.1:1080", "username": "u", "password": "p"}


class _Engine(EventListeners):
    """Answers every command and records how each was sent; `silent` names
    startup commands it does not answer before the launch's deadline."""

    def __init__(self, silent=()):
        EventListeners.__init__(self)
        self.silent = set(silent)
        self.bounds: dict = {}
        self.startup: list = []
        self.closed = False

    def send(self, method, params=None, session=None, timeout=30.0):
        self.bounds[method] = timeout
        return {"browserContextId": "ctx-1", "targetId": "t-1"}

    def startup_send(self, method, params=None):
        self.startup.append(method)
        if method in self.silent:
            raise ReplyTimeout("Timeout 5000ms exceeded while the browser was "
                               "starting: %s: no response in 3s" % method)
        return self.send(method, params, timeout=None)

    def close(self, timeout=5.0):
        self.closed = True


def _launch(monkeypatch, engine, **params):
    server = JugglerServer()
    server.attach(type("R", (), {"emit_message": lambda self, m: None})())
    seen: dict = {}

    def fake_launch(executable, profile_dir, **kwargs):
        seen.update(kwargs)
        return engine

    monkeypatch.setattr(module.juggler, "launch", fake_launch)
    try:
        channel = BrowserTypeDispatcher(server).op_launch(
            dict({"executablePath": "x", "firefoxUserPrefs": {}}, **params))
        seen["browser"] = server.object(channel["browser"]["guid"])
    except BaseException:
        # Read BEFORE the server shuts down, which closes every connection
        # anyway: the launch itself must have closed the browser it started.
        engine.closed_when_launch_failed = engine.closed
        server.shutdown()
        raise
    seen["server"] = server
    return seen


@pytest.mark.unit
def test_the_caller_s_timeout_and_proxy_reach_the_core(monkeypatch):
    engine = _Engine()
    seen = _launch(monkeypatch, engine, proxy=PROXY, timeout=60000)
    seen["server"].shutdown()
    assert seen["ready_timeout"] == 60.0
    wanted = {"type": "socks", "host": "127.0.0.1", "port": 1080,
              "username": "u", "password": "p"}
    assert {k: seen["proxy"].get(k) for k in wanted} == wanted, seen["proxy"]
    assert "Browser.setBrowserProxy" not in engine.bounds, (
        "the proxy was sent here too, with a bound of its own")
    assert engine.startup == ["Browser.enable"]


@pytest.mark.unit
def test_timeout_zero_is_no_limit(monkeypatch):
    seen = _launch(monkeypatch, _Engine(), timeout=0)
    seen["server"].shutdown()
    assert seen["ready_timeout"] is None


@pytest.mark.unit
def test_a_launch_past_its_deadline_closes_the_browser_and_is_a_timeout(monkeypatch):
    engine = _Engine(silent={"Browser.enable"})
    with pytest.raises(ReplyTimeout) as caught:
        _launch(monkeypatch, engine, timeout=5000)
    assert engine.closed_when_launch_failed, (
        "a launch that failed left its browser running")
    client_side = _translate_exception(caught.value)
    assert isinstance(client_side, PlaywrightTimeout), type(client_side)
    assert "Timeout 5000ms exceeded" in str(client_side)


@pytest.mark.unit
def test_an_actionability_timeout_is_playwright_s_timeout():
    """`except TimeoutError` around a click must catch a click that timed out."""
    from invisible_core.juggler.actions import ElementNotActionable
    client_side = _translate_exception(ElementNotActionable("not visible"))
    assert isinstance(client_side, PlaywrightTimeout), type(client_side)


@pytest.mark.unit
def test_context_options_have_no_bound_of_their_own(monkeypatch):
    """newContext carries no timeout in Playwright, like newPage (#321)."""
    engine = _Engine()
    seen = _launch(monkeypatch, engine)
    try:
        engine.bounds.clear()
        seen["browser"].op_new_context({
            "proxy": PROXY, "extraHTTPHeaders": [{"name": "a", "value": "b"}],
            "offline": True, "geolocation": {"latitude": 1, "longitude": 2},
            "httpCredentials": {"username": "u", "password": "p"},
            "ignoreHTTPSErrors": True, "bypassCSP": True,
            "javaScriptEnabled": False, "hasTouch": True,
            "permissions": ["geolocation"], "locale": "de-DE",
            "viewport": {"width": 800, "height": 600},
            "serviceWorkers": "block"})
    finally:
        seen["server"].shutdown()
    bounded = {m: b for m, b in engine.bounds.items() if b is not None}
    assert len(engine.bounds) >= 12, engine.bounds
    assert not bounded, "context commands with a bound of their own: %r" % bounded
