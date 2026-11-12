"""``preload_newtab`` of InvisiblePlaywright: the option travels to the core only when the caller asked.

A wrapper that always handed the kwarg to the core would raise TypeError on a pairing with a core that does not know it, for a caller who
never used it. ``None`` (default) hands over nothing; ``False`` / ``True`` arrive as given; the async class and ``launch_server`` carry it too.
"""
import inspect

import pytest

from invisible_playwright import InvisiblePlaywright

pytestmark = pytest.mark.unit
KEY = "browser.newtab.preload"


def _built(**kw):
    from invisible_core import decide_session_locale

    session = InvisiblePlaywright(seed=7, binary_path="firefox", **kw)
    session._timezone, session._locale = "Asia/Tokyo", decide_session_locale("en-US")
    return session._build_prefs()


def test_by_default_the_wrapper_asks_the_core_nothing_about_it(monkeypatch):
    from invisible_playwright import _session

    seen = {}
    real = _session.compose_session_prefs

    def spy(profile, **kw):
        seen.update(kw)
        kw.pop("preload_newtab", None)          # a core that does not know the option
        return real(profile, **kw)

    monkeypatch.setattr(_session, "compose_session_prefs", spy)
    prefs = _built()
    assert "preload_newtab" not in seen
    assert KEY not in prefs


def test_asked_to_turn_it_off_the_pref_is_false():
    pytest.importorskip("invisible_core")
    from invisible_core import prefs as core_prefs

    if "preload_newtab" not in inspect.signature(core_prefs.compose_session_prefs).parameters:
        pytest.skip("this core does not know preload_newtab yet")
    assert _built(preload_newtab=False)[KEY] is False
    assert _built(preload_newtab=True)[KEY] is True


def test_the_option_is_a_parameter_of_every_entry_point():
    from invisible_playwright import async_api
    from invisible_playwright._juggler import remote

    for fn in (InvisiblePlaywright.__init__, async_api.InvisiblePlaywright.__init__, remote.launch_server):
        assert "preload_newtab" in inspect.signature(fn).parameters, fn
