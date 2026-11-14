"""The server says nothing about the launch counter (no browser).

From invisible-core 64.69.0 (engine firefox-64) the counter has no pref: the engine's address is built in and it is
mandatory. The core declares no ``usage_ping`` pref and has no constant for one, so the server neither refuses nor
forwards anything about it. Known-bad input: the server as it was imported ``USAGE_PING_PREFS`` from the core at every
launch, which raises ImportError with core 64.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler import server

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def composed_session(monkeypatch):
    """The session the server composes for an unmarked launch, without composing one (that needs an engine)."""
    import invisible_playwright._session as session
    monkeypatch.setattr(session, "default_session", lambda executable, proxy=None: ({"x": 1}, {}))


@pytest.mark.parametrize("marked", [False, True], ids=["bare launch", "marked launch"])
def test_a_launch_composes_no_launch_counter_pref(marked):
    prefs = {"y": 2}
    if marked:
        prefs[server.COMPOSED_PREF] = True
    composed, _env = server.session_for_launch(prefs, "firefox")
    assert composed["y"] == 2
    assert not [k for k in composed if "usage_ping" in k]


def test_the_server_has_no_launch_counter_refusal():
    assert not hasattr(server, "refuse_the_launch_counter")
