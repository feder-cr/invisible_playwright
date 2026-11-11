"""What the e2e tests of the UDP line share: the STUN servers the core measures from, the TCP exit of each gateway, what the
launch declared, and the sequence of protocols a page reads on each road.

THE CORE MEASURES THE UDP EXIT of a SOCKS5 proxy before it declares it (``invisible_core._udp_exit``): a STUN Binding sent
through the ASSOCIATE of the proxy to a list of servers, compared with the TCP exit it discovers through the same proxy. Only
``udp: coherent`` makes the engine send UDP to the relay; ``no`` and ``unmeasured`` are "a router that drops UDP" (HTTP/3
too). The list of servers is the explicit launch parameter ``udp_stun_servers`` (``"ip:port"``, IPv4 literals), so a hermetic
test points the measurement at the fake STUN server only its gateway's relay reaches. The caller never declares ``udp``.

Nothing here opens a browser; the unit test ``test_the_udp_line_helpers.py`` holds it to what it says.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Callable, Dict, List, Optional, Sequence

#: How much slower than the first visit a later visit may load, in milliseconds, on the roads where UDP does not work. A
#: retail Firefox on a network that drops UDP loads the second visit as fast as the first (``captures/h3-fallback``, 15 of 15):
#: an h3 attempt that waits for a timeout before it falls back would be seconds.
LOAD_MARGIN_MS = 1500

#: The three visits of one page in one context: the first learns the Alt-Svc, the later ones may use h3 (retail 159, 15 of 15).
VISITS = 3

#: The protocol a page reads (``nextHopProtocol`` of the navigation entry) on each visit, by road. With UDP free (no proxy, or a
#: SOCKS5 proxy whose UDP the core measured ``coherent``) the first visit is h2 and the next two h3. With UDP dropped (a SOCKS5
#: proxy that refuses UDP, whose UDP leaves elsewhere or that never answers; or an HTTP proxy) every visit is h2.
_H3_ROADS = ("direct", "socks5_coherent")
_H2_ROADS = ("socks5_no_udp", "socks5_mismatched", "socks5_unmeasured", "http_proxy")


def _literal(entry: Any) -> str:
    """``"ip:port"`` of an IPv4 literal and a port, or ValueError (the form the launch parameter takes)."""
    host, sep, port = str(entry).rpartition(":")
    if not sep or ipaddress.ip_address(host).version != 4 or not 1 <= int(port) <= 65535:
        raise ValueError("%r is not an \"ip:port\" IPv4 literal" % (entry,))
    return "%s:%s" % (ipaddress.ip_address(host), int(port))


def measure_servers(gateway: Any) -> List[str]:
    """The ``udp_stun_servers=`` of a launch behind ``gateway``: the server its relay answers for the core's measurement.
    One place, so that no test writes a list of its own."""
    return [_literal(entry) for entry in gateway.measure_servers]


def egress_by_proxy(*gateways: Any, direct: Optional[str] = None) -> Callable[..., str]:
    """A replacement for ``invisible_core._geo.discover_egress_ip``: the TCP exit of the proxy it is asked about, which is
    the one the gateway reports (no local gateway can answer the product's HTTPS question as itself). One gateway or
    several (a session proxy and a context proxy have each their own); the UDP probe that follows is NOT replaced, it runs
    through the relay for real. ``direct`` answers the discovery made with no proxy. An unknown proxy fails loudly."""
    exits = {gateway.server: gateway.tcp_exit for gateway in gateways}

    def discover(proxy: Optional[Dict[str, Any]] = None, *_args: Any, **_kwargs: Any) -> str:
        if not proxy:
            if direct is None:
                raise AssertionError("the TCP exit was asked with no proxy and the test gave none")
            return direct
        server = proxy.get("server") if isinstance(proxy, dict) else None
        if server not in exits:
            raise AssertionError("the TCP exit of %r was asked and no gateway of the test owns it: %s" % (server, sorted(exits)))
        return exits[server]

    return discover


def declared_udp(proxy: Dict[str, Any], servers: Sequence[str]) -> str:
    """The ``udp`` the launch measured for ``proxy`` with ``servers``: ``coherent``, ``no`` or ``unmeasured``. It is what the
    core remembered for the proxy (and declares to the engine); an AssertionError when the launch measured nothing, so
    ``unmeasured`` cannot be mistaken for an absent measurement."""
    from invisible_core import _udp_exit
    from invisible_core._proxy import parse_proxy

    endpoint = parse_proxy(proxy)
    key = _udp_exit.cache_key(endpoint, _udp_exit.stun_servers(list(servers)))
    entry = _udp_exit._cache.get(key)
    if entry is None:
        raise AssertionError("the launch measured nothing for %r with %s" % (proxy.get("server"), list(servers)))
    return entry[1]


def expected_protocols(road: str) -> List[str]:
    """The ``nextHopProtocol`` of the three visits of one page on ``road`` (a retail Firefox, measured)."""
    if road in _H3_ROADS:
        return ["h2", "h3", "h3"]
    if road in _H2_ROADS:
        return ["h2"] * VISITS
    raise KeyError("no such road: %r (roads: %s)" % (road, ", ".join(_H3_ROADS + _H2_ROADS)))


def judge_visits(road: str, protocols: Sequence[str], walls_ms: Sequence[float]) -> List[str]:
    """What is wrong with three visits of one page on ``road``, as sentences; an empty list when nothing is.

    ``protocols`` is the ``nextHopProtocol`` of each visit and ``walls_ms`` the time ``page.goto`` took to the load event.
    The protocols must be exactly the road's sequence; an h3 on the FIRST visit is named apart (nothing has told the browser
    yet that the server speaks h3). On a road where UDP is dropped the later visits load no more than
    :data:`LOAD_MARGIN_MS` slower than the first: a fallback that waits for a timeout is a tell."""
    problems: List[str] = []
    protocols, walls_ms = list(protocols), list(walls_ms)
    want = expected_protocols(road)
    if protocols and protocols[0] == "h3":
        problems.append("visit 1 read h3: nothing had told the browser the server speaks h3 yet")
    if protocols != want:
        problems.append("the protocols of the %d visits are %s, a retail Firefox reads %s on this road" % (VISITS, protocols, want))
    if len(walls_ms) != VISITS:
        problems.append("%d load times for %d visits: %s" % (len(walls_ms), VISITS, walls_ms))
    elif road in _H2_ROADS:
        for number in (2, 3):
            if walls_ms[number - 1] > walls_ms[0] + LOAD_MARGIN_MS:
                problems.append("visit %d loaded in %d ms against %d ms for visit 1 (more than %d ms slower): an h3 attempt "
                                "that waited for a timeout before the fallback" % (number, walls_ms[number - 1], walls_ms[0], LOAD_MARGIN_MS))
    return problems
