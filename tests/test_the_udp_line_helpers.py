"""The helpers the e2e tests of the UDP line share work, without a browser (hermetic, loopback).

The core measures the UDP exit of a SOCKS5 proxy before it declares it (``invisible_core._udp_exit``), from the servers of the
launch parameter ``udp_stun_servers``. An e2e that expects the engine to use a relay is only as good as the measurement that
precedes it: if the gateway of the test did not answer the Binding the way the core asks it (a STUN request wrapped in the
SOCKS5 UDP header, to an IPv4 literal, through the ASSOCIATE), the road would be declared ``no`` and the engine would never
send the page's UDP there, and the e2e would fail for a reason that is not the product's. These tests hold the gateways of
``_socks_servers`` and the chain of ``_turn_chain`` to the real measurement of the core, and the helper that builds the list
to its form.
"""
from __future__ import annotations

import ipaddress

import pytest

import _turn_chain as chain_mod
from _socks_servers import EXIT, MEASURE_NUMBER, STUN_NUMBER, STUN_PORT, coherent, mismatched, silent_udp, tcp_only
from _udp_line import declared_udp, egress_by_proxy, measure_servers
from invisible_core import _geo, _udp_exit

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(_udp_exit, "_cache", {})


def test_the_measure_servers_are_ip_port_ipv4_literals_that_the_core_takes():
    for gateway in (coherent(), tcp_only(), mismatched(), silent_udp()):
        with gateway:
            servers = measure_servers(gateway)
            assert servers and all(isinstance(entry, str) for entry in servers)
            for entry in servers:
                host, _, port = entry.rpartition(":")
                assert ipaddress.ip_address(host).version == 4, entry
                assert 1 <= int(port) <= 65535, entry
            assert _udp_exit.stun_servers(servers), "the core refused the list the helper builds"


def test_the_measure_server_is_not_the_one_the_page_asks():
    """So that what the measurement sent through the relay is never mistaken for what the page sent."""
    with coherent() as gateway:
        assert measure_servers(gateway) == ["%s:%d" % (MEASURE_NUMBER, STUN_PORT)]
        assert MEASURE_NUMBER != STUN_NUMBER


def test_the_helper_refuses_a_name_or_a_bad_port():
    class Named:
        measure_servers = ["stun.example.test:3478"]

    class Ported:
        measure_servers = ["198.51.100.6:0"]

    for gateway in (Named(), Ported()):
        with pytest.raises(ValueError):
            measure_servers(gateway)


@pytest.mark.parametrize("make, verdict", [
    (lambda: coherent(), "coherent"),
    (lambda: coherent(user="alice", password="s3cret"), "coherent"),
    (lambda: mismatched(), "no"),
    (lambda: tcp_only(), "no"),
    (lambda: silent_udp(), "unmeasured"),
], ids=["coherent", "coherent_with_credentials", "mismatched", "tcp_only", "silent_udp"])
def test_the_gateway_answers_the_core_measurement_the_way_its_name_says(make, verdict):
    """The real measurement of the core, against the gateway, asked at the server the helper hands out."""
    with make() as gateway:
        got = _udp_exit.measure_udp_exit(gateway.proxy, gateway.tcp_exit, udp_stun_servers=measure_servers(gateway),
                                         budget=1.5)
        assert got == verdict
        if gateway.udp == "relay":
            asked = {host for host, _port, _payload in gateway.datagrams}
            assert asked == {MEASURE_NUMBER}, "the measurement asked %s; the page's server is not its business" % asked


def test_the_chain_answers_the_core_measurement_coherent():
    """The road of the whole-chain e2e (TURN by name, DoH, page): its relay answers a Binding to the helper's server with the
    address its datagrams leave from, which is its TCP exit."""
    with chain_mod.Chain(rtt_ms=0, relay_host="127.0.0.1") as chain:
        assert chain.tcp_exit == chain_mod.EXIT_HOST
        got = _udp_exit.measure_udp_exit(chain.proxy, chain.tcp_exit, udp_stun_servers=measure_servers(chain), budget=1.5)
        assert got == "coherent"
        # And the TCP exit the e2e hands the core is the chain's own: with any other address it would read ``no``.
        other = _udp_exit.measure_udp_exit(chain.proxy, EXIT, udp_stun_servers=measure_servers(chain), budget=1.5)
        assert other == "no"


def test_each_proxy_has_the_tcp_exit_of_its_own_gateway():
    with coherent(exit_ip="203.0.113.7") as first, coherent(exit_ip="198.51.100.77") as second:
        discover = egress_by_proxy(first, second)
        assert discover(first.proxy) == "203.0.113.7"
        assert discover(dict(second.proxy, username="x")) == "198.51.100.77"
        assert discover(first.proxy, timeout=3) == "203.0.113.7"
        with pytest.raises(AssertionError):
            discover({"server": "socks5://127.0.0.1:1"})
        with pytest.raises(AssertionError):
            discover(None)
        assert egress_by_proxy(first, direct="192.0.2.9")(None) == "192.0.2.9"


def test_what_the_launch_declared_is_what_the_core_measured_and_an_absent_measurement_is_not_unmeasured(monkeypatch):
    with silent_udp() as mute, coherent(exit_ip="198.51.100.77") as good:
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(mute, good))
        with pytest.raises(AssertionError):
            declared_udp(mute.proxy, measure_servers(mute))          # nothing measured yet
        assert _udp_exit.measure_proxy_udp(good.proxy, measure_servers(good)) == "coherent"
        assert declared_udp(good.proxy, measure_servers(good)) == "coherent"
        # a measurement the launch made with ANOTHER list is not this one
        with pytest.raises(AssertionError):
            declared_udp(good.proxy, ["198.51.100.8:3478"])
