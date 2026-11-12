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
#: retail Firefox on a network that drops UDP loads the later visits as fast as the first (15 of 15 sessions, h2/h2/h2): an h3
#: attempt that waits for a timeout before it falls back to h2 would be seconds.
LOAD_MARGIN_MS = 1500

#: The visits of one page in one context, by kind of road. With NO proxy and the native DNS the retail is measured in loopback
#: and is exact: h2, h3, h3 over three visits. BEHIND A PROXY the names are resolved by DoH, the browser learns the Alt-Svc
#: from the first answer and the retail (Windows 159, DoH forced like our core, 15 sessions per condition) takes its time to
#: move to h3: with UDP free h2/h2/h3 in 9 sessions, h2/h2/h2 in 4 and h2/h3/h3 in 2 in the first runs, and h3 already on the
#: first visit (h3, h2, h2) in 3 sessions of 15 in the third, when the DNS answer carried the HTTPS record; so the page is
#: visited up to ``PROXY_VISITS`` times, and every proxy road makes the same number of visits (a road where UDP is dropped
#: reads h2 on all of them, as the retail does in 15 of 15: the one outcome that repeats). The fixture's first visit is h2
#: because it is reached by an address (no HTTPS record exists), not because the retail never reads h3 first.
DIRECT_VISITS = 3
PROXY_VISITS = 5

#: The roads. ``direct``: no proxy, native DNS. ``socks5_coherent``: a SOCKS5 proxy whose UDP the core measured coherent (UDP
#: free behind the proxy). The others are "a router that drops UDP": a SOCKS5 proxy that refuses UDP, whose UDP leaves
#: elsewhere or that never answers; or an HTTP proxy.
_DIRECT_ROAD = "direct"
_H3_PROXY_ROAD = "socks5_coherent"
_H2_ROADS = ("socks5_no_udp", "socks5_mismatched", "socks5_unmeasured", "http_proxy")
ROADS = (_DIRECT_ROAD, _H3_PROXY_ROAD) + _H2_ROADS


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


def visits_for(road: str) -> int:
    """How many times the page is visited on ``road``: three with no proxy, :data:`PROXY_VISITS` behind any proxy."""
    if road == _DIRECT_ROAD:
        return DIRECT_VISITS
    if road in ROADS:
        return PROXY_VISITS
    raise KeyError("no such road: %r (roads: %s)" % (road, ", ".join(ROADS)))


def expected_protocols(road: str) -> str:
    """What a retail Firefox reads (``nextHopProtocol`` of the document) over the visits of ``road``, as a sentence."""
    visits = visits_for(road)
    if road == _DIRECT_ROAD:
        return "h2, h3, h3 over %d visits (no proxy, native DNS)" % visits
    if road == _H3_PROXY_ROAD:
        return ("h2 on the first visit, then h3 from a later visit on and h3 on every one after it, within %d visits "
                "(retail with DoH and UDP free: h2/h2/h3 in 9 of 15, h2/h2/h2 in 4, h2/h3/h3 in 2, and h3 first in 3 of 15 in the "
                "third run when the DNS answer carries the HTTPS record; this fixture has none: h2 first)" % visits)
    return "h2 on every one of %d visits, as fast as the first (retail with UDP dropped: h2/h2/h2 in 15 of 15)" % visits


def no_https_record(host: str, doh: Any = None) -> List[str]:
    """What is wrong with the claim "the first visit of this fixture is h2", as sentences; an empty list when nothing is.

    The retail reads h3 at the first visit when the DNS answer for the site carries the HTTPS record (type 65): the third
    retail run did in 3 sessions of 15. The fixture reads h2 first only if no such record can reach the browser: the server
    is reached by an ADDRESS (no name, no lookup), and the FakeDoh, when one is in the path, has never served a type 65 with
    data (``FakeDoh.https_served``)."""
    problems: List[str] = []
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        problems.append("the server is reached by the name %r: its DNS answer could carry an HTTPS record, and the retail "
                        "reads h3 at the first visit when it does" % host)
    served = list(getattr(doh, "https_served", ())) if doh is not None else []
    if served:
        problems.append("the FakeDoh served an HTTPS record (type 65) for %s: the first visit may read h3, as the retail does"
                        % sorted(set(served)))
    return problems


def judge_visits(road: str, protocols: Sequence[str], walls_ms: Sequence[float]) -> List[str]:
    """What is wrong with the visits of one page on ``road``, as sentences; an empty list when nothing is.

    ``protocols`` is the ``nextHopProtocol`` of each visit and ``walls_ms`` the time ``page.goto`` took to the load event;
    both have :func:`visits_for` entries. The rules, from the retail:

    * ``direct``: exactly h2, h3, h3.
    * ``socks5_coherent``: the first visit is h2 (the fixture is reached by an address: no HTTPS record exists, so nothing
      tells the browser about h3 before the first connection; the retail itself reads h3 first when the DNS answer carries
      the record, so this is a rule of the fixture, checked with :func:`no_https_record`); every later visit is h2 or h3, but once one is h3 all the following are (h3 never goes back to h2); and at
      least one visit is h3 (it is NOT required at the second: h2/h2/h3 is the commonest retail sequence).
    * the roads where UDP is dropped: h2 on every visit, and no visit loads more than :data:`LOAD_MARGIN_MS` slower than the
      first (a fallback that waits for a timeout is a tell)."""
    problems: List[str] = []
    protocols, walls_ms = list(protocols), list(walls_ms)
    visits = visits_for(road)
    said = "a retail Firefox reads %s on this road" % expected_protocols(road)
    if len(protocols) != visits:
        problems.append("%d protocols for %d visits: %s" % (len(protocols), visits, protocols))
    if len(walls_ms) != visits:
        problems.append("%d load times for %d visits: %s" % (len(walls_ms), visits, walls_ms))
    if protocols and protocols[0] == "h3":
        problems.append("visit 1 read h3: in this fixture nothing had told the browser yet that the server speaks h3 (no "
                        "name, no HTTPS record): something told it, or the fixture changed")
    if len(protocols) == visits:
        if road == _DIRECT_ROAD:
            if protocols != ["h2", "h3", "h3"]:
                problems.append("the protocols of the %d visits are %s, %s" % (visits, protocols, said))
        elif road == _H3_PROXY_ROAD:
            first = protocols.index("h3") if "h3" in protocols else None
            if first is None:
                problems.append("none of the %d visits read h3: %s, %s" % (visits, protocols, said))
            elif set(protocols) - {"h2", "h3"} or protocols[0] != "h2" or "h2" in protocols[first:]:
                problems.append("the protocols of the %d visits are %s, %s" % (visits, protocols, said))
        elif protocols != ["h2"] * visits:
            problems.append("the protocols of the %d visits are %s, %s" % (visits, protocols, said))
    if len(walls_ms) == visits and road in _H2_ROADS:
        for number in range(2, visits + 1):
            if walls_ms[number - 1] > walls_ms[0] + LOAD_MARGIN_MS:
                problems.append("visit %d loaded in %d ms against %d ms for visit 1 (more than %d ms slower): an h3 attempt "
                                "that waited for a timeout before the fallback" % (number, walls_ms[number - 1], walls_ms[0], LOAD_MARGIN_MS))
    return problems
