"""A proxied session declares Firefox's DoH in mode 3 through the proxy; a session without a proxy declares no DoH.

The contract (core-19): with a proxy, every name the browser resolves goes to DoH, and a DoH request is a channel like
any other, so it leaves through the proxy of the context of the lookup. What the core writes to make that so:

  network.trr.mode = 3                     no fallback to the resolver of the machine
  network.trr.uri = https://mozilla.cloudflare-dns.com/dns-query
  network.trr.bootstrapAddr = <an IP>      the DoH host is found with no DNS at all
  network.trr.exclude-etc-hosts = False    the hosts file of the machine answers nothing
  network.trr.excluded-domains = <proxy>   ONLY when the proxy is given by NAME: the one name the system resolver
                                           answers (DoH needs the proxy); an address needs no lookup
  network.proxy.allow_bypass = False       no channel falls back to direct when the proxy stumbles
  network.parental_controls_cached_state = False   declared off, as retail without the service

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
def test_a_proxied_session_declares_doh_in_mode_3(compose, proxy):
    prefs = compose(proxy)
    assert prefs["network.trr.mode"] == 3
    assert prefs["network.trr.uri"] == URI
    ipaddress.ip_address(prefs["network.trr.bootstrapAddr"])  # raises unless it is an IP literal
    assert prefs["network.trr.exclude-etc-hosts"] is False
    assert prefs["network.proxy.allow_bypass"] is False
    assert prefs["network.parental_controls_cached_state"] is False


@pytest.mark.parametrize("proxy, host", [(NAMED, "gw.example.test"), (HTTP_WITH_CREDENTIALS, "gw.example.test")])
def test_a_proxy_named_by_a_name_is_the_one_name_the_system_resolver_answers(compose, proxy, host):
    assert compose(proxy)["network.trr.excluded-domains"] == host


@pytest.mark.parametrize("proxy", [BY_ADDRESS, HTTP_BY_V6_ADDRESS], ids=["ipv4", "ipv6"])
def test_a_proxy_given_by_an_address_excludes_nothing(compose, proxy):
    assert "network.trr.excluded-domains" not in compose(proxy)


def test_the_bootstrap_address_is_a_public_address_and_not_the_proxy(compose):
    bootstrap = ipaddress.ip_address(compose(BY_ADDRESS)["network.trr.bootstrapAddr"])
    assert bootstrap.is_global and str(bootstrap) != "203.0.113.9"


def test_a_session_without_a_proxy_declares_no_doh(compose):
    prefs = compose(None)
    assert [k for k in prefs if k.startswith("network.trr.")] == []
    assert prefs.get("network.proxy.allow_bypass") is not False, "no proxy, no closed fallback"
    assert prefs["network.parental_controls_cached_state"] is False, "declared off with or without a proxy"


def test_the_doh_prefs_are_the_same_for_the_same_proxy_whatever_the_seed():
    """Nothing about the DoH is drawn from the seed: two sessions behind the same proxy declare the same DoH."""
    def trr(seed):
        prefs = compose_session_prefs(generate_profile(seed=seed), locale=decide_session_locale("en-US"),
                                      timezone="America/New_York", proxy=NAMED).prefs
        return {k: v for k, v in prefs.items() if k.startswith("network.trr.")}

    assert trr(1) == trr(2) == trr(970411)
