"""HTTP/3 behind a proxy: visits of one page read the protocols a retail Firefox reads, road by road.

⛔ THE KNOWN-BAD INPUT: an engine that refuses HTTP/3 whenever a proxy is in use reads `h2` on every visit behind a SOCKS5 gateway
that relays UDP where the same Firefox reads `h3` after the first visits; one that reads `h3` at the FIRST visit behind a proxy
(the retail never does); and one that tries h3 over a UDP that goes nowhere makes the page wait for a timeout before it falls
back to h2.

THE REFERENCE is a retail Firefox 159 on Windows, measured with DoH forced like our core (15 sessions per condition, one
profile per session). With UDP free the sequence is h2/h2/h3 in 9 sessions, h2/h2/h2 in 4 and h2/h3/h3 in 2: NEVER h3 at the first
visit, and h3 does not have to come at the second. With UDP blocked it is h2/h2/h2 in 15 of 15, with no delay of the load. With no
proxy and the native DNS (measured in loopback) it is h2, h3, h3. The method is the retail's: one context, the same page again
and again (``?v=N`` keeps each visit off the HTTP cache), a pause of two seconds after each load so that the Alt-Svc it carries
settles, and the ``nextHopProtocol`` of the navigation entry of the document,
``performance.getEntriesByType('navigation')[0].nextHopProtocol``. What each road reads is in ``_udp_line.expected_protocols``
and is judged by ``_udp_line.judge_visits``.

THE VISITS. No proxy: three, exactly h2, h3, h3. Behind a proxy: FIVE, on every road (the same number for all, for symmetry), and
all five are done (no early stop: the judge is a pure function of the list, so it is deterministic). On the road with UDP free
the first visit must be h2, at least one visit must be h3 within the five, and once a visit is h3 the next ones are h3 too; the
second visit is not required to be h3. On the roads without usable UDP every one of the five is h2 and visits 2 to 5 load no more
than ``LOAD_MARGIN_MS`` slower than the first. Time: about 3 s a visit (load plus the pause), so about 15 s of browsing per
proxy test and 9 s for the one with no proxy.

THE ROADS. A proxy whose UDP the core MEASURED ``coherent`` (a STUN Binding through its ASSOCIATE came back from the address of
its TCP exit) carries QUIC through the relay, the server sees the relay and never this host, and the page ends up on h3 as the
retail does with UDP free. Every other proxy is "a router that drops UDP", and the engine acts on it: the SOCKS5 proxy
that refuses UDP (``no``), whose UDP leaves from another address (``no``) or that never answers (``unmeasured``), and an HTTP
proxy (which has no UDP at all): h2 on every visit, nothing QUIC reaches the server, no datagram of the page goes to a relay, and
no visit waits for a timeout. The core's measurement is not replaced: it asks the relay of each gateway for a Binding for real,
at the server the launch names (``udp_stun_servers``), and the test says what the launch declared.

The server is local, reached by its address (an address of this host that is not loopback: a loopback destination goes direct
whatever the proxy), with a self-signed certificate (the context ignores certificate errors); its TCP side speaks HTTP/2 and
carries ``Alt-Svc: h3`` (tests/_h3_server.py).
"""
from __future__ import annotations

import os
import socket
import time

import pytest

from _host_address import this_host_address
from _udp_line import declared_udp, egress_by_proxy, expected_protocols, judge_visits, measure_servers, visits_for

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

#: What the product's own probe of the egress is told when there is no proxy: RFC 5737 TEST-NET-3.
EXIT_DIRECT = "203.0.113.7"
#: What the core is expected to have measured, by road.
_DECLARED = {"socks5_coherent": "coherent", "socks5_no_udp": "no", "socks5_mismatched": "no", "socks5_unmeasured": "unmeasured"}
#: The pause after each load (the retail method), and the nextHopProtocol of the document.
PAUSE_MS = 2000
_NAV_JS = "() => performance.getEntriesByType('navigation')[0].nextHopProtocol"


def _binary():
    cand = os.environ.get("STEALTHFOX_E2E_BINARY") or os.environ.get("INVPW_BINARY_PATH")
    if not cand or not os.path.exists(cand):
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    return cand


def _gateway(road: str, host: str):
    from _socks_servers import coherent, mismatched, silent_udp, tcp_only
    make = {"socks5_coherent": coherent, "socks5_no_udp": tcp_only, "socks5_mismatched": mismatched,
            "socks5_unmeasured": silent_udp}[road]
    # The relay sits on the address of this host, as a real gateway's does: the engine's UDP sockets are bound to an interface.
    return make(user="alice", password="s3cret", relay_host=host)


def _visits(road, server, monkeypatch, discover, **launch):
    """Visit the page of ``server`` ``visits_for(road)`` times in one context; the nextHopProtocol of the document of each visit
    and the time ``page.goto`` took to the load event, in milliseconds."""
    from invisible_core import _geo
    from invisible_playwright import InvisiblePlaywright

    monkeypatch.setattr(_geo, "discover_egress_ip", discover)
    protocols, walls = [], []
    # Firefox turns HTTP/3 off when the certificate chain ends in a root that is not one of its own (a self-signed test
    # server always does): measured, the Alt-Svc is learned, the QUIC handshake of validation succeeds with the override and
    # Firefox then closes it itself (hasThirdPartyRoots), so the visits stay h2. A test with a certificate of its own turns
    # that check off; the product does not (it would change what Firefox does with a root it does not know).
    prefs = {"network.http.http3.disable_when_third_party_roots_found": False, **launch.pop("extra_prefs", {})}
    with InvisiblePlaywright(headless=True, binary_path=_binary(), timezone="America/New_York", locale="en-US",
                             extra_prefs=prefs, **launch) as browser:
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        for visit in range(1, visits_for(road) + 1):
            url = server.url if visit == 1 else "%s?v=%d" % (server.url, visit)
            started = time.monotonic()
            page.goto(url, wait_until="load", timeout=60000)
            walls.append(round((time.monotonic() - started) * 1000))
            page.wait_for_timeout(PAUSE_MS)           # let the later resources and the Alt-Svc settle
            protocols.append(page.evaluate(_NAV_JS))
    return protocols, walls


def _report(road, protocols, walls, server, gateway=None):
    said = ("the %d visits of road %s read %s (a retail Firefox reads %s), loaded in %s ms; the server answered TCP with "
            "ALPN %s and saw QUIC from %s" % (len(protocols), road, protocols, expected_protocols(road), walls,
                                              server.tcp_alpn, server.quic_peers))
    if gateway is not None:
        said += "; datagrams the relay carried: %s" % sorted({(h, p) for h, p, _data in gateway.datagrams})
    return said


def test_a_page_with_no_proxy_reads_h2_then_h3_then_h3(monkeypatch):
    import aioquic  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    from _h3_server import H3Server

    with H3Server(this_host_address()) as server:
        protocols, walls = _visits("direct", server, monkeypatch, egress_by_proxy(direct=EXIT_DIRECT))
        problems = judge_visits("direct", protocols, walls)
        assert not problems, "; ".join(problems) + ". " + _report("direct", protocols, walls, server)
        assert server.quic_peers, "the h3 visits did not reach the server by QUIC"


def test_a_page_behind_a_gateway_that_relays_udp_starts_on_h2_reaches_h3_within_five_visits_and_the_quic_goes_by_the_relay(monkeypatch):
    import aioquic  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    from _h3_server import H3Server

    host = this_host_address()
    with H3Server(host) as server, _gateway("socks5_coherent", host) as gateway:
        protocols, walls = _visits("socks5_coherent", server, monkeypatch, egress_by_proxy(gateway), proxy=gateway.proxy,
                                  udp_stun_servers=measure_servers(gateway))
        carried = [(h, p) for h, p, _data in gateway.datagrams if (h, p) == (host, server.port)]
        declared = declared_udp(gateway.proxy, measure_servers(gateway))
        said = _report("socks5_coherent", protocols, walls, server, gateway)
    assert declared == "coherent", "the core measured the gateway %s, not coherent: the engine would not use its relay. %s" % (declared, said)
    problems = judge_visits("socks5_coherent", protocols, walls)
    assert not problems, "; ".join(problems) + ". " + said
    assert carried, "the QUIC datagrams did not go by the relay of the gateway. " + said
    assert server.quic_peers and set(server.quic_peers) == {"127.0.0.1"}, (
        "the QUIC server saw %s: only the relay of the gateway (127.0.0.1) may reach it. %s" % (server.quic_peers, said))


@pytest.mark.parametrize("road", ["socks5_no_udp", "socks5_mismatched", "socks5_unmeasured"])
def test_a_page_behind_a_socks5_proxy_that_is_not_coherent_reads_h2_on_every_visit_and_loads_without_delay(monkeypatch, road):
    """A relay that is refused, that leaves from another address or that never answers is a router that drops UDP: every
    visit (five) is h2, nothing QUIC reaches the server, nothing of the page goes to the relay, and visits 2 to 5 load as fast as 1."""
    import aioquic  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    from _h3_server import H3Server

    host = this_host_address()
    with H3Server(host) as server, _gateway(road, host) as gateway:
        protocols, walls = _visits(road, server, monkeypatch, egress_by_proxy(gateway), proxy=gateway.proxy,
                                  udp_stun_servers=measure_servers(gateway))
        to_the_server = [(h, p) for h, p, _data in gateway.datagrams if (h, p) == (host, server.port)]
        declared = declared_udp(gateway.proxy, measure_servers(gateway))
        said = _report(road, protocols, walls, server, gateway)
    assert declared == _DECLARED[road], "the core declared udp=%s, the road is %s. %s" % (declared, _DECLARED[road], said)
    problems = judge_visits(road, protocols, walls)
    assert not problems, "; ".join(problems) + ". " + said
    assert not server.quic_peers, "a QUIC connection reached the server: %s. %s" % (server.quic_peers, said)
    assert not to_the_server, "the page's UDP went to a relay that was not measured coherent. " + said


def test_a_page_behind_an_http_proxy_reads_h2_on_every_visit_and_loads_without_delay(monkeypatch):
    """An HTTP proxy has no UDP at all: the engine refuses HTTP/3 behind it and the page falls to h2 by itself."""
    import aioquic  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip
    from _doh_servers import FakeDoh, relay
    from _h3_server import H3Server
    from _http_proxy import HttpProxy

    class ProxyToTheServer(HttpProxy):
        """The HTTP proxy of the tests relays a CONNECT to a loopback address only; this one also relays the one to the server."""
        target = None

        def _connect(self, conn, host, port, authorization):
            if (host, port) != self.target:
                return super()._connect(conn, host, port, authorization)
            upstream = socket.create_connection((host, port), timeout=10)
            self._record("CONNECT", host, port, authorization, 200)
            self._answer(conn, 200)
            try:
                relay(conn, upstream)
            finally:
                upstream.close()

    host = this_host_address()
    doh = FakeDoh({})
    try:
        with H3Server(host) as server:
            proxy = ProxyToTheServer("http1", doh)
            proxy.target = (host, server.port)
            try:
                protocols, walls = _visits("http_proxy", server, monkeypatch, lambda *_a, **_k: EXIT_DIRECT, proxy=proxy.proxy)
                connects = [c for c in proxy.connects if c == (host, server.port)]
            finally:
                proxy.close()
            said = _report("http_proxy", protocols, walls, server) + "; CONNECTs to the server: %d" % len(connects)
        problems = judge_visits("http_proxy", protocols, walls)
        assert not problems, "; ".join(problems) + ". " + said
        assert connects, "the page did not go through the proxy. " + said
        assert not server.quic_peers, "a QUIC connection reached the server direct, around the proxy: %s. %s" % (server.quic_peers, said)
    finally:
        doh.close()
