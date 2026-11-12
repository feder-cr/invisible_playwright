"""The DoH e2e launch their browser on a virtual display, whatever machine they run on (run 43176569993: 19 of 19 of
wrapper.e2e.doh on Linux died at the launch with "no DISPLAY environment variable specified": the files launched a
headed browser with a window on a runner that has no screen).

``headless=True`` is a headed browser on a display the wrapper starts itself, as in the UDP and realness e2e. A test
does not behave differently by the display of the machine it runs on, so the launchers do not look at ``DISPLAY``.
Nothing is launched here: the session object holds the answer."""
from __future__ import annotations

import sys

import pytest

import test_doh_through_the_proxy_e2e as through_the_proxy
import test_doh_under_stress_e2e as under_stress
import test_ice_names_by_the_context_proxy_e2e as ice_names

pytestmark = pytest.mark.unit

LAUNCHERS = {
    "the DoH e2e": lambda: through_the_proxy._launch("/nowhere/firefox", None),
    "the stress e2e": lambda: under_stress._launch_with_a_short_negative_cache("/nowhere/firefox"),
    "the ICE names e2e": lambda: ice_names._launch("/nowhere/firefox", None),
}


@pytest.mark.parametrize("host", ["linux without a display", "linux with a display", "windows"])
@pytest.mark.parametrize("which", sorted(LAUNCHERS))
def test_every_launcher_asks_for_a_virtual_display_on_every_host(monkeypatch, which, host):
    monkeypatch.setattr(sys, "platform", "win32" if host == "windows" else "linux")
    if host == "linux with a display":
        monkeypatch.setenv("DISPLAY", ":0")
    else:
        monkeypatch.delenv("DISPLAY", raising=False)
    assert LAUNCHERS[which]()._headless is True


def test_what_the_caller_asks_for_wins():
    assert through_the_proxy._launch("/nowhere/firefox", None, headless=False)._headless is False
