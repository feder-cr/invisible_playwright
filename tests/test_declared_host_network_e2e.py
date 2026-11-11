"""The host network a page sees is the one the session DECLARES, not the one of the machine (red until it is declared).

Design: ``invisible_WebRTC/notes/declared-interfaces-design.md`` and ``declared-interfaces-proofs.md`` (the private study
repository). Behind a
proxy the engine must not read the host's interfaces: the core declares one adapter in ``STEALTHFOX_WEBRTC_NETWORK``,
``v4=<private address>;v6=<0|1>;p6=<the exit's /64 prefix, empty without IPv6>`` (the IPv6 host is that prefix and a
temporary identifier; v6=1 without a prefix is no IPv6), and the engine answers the reads of the host (the interface list,
the default route, the address and port a bound socket reports) from it (nr_stealth_network.h). A page
then sees what retail Firefox on Windows shows on a machine with that adapter, whatever the machine under the browser has:

  * no IPv6: ONE host name (udp + tcp), ranked 127 (the only address);
  * IPv6: TWO host names, the IPv6 one ranked 127 above the IPv4 one at 126 (``nrinterfaceprioritizer.cpp:82-85``);
  * the host ports are those of a Windows machine, chosen by our code: 49152-65535, a consecutive pair per connection
    (IPv4 N, IPv6 N+1), drawn at random by our code for each connection (not by the kernel, not from the seed: two visits
    with the same seed must not share ports), the same distribution on every host;
  * two connections on one page connect to each other in the time retail takes.

Reference shape (retail 159, measured 2026-11-11, ``captures/retail/firefox-159.0-win/direct-dualstack-stun-candidates``):
host udp 2122252543 (IPv6) and 2122187007 (IPv4), host tcp 2105524479 and 2105458943, one name per address.

Red on an engine without the declared network (before webrtc/declared-interfaces b9bdb3c3ad + 6ca7a8c104): it gathered
every filtered address of the OS and let the kernel hand out the ports, so these failed on every machine with more than
one interface or a port range that is not Windows's, the IPv6 case everywhere, and the loopback test was slower than
retail and died with no usable interface. Hermetic: a local TCP-only SOCKS5 proxy and a local page, no external network.
"""
from __future__ import annotations

import statistics

import pytest

from test_webrtc_realness import (_FAKE_EGRESS, _e2e_binary, _launch, candidates, decode_priority, host_candidates,
                                  is_mdns, local_https_page, socks5_tcp_only)  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

NETWORK_ENV = "STEALTHFOX_WEBRTC_NETWORK"
# retail 159 on Windows. The interface rank is the position in the prioritizer's sort, starting at 127
# (nrinterfaceprioritizer.cpp:183-189), so an IPv4-only machine ranks its one host 127, and with IPv6 the IPv6 host
# takes 127 and the IPv4 host 126.
UDP_127, UDP_126, TCP_127, TCP_126 = 2122252543, 2122187007, 2105524479, 2105458943
# The two shapes of the declared adapter: without IPv6, and with the exit's /64 prefix.
V4_ONLY = "v4=192.168.31.7;v6=0;p6="
DUAL = "v4=192.168.31.7;v6=1;p6=2a01:e0a:1f2:3450::/64"

_GATHER_JS = r"""async () => {
  const pc = new RTCPeerConnection({iceServers: []});
  pc.createDataChannel('');
  const cands = [];
  pc.addEventListener('icecandidate', e => { if (e.candidate && e.candidate.candidate) cands.push(e.candidate.candidate); });
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => setTimeout(r, 2500));
  try { pc.close(); } catch (e) {}
  return cands;
}"""

# Two connections on one page, connected to each other in script (the loopback test many demos use).
_LOOPBACK_JS = r"""async () => {
  const t0 = performance.now();
  const pc1 = new RTCPeerConnection(), pc2 = new RTCPeerConnection();
  pc1.onicecandidate = e => { if (e.candidate) pc2.addIceCandidate(e.candidate).catch(() => {}); };
  pc2.onicecandidate = e => { if (e.candidate) pc1.addIceCandidate(e.candidate).catch(() => {}); };
  const dc = pc1.createDataChannel('x');
  let opened = null, echoed = false;
  dc.onopen = () => { opened = Math.round(performance.now() - t0); dc.send('ping'); };
  pc2.ondatachannel = ev => { ev.channel.onmessage = m => ev.channel.send('pong:' + m.data); };
  dc.onmessage = m => { echoed = (m.data === 'pong:ping'); };
  await pc1.setLocalDescription(await pc1.createOffer());
  await pc2.setRemoteDescription(pc1.localDescription);
  await pc2.setLocalDescription(await pc2.createAnswer());
  await pc1.setRemoteDescription(pc2.localDescription);
  const deadline = performance.now() + 8000;
  while (performance.now() < deadline && !(opened !== null && echoed)) await new Promise(r => setTimeout(r, 5));
  pc1.close(); pc2.close();
  return {opened, echoed};
}"""

# Retail 159 on Windows, 12 cold launches (``captures/mdns-loopback/retail-159-win-12``): 37-52 ms in ten of twelve, two
# slow ones at about 110, median 41.5. Without mDNS retail takes about 20 ms (``retail-159-win-nomdns``): the other 20 ms are
# the multicast round trip of the responder. The engine makes that round trip too, on the loopback (the responder joins and
# speaks on 127.0.0.1; a name of ours answers with the loopback of its family), so the window is the retail one: 30-55 ms on
# the MEDIAN of five cold launches. It keeps out "dead" (no usable interface), "slower than retail" and "faster than retail"
# (an answer that skips the round trip).
RETAIL_MEDIAN_WINDOW_MS = (30, 55)


def _session(declaration, socks, page_url, monkeypatch, seed, script):
    """One proxied launch with the declaration in the environment; runs ``script`` on the page and returns the result."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    monkeypatch.setenv("STEALTHFOX_WEBRTC_PUBLIC_IP", _FAKE_EGRESS)
    monkeypatch.delenv("STEALTHFOX_WEBRTC_DISABLE_IPV6", raising=False)  # the declaration replaces it
    monkeypatch.setenv(NETWORK_ENV, declaration)
    prefs = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": ""}
    proxy = {"server": "socks5://127.0.0.1:%d" % socks.port}
    with _launch(binary_path=binary, proxy=proxy, extra_prefs=prefs, seed=seed) as browser:
        page = browser.new_context().new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        return script(page)


def _host_shape(declaration, socks, page_url, monkeypatch, seed=42):
    return host_candidates(candidates(
        _session(declaration, socks, page_url, monkeypatch, seed, lambda page: page.evaluate(_GATHER_JS))))


def _by_protocol(hosts):
    return {proto: sorted((c["priority"] for c in hosts if c["proto"] == proto), reverse=True)
            for proto in ("UDP", "TCP")}


def _udp_ports_by_rank(hosts):
    return {decode_priority(c["priority"])["iface_pref"]: c["port"] for c in hosts if c["proto"] == "UDP"}


def test_without_a_declared_ipv6_the_page_sees_one_host_name(local_https_page, socks5_tcp_only, monkeypatch):
    """One adapter, no IPv6: a udp and a tcp host candidate on ONE mDNS name, ranked 127 as the only address.

    The shape is a function of the declaration alone, so this must hold on every machine: run it on a host with three
    IPv4 adapters (the engine gathers all three today), in a container with two interfaces (``docker network connect``
    before ``docker start``), and in a container with one."""
    hosts = _host_shape(V4_ONLY, socks5_tcp_only, local_https_page, monkeypatch)
    assert all(is_mdns(c["address"]) for c in hosts), [c["address"] for c in hosts]
    assert len({c["address"] for c in hosts}) == 1, "host names: %r" % sorted({c["address"] for c in hosts})
    assert _by_protocol(hosts) == {"UDP": [UDP_127], "TCP": [TCP_127]}, _by_protocol(hosts)


def test_a_declared_ipv6_gives_two_host_names_ranked_127_and_126(local_https_page, socks5_tcp_only, monkeypatch):
    """One adapter with IPv6: two names; the IPv6 host ranks one above the IPv4 one, as on the retail capture."""
    hosts = _host_shape(DUAL, socks5_tcp_only, local_https_page, monkeypatch)
    assert all(is_mdns(c["address"]) for c in hosts), [c["address"] for c in hosts]
    assert len({c["address"] for c in hosts}) == 2, "host names: %r" % sorted({c["address"] for c in hosts})
    assert _by_protocol(hosts) == {"UDP": [UDP_127, UDP_126], "TCP": [TCP_127, TCP_126]}, _by_protocol(hosts)
    assert [decode_priority(p)["iface_pref"] for p in _by_protocol(hosts)["UDP"]] == [127, 126]


def test_ipv6_without_the_exit_prefix_is_no_ipv6(local_https_page, socks5_tcp_only, monkeypatch):
    """v6=1 with an empty prefix: the exit gave the core no /64, so the machine has no IPv6 address, and no fallback
    invents one. One host name, as without IPv6."""
    hosts = _host_shape("v4=192.168.31.7;v6=1;p6=", socks5_tcp_only, local_https_page, monkeypatch)
    assert _by_protocol(hosts) == {"UDP": [UDP_127], "TCP": [TCP_127]}, hosts


def test_the_host_ports_are_those_of_a_windows_machine(local_https_page, socks5_tcp_only, monkeypatch):
    """Retail on Windows (6 of 6 runs, ``captures/retail/firefox-159.0-win/direct-dualstack-srflx-ports``): the UDP host
    ports are in the dynamic range 49152-65535 and, on one connection, the IPv4 port and the IPv6 port are consecutive
    (N, N+1). A page reads them. Linux hands out 32768-60999 at random, so on a Linux host this is red today."""
    hosts = _host_shape(DUAL, socks5_tcp_only, local_https_page, monkeypatch)
    by_rank = _udp_ports_by_rank(hosts)
    assert sorted(by_rank) == [126, 127], by_rank
    assert all(49152 <= port <= 65535 for port in by_rank.values()), by_rank
    assert by_rank[127] == by_rank[126] + 1, "IPv4 port %d, IPv6 port %d" % (by_rank[126], by_rank[127])


def test_the_host_port_is_in_the_windows_range_without_ipv6_too(local_https_page, socks5_tcp_only, monkeypatch):
    """The one-address case of the range check. On a Linux host it can pass by luck (a kernel port in 49152-60999 falls
    in the range); the same-seed test above is the one that cannot."""
    hosts = _host_shape(V4_ONLY, socks5_tcp_only, local_https_page, monkeypatch)
    by_rank = _udp_ports_by_rank(hosts)
    assert list(by_rank) == [127] and 49152 <= by_rank[127] <= 65535, by_rank


def _ports_of_two_connections(declaration, socks, page_url, monkeypatch, seed):
    def script(page):
        first = host_candidates(candidates(page.evaluate(_GATHER_JS)))
        second = host_candidates(candidates(page.evaluate(_GATHER_JS)))
        return first, second

    first, second = _session(declaration, socks, page_url, monkeypatch, seed, script)
    return (sorted(c["port"] for c in first if c["proto"] == "UDP"),
            sorted(c["port"] for c in second if c["proto"] == "UDP"))


def test_two_connections_on_one_page_get_their_own_port_pair(local_https_page, socks5_tcp_only, monkeypatch):
    """Retail (``captures/retail/firefox-159.0-win/two-pc-port-sequence``, 6 of 6): each connection gets its own pair,
    consecutive (N, N+1) in the range, and the second pair is unrelated to the first (not increasing)."""
    first, second = _ports_of_two_connections(DUAL, socks5_tcp_only, local_https_page, monkeypatch, 42)
    for pair in (first, second):
        assert len(pair) == 2 and pair[1] == pair[0] + 1 and 49152 <= pair[0] and pair[1] <= 65535, (first, second)
    assert first != second, (first, second)


def test_two_connections_on_one_page_connect_in_the_time_of_retail(local_https_page, socks5_tcp_only, monkeypatch):
    """The names the browser registered for itself must resolve although the declared address is not the host's, and in
    the time retail takes: not slower (a responder that cannot hear itself, or an OS list that is empty, kills the test),
    not faster (an answer that skips the round trip). Five cold launches, median in the retail window."""
    times = []
    for n in range(5):
        res = _session(V4_ONLY, socks5_tcp_only, local_https_page, monkeypatch, 100 + n,
                       lambda page: page.evaluate(_LOOPBACK_JS))
        assert res["echoed"] and res["opened"] is not None, "run %d: the data channel did not open: %r" % (n, res)
        times.append(res["opened"])
    low, high = RETAIL_MEDIAN_WINDOW_MS
    median = statistics.median(times)
    assert low <= median <= high, "median %s ms of %r, retail window %d-%d ms" % (median, sorted(times), low, high)
