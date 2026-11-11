"""HTTP/3 behind a SOCKS5 proxy: the page reads `nextHopProtocol`, and a retail Firefox with no proxy uses h3.

⛔ THE KNOWN-BAD INPUT: the engine refuses HTTP/3 whenever a proxy is in use (AltSvcCache::UpdateAltServiceMapping, the
attempt of DnsAndConnectSocket, HttpBaseChannel), so a page behind a SOCKS5 gateway that relays UDP read `h2` where the
same Firefox with no proxy reads `h3`. With a gateway that relays UDP the QUIC connection goes through the ASSOCIATE of the
proxy (the server sees the relay, never this host); with one that does not, the QUIC handshake never completes and Firefox
falls back to h2 by itself, and nothing UDP leaves this host direct.

The server is local, reached by its address (an address of this host that is not loopback: a loopback destination goes
direct whatever the proxy), with a self-signed certificate (the context ignores certificate errors).
"""
from __future__ import annotations

import os
import socket

import pytest

pytestmark = pytest.mark.e2e


def _binary():
    cand = os.environ.get("STEALTHFOX_E2E_BINARY") or os.environ.get("INVPW_BINARY_PATH")
    if not cand or not os.path.exists(cand):
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    return cand


def _address_of_this_host() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


_FETCH = """async (url) => {
  const seen = [];
  for (let i = 0; i < 12; i++) {
    const r = await fetch(url + '?n=' + i, {cache: 'no-store', mode: 'no-cors'});
    await r.arrayBuffer();
    const entry = performance.getEntriesByType('resource').filter(e => e.name.includes('?n=' + i)).pop();
    seen.push(entry ? entry.nextHopProtocol : '?');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  return seen;
}"""


def _protocols_behind(gateway, server, monkeypatch):
    from invisible_core import _geo
    from invisible_playwright import InvisiblePlaywright

    monkeypatch.setattr(_geo, "discover_egress_ip", lambda *_a, **_k: gateway.tcp_exit)
    with InvisiblePlaywright(headless=True, binary_path=_binary(), timezone="America/New_York",
                             proxy=gateway.proxy) as browser:
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.goto(server.url, wait_until="load", timeout=60000)      # the answer that carries Alt-Svc: h3
        page.wait_for_timeout(1500)
        return page.evaluate(_FETCH, server.url)


def test_a_page_behind_a_gateway_that_relays_udp_reads_h3_and_the_quic_goes_by_the_relay(monkeypatch):
    pytest.importorskip("aioquic")
    from _h3_server import H3Server
    from _socks_servers import coherent

    host = _address_of_this_host()
    with H3Server(host) as server, coherent(user="alice", password="s3cret", relay_host=host) as gateway:
        protocols = _protocols_behind(gateway, server, monkeypatch)
        carried = [(h, p) for h, p, _data in gateway.datagrams if (h, p) == (host, server.port)]
    assert "h3" in protocols, f"nextHopProtocol of 12 fetches behind a gateway that relays UDP: {protocols}"
    assert carried, "the QUIC datagrams did not go by the relay of the gateway"
    assert server.quic_peers and set(server.quic_peers) == {"127.0.0.1"}, (
        f"the QUIC server saw {server.quic_peers}: only the relay of the gateway (127.0.0.1) may reach it")


def test_a_page_behind_a_gateway_without_udp_falls_back_to_h2_and_nothing_goes_direct(monkeypatch):
    pytest.importorskip("aioquic")
    from _h3_server import H3Server
    from _socks_servers import tcp_only

    host = _address_of_this_host()
    with H3Server(host) as server, tcp_only(user="alice", password="s3cret") as gateway:
        protocols = _protocols_behind(gateway, server, monkeypatch)
    assert "?" not in protocols and "h3" not in protocols, (
        f"behind a gateway that carries no UDP every fetch is h2 (or http/1.1), never h3: {protocols}")
    assert not server.quic_peers, f"a QUIC connection reached the server direct, around the proxy: {server.quic_peers}"
