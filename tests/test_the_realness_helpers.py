"""The judges and the gateway of the realness suite (``test_realness_behind_a_declared_exit_e2e.py``), held to what they say.

No browser: golden reads for the judges (each red case is one the e2e must catch), and the gateway and the site with a plain
socket client, so that "a connection the gateway did not open is a leak" is shown to work before a browser is trusted with it.
"""
from __future__ import annotations

import ipaddress
import socket
import struct

import pytest

import _realness_support as rs

pytestmark = pytest.mark.unit

EXIT = rs.EXITS["germany"]
HOST_LAN = "192.168.1.20"
GOOD_SDP = "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\nc=IN IP4 0.0.0.0\r\n"
MDNS_HOST = "candidate:0 1 UDP 2122252543 1460e928-16b3-4c66-80ad-04abcdef0000.local 54551 typ host"
SRFLX = "candidate:1 1 UDP 1686052863 %s 58555 typ srflx raddr 0.0.0.0 rport 0"


def _probe(*lines, sdp=GOOD_SDP):
    return {"candidates": list(lines), "sdp": sdp + "".join("a=%s\r\n" % line for line in lines), "errors": []}


# ---------------------------------------------------------------------------------------------------------- the table
def test_every_declared_exit_is_a_public_ipv4_address_of_its_own():
    addresses = list(rs.EXITS.values())
    assert len(set(addresses)) == len(addresses) >= 6
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        assert parsed.version == 4 and parsed.is_global, address


# ---------------------------------------------------------------------------------------------------------- webrtc
def test_a_coherent_gather_with_mdns_hosts_and_the_exit_as_srflx_is_clean():
    assert rs.webrtc_problems(_probe(MDNS_HOST, SRFLX % EXIT), EXIT, {HOST_LAN, "127.0.0.1"}) == []


@pytest.mark.parametrize("probe, fragment", [
    (_probe("candidate:0 1 UDP 2122252543 %s 54551 typ host" % HOST_LAN), "not mDNS"),
    (_probe(MDNS_HOST, SRFLX % rs.EXITS["japan"]), "server-reflexive"),
    (_probe(MDNS_HOST, "candidate:2 1 UDP 2122252542 2001:db8::7 54552 typ host"), "IPv6"),
    (_probe(MDNS_HOST, sdp="v=0\r\nc=IN IP4 0.0.0.0\r\na=ice-ufrag:x\r\na=candidate:9 1 UDP 1 10.0.0.8 5 typ host raddr "
                          "10.0.0.8 rport 5\r\n"), "private"),
    ({"candidates": [], "sdp": GOOD_SDP, "errors": [{"code": 701}]}, "no candidate"),
], ids=["raw_lan_host", "srflx_is_not_the_exit", "ipv6_behind_ipv4", "private_address_in_the_sdp", "no_candidate"])
def test_a_gather_that_shows_the_host_or_disagrees_with_the_exit_is_named(probe, fragment):
    problems = rs.webrtc_problems(probe, EXIT, {HOST_LAN})
    assert problems and any(fragment in p for p in problems), problems


def test_an_address_of_this_machines_interfaces_in_the_sdp_is_named_even_when_it_is_not_private():
    public_own = rs.EXITS["japan"]   # an interface can carry a public address: the exit of this test is another one
    problems = rs.webrtc_problems(_probe(MDNS_HOST, sdp=GOOD_SDP + "a=x-note:%s\r\n" % public_own), EXIT, {public_own})
    assert any("interfaces" in p for p in problems), problems


# --------------------------------------------------------------------------------------------------------------- zone
def test_the_offsets_of_a_zone_at_the_two_instants_follow_its_daylight_saving():
    assert rs.expected_offsets("Asia/Kolkata") == [330, 330]
    assert rs.expected_offsets("America/Los_Angeles") == [-480, -420]
    assert rs.expected_offsets("Australia/Melbourne") == [660, 600]       # the southern hemisphere runs the other way
    assert rs.expected_offsets("Asia/Tokyo") == [540, 540]


def _read(zone, **override):
    read = {"zone": zone, "offsets_by_date": rs.expected_offsets(zone), "offsets_by_intl": rs.expected_offsets(zone),
            "language": "de", "languages": ["de", "en-US", "en"], "intl": "de"}
    read.update(override)
    return read


def test_a_page_and_a_worker_that_say_the_zone_of_the_exit_are_clean():
    assert rs.timezone_problems({"page": _read("Europe/Berlin"), "worker": _read("Europe/Berlin")}, "Europe/Berlin") == []


@pytest.mark.parametrize("worker, fragment", [
    (_read("Europe/London"), "worker: Intl says"),
    (_read("Europe/Berlin", offsets_by_date=[0, 0]), "worker: offsets_by_date"),
    (_read("Europe/Berlin", offsets_by_intl=[120, 60]), "worker: offsets_by_intl"),
], ids=["worker_zone", "clock_offset", "intl_offset"])
def test_a_worker_that_keeps_another_zone_or_offset_is_named(worker, fragment):
    problems = rs.timezone_problems({"page": _read("Europe/Berlin"), "worker": worker}, "Europe/Berlin")
    assert any(fragment in p for p in problems), problems


def test_the_older_canonical_name_of_a_zone_is_the_zone():
    both = {"page": _read("Asia/Calcutta"), "worker": _read("Asia/Kolkata")}
    assert rs.timezone_problems(both, "Asia/Kolkata") == []


# ---------------------------------------------------------------------------------------------------------- language
HEADER = "de,en-US;q=0.7,en;q=0.3"
HEADERS = {"/": HEADER, "/fetch": HEADER, "/worker-fetch": HEADER}


def test_a_language_everything_agrees_on_and_the_country_speaks_is_clean():
    reads = {"page": _read("Europe/Berlin"), "worker": _read("Europe/Berlin")}
    assert rs.language_problems(reads, HEADERS, "DE") == []


def test_a_country_that_is_not_in_the_table_is_checked_for_agreement_only():
    reads = {"page": _read("Europe/Berlin"), "worker": _read("Europe/Berlin")}
    assert rs.language_problems(reads, HEADERS, "ZZ") == []
    assert rs.language_problems(reads, HEADERS, None) == []


@pytest.mark.parametrize("change, fragment", [
    (lambda reads, headers: reads["worker"].update(languages=["en-US", "en"]), "worker: languages"),
    (lambda reads, headers: reads["worker"].update(intl="en-US"), "worker: the Intl locale"),
    (lambda reads, headers: reads["page"].update(language="en-US"), "navigator.language"),
    (lambda reads, headers: headers.update({"/worker-fetch": "en-US,en;q=0.5"}), "/worker-fetch: Accept-Language"),
    (lambda reads, headers: headers.update({"/fetch": None}), "carried no Accept-Language"),
], ids=["worker_languages", "worker_intl", "navigator_language", "worker_header", "no_header"])
def test_a_read_that_disagrees_with_the_others_is_named(change, fragment):
    reads = {"page": _read("Europe/Berlin"), "worker": _read("Europe/Berlin")}
    headers = dict(HEADERS)
    change(reads, headers)
    problems = rs.language_problems(reads, headers, "DE")
    assert any(fragment in p for p in problems), problems


def test_a_language_the_country_does_not_speak_is_named():
    reads = {"page": _read("Asia/Tokyo", language="ru", languages=["ru", "en"], intl="ru"),
             "worker": _read("Asia/Tokyo", language="ru", languages=["ru", "en"], intl="ru")}
    headers = {path: "ru,en;q=0.5" for path in HEADERS}
    problems = rs.language_problems(reads, headers, "JP")
    assert any("not plausible for JP" in p for p in problems), problems


# ------------------------------------------------------------------------------------------ the gateway and the site
def _ask(s):
    s.sendall(b"GET /ip HTTP/1.0\r\nHost: x\r\nAccept-Language: de\r\n\r\n")
    data = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.split(b"\r\n\r\n", 1)[1].decode()


def _get_ip_through(gateway_port, site_port):
    s = socket.create_connection(("127.0.0.1", gateway_port), timeout=10)
    try:
        s.sendall(b"\x05\x01\x00")
        assert s.recv(2) == b"\x05\x00"
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", site_port))
        reply = b""
        while len(reply) < 10:
            reply += s.recv(10 - len(reply))
        assert reply[1] == 0, reply
        return _ask(s)
    finally:
        s.close()


def test_the_site_tells_a_connection_of_the_gateway_from_a_direct_one(doh):
    gateway = rs.ExitGateway("exit", doh, tcp_exit=EXIT, udp_exit=EXIT)
    site = rs.Site(gateway)
    try:
        assert _get_ip_through(gateway.port, site.port) == EXIT
        direct = socket.create_connection(("127.0.0.1", site.port), timeout=10)
        try:
            assert _ask(direct).startswith("DIRECT 127.0.0.1")
        finally:
            direct.close()
        verdicts = [a.via_gateway for a in site.arrivals]
        assert verdicts == [True, False], site.arrivals
        problems = rs.ip_problems(EXIT, site.arrivals, [], EXIT, gateway.connects, site.port)
        assert any("did not open" in p for p in problems), problems
        assert site.headers_of("/ip") == {"/ip": "de"}
    finally:
        site.close()
        gateway.close()


def test_a_run_through_the_gateway_only_is_clean_and_a_wrong_address_is_named(doh):
    gateway = rs.ExitGateway("exit", doh, tcp_exit=EXIT, udp_exit=EXIT)
    site = rs.Site(gateway)
    try:
        seen = _get_ip_through(gateway.port, site.port)
        assert rs.ip_problems(seen, site.arrivals, [], EXIT, gateway.connects, site.port) == []
        assert any("declared exit" in p for p in rs.ip_problems("192.0.2.1", site.arrivals, [], EXIT, gateway.connects, site.port))
        assert any("past the gateway" in p for p in rs.ip_problems(seen, site.arrivals, ["192.0.2.9:443"], EXIT,
                                                                   gateway.connects, site.port))
    finally:
        site.close()
        gateway.close()
