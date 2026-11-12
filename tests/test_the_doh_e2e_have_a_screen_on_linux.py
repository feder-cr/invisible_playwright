"""The DoH e2e launch a browser on a Linux machine that has no screen (run 43176569993: 19 of 19 of wrapper.e2e.doh on
Linux died at the launch with "no DISPLAY environment variable specified", under sudo and without a display).

Every launcher of those files asks for a virtual display when the machine has none, and leaves a machine that has one,
and Windows, as they are. Nothing is launched here: the session object holds the answer."""
from __future__ import annotations

import sys

import pytest

import _doh_support
import test_doh_through_the_proxy_e2e as through_the_proxy
import test_doh_under_stress_e2e as under_stress
import test_ice_names_by_the_context_proxy_e2e as ice_names

pytestmark = pytest.mark.unit

LAUNCHERS = {
    "the DoH e2e": lambda: through_the_proxy._launch("/nowhere/firefox", None),
    "the stress e2e": lambda: under_stress._launch_with_a_short_negative_cache("/nowhere/firefox"),
    "the ICE names e2e": lambda: ice_names._launch("/nowhere/firefox", None),
}


@pytest.fixture
def host(monkeypatch):
    def set_host(platform, display):
        monkeypatch.setattr(sys, "platform", platform)
        if display is None:
            monkeypatch.delenv("DISPLAY", raising=False)
        else:
            monkeypatch.setenv("DISPLAY", display)
    return set_host


@pytest.mark.parametrize("which", sorted(LAUNCHERS))
def test_a_linux_host_without_a_display_gets_a_virtual_one(host, which):
    host("linux", None)
    assert LAUNCHERS[which]()._headless is True


@pytest.mark.parametrize("which", sorted(LAUNCHERS))
def test_a_linux_host_with_a_display_and_windows_are_left_as_they_are(host, which):
    host("linux", ":0")
    assert LAUNCHERS[which]()._headless is False
    host("win32", None)
    assert LAUNCHERS[which]()._headless is False


def test_what_the_caller_asks_for_wins(host):
    host("linux", None)
    assert through_the_proxy._launch("/nowhere/firefox", None, headless=False)._headless is False
    assert _doh_support.a_display_when_the_host_has_none() == {"headless": True}
