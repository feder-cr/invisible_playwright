"""The UDP association of the proxy is ready BEFORE the PeerConnection is born: its first datagram does not wait for a handshake.

⛔ THE KNOWN-BAD INPUT. The sockets of a PeerConnection are made in the SOCKET process; the pool of UDP associations was warmed only
where Necko opens a SOCKS5 connection (the parent, where HTTP lives). So the first ICE socket found the pool empty and made its
association from scratch: TCP, greeting, auth, ASSOCIATE, four round trips before the first STUN datagram left (605 ms of 1083 on a
real proxy 150 ms away; measured in the logs of the giro 2 binary: "layer pushed" at +34 ms from the birth of the PeerConnection,
"ASSOCIATE OK" at +639 ms). A retail Firefox at home has no such wait.

What is asserted, with a gateway that answers every step of the handshake after a round trip (130 ms):
  1. an association the gateway granted was completed before the PeerConnection was created (the page loaded through the same
     proxy first, which is when Firefox learns the proxy); alone this does not tell the processes apart (the parent's pool is
     warmed by the page load), the second line does;
  2. the first STUN datagram of the PeerConnection reaches the relay within a fraction of a handshake of its birth (a cold
     association needs three answered steps, 3 x 130 ms, before it can send).
Hermetic: a local SOCKS5 gateway with a relay and a fake STUN server, a local page reached through the proxy, no external network.
Red on an engine that warms the pool only in the process of the HTTP connections: measured on the giro 2 binary, the first datagram left
396 ms after the birth of the PeerConnection (3 x 130 ms) instead of at once.
"""
from __future__ import annotations

import time

import pytest

from _socks_servers import STUN_NUMBER, STUN_PORT, coherent
from _udp_line import egress_by_proxy
from test_webrtc_realness import _e2e_binary, _launch  # noqa: F401

# Bound to the engine that carries the UDP line: red before the UDP line (firefox-63): skips on the published engine; runs on the candidate.
pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

RTT_MS = 130

_PC_JS = """async (stun) => {
  const t_pc = Date.now();
  const pc = new RTCPeerConnection({iceServers: [{urls: ['stun:' + stun]}]});
  pc.createDataChannel('x');
  let t_srflx = null;
  pc.onicecandidate = e => { if (e.candidate && e.candidate.type === 'srflx' && t_srflx === null) t_srflx = Date.now(); };
  await pc.setLocalDescription(await pc.createOffer());
  const deadline = Date.now() + 8000;
  while (t_srflx === null && Date.now() < deadline) await new Promise(r => setTimeout(r, 5));
  pc.close();
  return {t_pc, t_srflx};
}"""


@pytest.mark.wall_clock
def test_the_first_datagram_does_not_wait_for_a_handshake(local_https_page, monkeypatch):
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo
    with coherent(user="alice", password="s3cret", rtt_ms=RTT_MS) as gateway:
        # ⛔ THE SESSION'S UDP HAS TO BE DECLARED COHERENT, or the engine treats the proxy as a router that drops UDP (no association is
        # asked, no datagram leaves) - the red of the giro 3 gate. The core declares `coherent` when the UDP probe's mapped address IS
        # the TCP exit it discovered; a hermetic test gives it the gateway's TCP exit as every e2e of the line does (the probe that
        # follows still runs through the relay, for real). The hermetic world has no network but the gateway, so the core's own discovery of the
        # exit (third-party echo services) fails and the verdict would be `unmeasured`.
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(gateway))
        prefs = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": ""}
        with _launch(binary_path=binary, proxy=gateway.proxy, extra_prefs=prefs, seed=7) as browser:
            page = browser.new_context().new_page()
            page.goto(local_https_page, wait_until="load", timeout=60000)   # through the proxy: Firefox learns it here
            page.wait_for_timeout(2500)                                      # the pool has time to make its associations
            result = page.evaluate(_PC_JS, "%s:%d" % (STUN_NUMBER, STUN_PORT))
        t_pc = result["t_pc"] / 1000.0
        granted = [a for a in gateway.associations if a[1] is not None]
        first = next((t for host, port, _atyp, t in gateway.datagram_log if host == STUN_NUMBER), None)
    assert granted, "the proxy was never asked for an association"
    # the first association of the gateway is the core's own UDP probe, made before the browser exists: it is no pool
    # association of the engine, and counting it made this test pass with an engine that asked for none
    granted = granted[1:]
    assert granted, "the engine never asked the proxy for an association (only the core's probe did): the session's UDP was not declared coherent"
    ready_before = [a for a in granted if gateway.wall0 + a[1] < t_pc]
    assert ready_before, (
        "no association was completed before the PeerConnection was born (granted at %s s from the gateway's start, the "
        "PeerConnection at %.3f s): the first ICE socket made its own, from scratch"
        % ([round(a[1], 3) for a in granted], t_pc - gateway.wall0))
    assert first is not None, "no STUN datagram of the PeerConnection reached the relay"
    wait = gateway.wall0 + first - t_pc
    assert wait < 0.25, (
        "the first STUN datagram left %.0f ms after the PeerConnection was born; a ready association sends at once, a cold one "
        "needs three answered steps of %d ms" % (wait * 1000, RTT_MS))
    assert result["t_srflx"] is not None, "no srflx candidate came back through the relay"
