"""The session declares the host network to the engine from its seed.

One variable, STEALTHFOX_WEBRTC_NETWORK, drawn by the core from the session seed
and written with or without a proxy. Nothing here launches a browser.
"""
from __future__ import annotations

import pathlib

import pytest
from invisible_core import network_to_declare

from invisible_playwright._session import build_env

NETWORK = "STEALTHFOX_WEBRTC_NETWORK"
SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "invisible_playwright"


@pytest.mark.unit
@pytest.mark.parametrize("seed", [1, 42, 123456])
def test_build_env_carries_the_seed_declaration(seed):
    env = build_env(network_declared=network_to_declare(seed), base_env={})
    assert env[NETWORK] == network_to_declare(seed)


@pytest.mark.unit
@pytest.mark.parametrize("proxy", [None, {"server": "socks5://127.0.0.1:1"}])
def test_a_session_declares_its_seed_network_with_or_without_a_proxy(monkeypatch, proxy):
    from invisible_playwright import _session
    from invisible_playwright.launcher import InvisiblePlaywright
    # Nothing here launches a browser, and the engine is not what is asked about: under a local seal (the gate's, for a
    # candidate) there is no engine to download, and resolving one is not this test's business.
    monkeypatch.setattr(_session, "resolve_executable", lambda path: pathlib.Path("firefox"))
    ip = InvisiblePlaywright(seed=42, proxy=proxy)
    assert ip._build_env({})[NETWORK] == network_to_declare(42)


@pytest.mark.unit
@pytest.mark.parametrize("proxy", [None, {"server": "socks5://127.0.0.1:1"}])
def test_the_server_default_session_declares_the_network_of_its_seed(monkeypatch, proxy):
    """`default_session` (what a launch naming no session gets) declares the
    network of ITS seed, with or without a proxy."""
    from invisible_core import decide_session_locale
    from invisible_core._geo import SessionGeo
    from invisible_playwright import _session
    from invisible_playwright.launcher import InvisiblePlaywright

    seeds = []
    real_init = InvisiblePlaywright.__init__

    def spy(self, *a, **k):
        real_init(self, *a, **k)
        seeds.append(self.seed)

    monkeypatch.setattr(InvisiblePlaywright, "__init__", spy)
    monkeypatch.setattr(_session, "session_geo", lambda tz, px, loc: SessionGeo(
        "America/New_York", None, locale=decide_session_locale("en-US")))
    _prefs, env = _session.default_session(None, proxy)
    assert env[NETWORK] == network_to_declare(seeds[0])


@pytest.mark.unit
def test_the_library_adds_no_other_webrtc_variable():
    env = build_env(network_declared=network_to_declare(42), base_env={})
    assert "STEALTHFOX_WEBRTC_PUBLIC_IP" not in env
    assert "STEALTHFOX_WEBRTC_DISABLE_IPV6" not in env


@pytest.mark.unit
def test_no_source_file_spells_the_removed_names():
    removed = ("srflx_declared", "srflx_to_declare", "WEBRTC_PUBLIC_IP", "WEBRTC_DISABLE_IPV6")
    hits = [(p.name, w) for p in SRC.rglob("*.py")
            for w in removed if w in p.read_text(encoding="utf-8")]
    assert not hits, hits
