"""The core declares the PROVIDER of Firefox's DoH for every session; the engine derives the rest from the proxies it receives.

The contract: with a proxy, every name the browser resolves goes to DoH, and a DoH request is a channel like any other, so it
leaves through the proxy of the context of the lookup. What the core writes to make that so is only the provider, with or
without a proxy (a DoH state would be two declarations of one fact):

  network.trr.uri = https://mozilla.cloudflare-dns.com/dns-query
  network.trr.bootstrapAddr = <an IP>      the DoH host is found with no DNS at all
  network.proxy.allow_bypass = False       with a proxy: no channel falls back to direct when the proxy stumbles
  network.parental_controls_cached_state = False   declared off, as retail without the service

and NOT `network.trr.mode`, `network.trr.exclude-etc-hosts` or `network.trr.excluded-domains` (the engine derives the state, the
hosts file and the proxy's own name from the proxies Juggler gives it).

Two doors are checked, because the e2e launches through the second: the core's composer, and the wrapper's own path
(`InvisiblePlaywright._build_prefs` and the core's proxy step, what a launch composes). Nothing here starts a browser or the network (the
suite's autouse fixture answers the egress lookup).
"""
from __future__ import annotations

import ipaddress

import pytest

from invisible_core import decide_session_locale
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import compose_session_prefs

from invisible_playwright import _session

pytestmark = pytest.mark.unit

URI = "https://mozilla.cloudflare-dns.com/dns-query"
NAMED = {"server": "socks5://gw.example.test:1080"}
BY_ADDRESS = {"server": "socks5://203.0.113.9:1080"}
HTTP_WITH_CREDENTIALS = {"server": "http://gw.example.test:3128", "username": "u", "password": "p"}
HTTP_BY_V6_ADDRESS = {"server": "http://[2001:db8::1]:3128"}


def _core(proxy):
    return compose_session_prefs(generate_profile(seed=42), locale=decide_session_locale("en-US"),
                                 timezone="America/New_York", proxy=proxy).prefs


def _wrapper(proxy):
    """What the launcher composes: the session's prefs (`_build_prefs`), then the core's proxy step
    (`configure_proxy`) on them, exactly as `launcher.__enter__` and `remote.op_launch` do."""
    from invisible_core import configure_proxy
    from invisible_playwright.launcher import InvisiblePlaywright

    session = InvisiblePlaywright(seed=42, binary_path="firefox.exe", humanize=False, proxy=proxy or None)
    geo = _session.session_geo(session._timezone, session._proxy, session._locale_requested)
    session._timezone, session._locale = geo.timezone, geo.locale
    prefs = session._build_prefs()
    configure_proxy(session._proxy, prefs)
    return prefs


@pytest.fixture(params=[_core, _wrapper], ids=["core", "wrapper"])
def compose(request):
    return request.param


@pytest.mark.parametrize("proxy", [NAMED, BY_ADDRESS, HTTP_WITH_CREDENTIALS, HTTP_BY_V6_ADDRESS],
                         ids=["socks-name", "socks-ip", "http-name", "http-ipv6"])
def test_a_proxied_session_declares_the_doh_provider_and_closes_the_bypass(compose, proxy):
    prefs = compose(proxy)
    assert prefs["network.trr.uri"] == URI
    ipaddress.ip_address(prefs["network.trr.bootstrapAddr"])  # raises unless it is an IP literal
    for derived in ("network.trr.mode", "network.trr.exclude-etc-hosts"):
        assert derived not in prefs, "%s is derived by the engine from the proxies, not declared by the core" % derived
    assert prefs["network.proxy.allow_bypass"] is False
    assert prefs["network.parental_controls_cached_state"] is False


@pytest.mark.parametrize("proxy", [NAMED, HTTP_WITH_CREDENTIALS, BY_ADDRESS, HTTP_BY_V6_ADDRESS],
                         ids=["socks-name", "http-name", "ipv4", "ipv6"])
def test_the_core_excludes_no_domain_the_engine_derives_the_proxys_own_name(compose, proxy):
    assert "network.trr.excluded-domains" not in compose(proxy)


def test_the_bootstrap_address_is_a_public_address_and_not_the_proxy(compose):
    bootstrap = ipaddress.ip_address(compose(BY_ADDRESS)["network.trr.bootstrapAddr"])
    assert bootstrap.is_global and str(bootstrap) != "203.0.113.9"


def test_a_session_without_a_proxy_declares_the_provider_and_no_state(compose):
    prefs = compose(None)
    assert sorted(k for k in prefs if k.startswith("network.trr.")) == ["network.trr.bootstrapAddr", "network.trr.uri"]
    assert prefs.get("network.proxy.allow_bypass") is not False, "no proxy, no closed fallback"
    assert prefs["network.parental_controls_cached_state"] is False, "declared off with or without a proxy"


def test_the_doh_prefs_are_the_same_for_the_same_proxy_whatever_the_seed():
    """Nothing about the DoH is drawn from the seed: two sessions behind the same proxy declare the same DoH."""
    def trr(seed):
        prefs = compose_session_prefs(generate_profile(seed=seed), locale=decide_session_locale("en-US"),
                                      timezone="America/New_York", proxy=NAMED).prefs
        return {k: v for k, v in prefs.items() if k.startswith("network.trr.")}

    assert trr(1) == trr(2) == trr(970411)
