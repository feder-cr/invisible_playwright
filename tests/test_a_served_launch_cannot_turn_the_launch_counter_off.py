"""A client of the server cannot turn the launch counter off or point it elsewhere (no browser).

The core refuses the two prefs of the counter in ``extra_prefs``; the server takes ``firefoxUserPrefs`` from a client and, for a launch
that names no session, puts them on top of the session it composes, and for a marked launch takes them as they come. Both roads are
closed here, at ``session_for_launch``, with the core's own refusal.
"""
from __future__ import annotations

import pytest

from invisible_core.prefs import USAGE_PING_PREFS, USAGE_PING_REFUSAL, USAGE_PING_URL
from invisible_playwright._juggler import server

pytestmark = pytest.mark.unit

ENABLED, URL = USAGE_PING_PREFS


@pytest.fixture(autouse=True)
def composed_session(monkeypatch):
    """The session the server composes for an unmarked launch, without composing one (that needs an engine)."""
    import invisible_playwright._session as session
    monkeypatch.setattr(session, "default_session", lambda executable, proxy=None: ({URL: USAGE_PING_URL, "x": 1}, {}))


@pytest.mark.parametrize("pref,value", [(ENABLED, False), (ENABLED, True), (ENABLED, None), (URL, "https://example.invalid/x"), (URL, None)])
@pytest.mark.parametrize("marked", [False, True], ids=["bare launch", "marked launch"])
def test_a_client_cannot_send_the_counter_prefs(pref, value, marked):
    prefs = {pref: value}
    if marked:
        prefs[server.COMPOSED_PREF] = True
    with pytest.raises(ValueError) as refused:
        server.session_for_launch(prefs, "firefox")
    assert pref in str(refused.value) and USAGE_PING_REFUSAL in str(refused.value)


def test_the_address_the_core_wrote_travels_in_a_marked_launch():
    prefs, _env = server.session_for_launch({server.COMPOSED_PREF: True, URL: USAGE_PING_URL, "y": 2}, "firefox")
    assert prefs[URL] == USAGE_PING_URL and prefs["y"] == 2 and ENABLED not in prefs


def test_a_bare_launch_gets_the_core_address_and_the_clients_other_prefs():
    prefs, _env = server.session_for_launch({"z": 3}, "firefox")
    assert prefs[URL] == USAGE_PING_URL and prefs["z"] == 3 and ENABLED not in prefs


def test_the_same_address_in_a_bare_launch_is_still_refused():
    """Only the address the CORE wrote, inside a composed launch, is let through: a bare client that names it is refused too."""
    with pytest.raises(ValueError):
        server.session_for_launch({URL: USAGE_PING_URL}, "firefox")
