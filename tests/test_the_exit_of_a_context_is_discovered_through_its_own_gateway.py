"""The core discovers the exit of a context's proxy by asking the PROXY, by NAME, and nothing else leaves.

When a context is given a SOCKS5 proxy, the core measures what it needs before it sends ``Browser.setContextProxy``
(``invisible_core._udp_exit.measure_proxy_udp``): the TCP exit through that proxy, with the services of the core
(``_geo._IP_ECHO_ENDPOINTS``), and the UDP exit beside it. The names are resolved by the PROXY (remote DNS: the host's own resolver is
never asked), through the gateway of THAT context and no other. The DoH e2e declare this discovery away (their subject is the roads
of the BROWSER, and the asserts on the CONNECTs of a gateway would otherwise see the core's three names), so this test keeps what
the declaration hides: the discovery goes through the gateway of the context, by name, only to the services of the core, and a
gateway that refuses them never makes the measurement say ``coherent``.
"""
import pytest

from invisible_core import _geo, _udp_exit

pytestmark = pytest.mark.unit

#: The services of the core, in the order it asks them (https, so port 443).
ECHO = [(url.split("//", 1)[1], 443) for url in _geo._IP_ECHO_ENDPOINTS]
SERVICES = {url.split("//", 1)[1] for url in _geo._IP_ECHO_ENDPOINTS + _geo._IPV6_ECHO_ENDPOINTS}


def test_the_exit_of_a_context_is_asked_to_its_own_gateway_by_name(gateway):
    mine = gateway("gw-mine")
    other = gateway("gw-other")
    command = _udp_exit.context_proxy_command(mine.proxy)
    assert command is not None
    assert mine.connects[:len(ECHO)] == ECHO, "the discovery goes through the gateway of the context, by name, in the core's order"
    assert {host for host, _port in mine.connects} <= SERVICES, (
        "only the services of the core are asked through it: %s" % (mine.connects,))
    assert other.connects == [], "the gateway of another context never sees it: %s" % (other.connects,)


def test_a_gateway_that_refuses_the_services_never_gives_a_coherent_udp(gateway):
    gw = gateway("gw-refusing")
    command = _udp_exit.context_proxy_command(gw.proxy)
    assert command["type"] == "socks"
    assert gw.refused[:len(ECHO)] == ECHO, "the gateway refused the services: %s" % (gw.refused,)
    assert command["udp"] != "coherent", "a measurement that could not run is never coherent: %s" % (command,)
