"""WebRTC realness regression tests.

Two layers, both runnable on GitHub CI:

* **unit** (`@pytest.mark.unit`) - pure SDP/candidate assertions against golden
  samples. No browser, no proxy, no network. These lock in every rule we found
  on 2026-06-06: host must be mDNS ``.local``; an srflx must carry a
  GENUINE nICEr priority (never ``local_pref == 0xFFFF``) and a
  stable, distinct foundation; CreepJS's resolver must return the egress, and a
  host-only SDP must read as "blocked". They run in the standard ``tests.yml``.

* **e2e** (`@pytest.mark.e2e`) - launch the patched binary and verify the live
  ICE gather. "Being behind a proxy" is faked WITHOUT any external proxy provider:
    - the "behind a TCP-only SOCKS proxy" condition is reproduced by a tiny
      in-process SOCKS5 server that relays TCP CONNECT but refuses UDP ASSOCIATE
      (exactly a residential TCP-only proxy → WebRTC's default-route UDP probe
      fails → exercises the Fix C fallback). No credentials, no external proxy.
  Excluded from the default run; a binary is located via ``STEALTHFOX_E2E_BINARY``
  (or the locally-built tree), else the test skips.
"""
from __future__ import annotations

import os
import re
import select
import socket
import struct
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler, HTTPServer

import pytest

from _host_address import this_host_address
from _socks_servers import MEASURE_NUMBER, STUN_PORT, Socks5TcpOnly
from _udp_line import declared_udp, egress_by_proxy, measure_servers

# ──────────────────────────────────────────────────────────────────────────
#  Pure SDP / ICE-candidate helpers (no I/O) - the heart of the sentinels.
# ──────────────────────────────────────────────────────────────────────────
_CAND = re.compile(
    r"candidate:(?P<foundation>\S+)\s+(?P<component>\d+)\s+(?P<proto>UDP|TCP|udp|tcp)\s+"
    r"(?P<priority>\d+)\s+(?P<address>\S+)\s+(?P<port>\d+)\s+typ\s+(?P<typ>\w+)"
    r"(?:.*?raddr\s+(?P<raddr>\S+)\s+rport\s+(?P<rport>\d+))?"
)


def parse_candidate(line):
    """Parse one ``a=candidate:`` / ``candidate:`` line into a dict (or None)."""
    m = _CAND.search(line)
    if not m:
        return None
    d = m.groupdict()
    d["component"] = int(d["component"])
    d["priority"] = int(d["priority"])
    d["port"] = int(d["port"])
    d["proto"] = d["proto"].upper()
    if d["rport"] is not None:
        d["rport"] = int(d["rport"])
    return d


def decode_priority(prio):
    """Split a candidate priority into nICEr's fields (RFC 5245 layout that
    nICEr emits: type<<24 | iface<<16 | dir<<13 | stun<<8 | (256-component))."""
    return {
        "type_pref": (prio >> 24) & 0xFF,
        "iface_pref": (prio >> 16) & 0xFF,
        "local_pref": (prio >> 8) & 0xFFFF,
        "direction": (prio >> 13) & 0x7,
        "stun_priority": (prio >> 8) & 0x1F,
        "component": 256 - (prio & 0xFF),
    }


def is_mdns(addr):
    return bool(addr) and str(addr).endswith(".local")


def candidates(sdp_or_lines):
    if isinstance(sdp_or_lines, str):
        lines = re.findall(r"(?:a=)?candidate:[^\r\n]*", sdp_or_lines)
    else:
        lines = list(sdp_or_lines)
    return [c for c in (parse_candidate(l) for l in lines) if c]


def host_candidates(cands):
    return [c for c in cands if c["typ"] == "host"]


def srflx_candidates(cands):
    return [c for c in cands if c["typ"] == "srflx"]


def host_is_mdns(cands):
    """Every host candidate must be a ``<uuid>.local`` mDNS name, never a raw
    LAN IP (the §9.4 leak form that fails BrowserLeaks)."""
    hosts = host_candidates(cands)
    return bool(hosts) and all(is_mdns(c["address"]) for c in hosts)


def srflx_realness(cand, expected_ip=None):
    """Return (ok, reasons) for whether ``cand`` looks like a GENUINE nICEr UDP
    server-reflexive candidate. Encodes the 2026-06-06 findings."""
    reasons = []
    if cand["typ"] != "srflx":
        reasons.append("not a srflx candidate")
        return False, reasons
    if expected_ip is not None and cand["address"] != expected_ip:
        reasons.append(f"address {cand['address']} != expected {expected_ip}")
    p = decode_priority(cand["priority"])
    if p["type_pref"] != 100:
        reasons.append(f"type_pref {p['type_pref']} != 100 (SRV_RFLX)")
    if p["local_pref"] == 0xFFFF:
        reasons.append("local_pref == 0xFFFF - impossible nICEr value (the old hardcoded tell)")
    elif not (0x7000 <= p["local_pref"] < 0x8000):
        reasons.append(f"local_pref {p['local_pref']} outside the genuine ~0x7E00-0x7FFF band")
    if not (16 <= p["stun_priority"] <= 31):
        reasons.append(f"stun_priority {p['stun_priority']} implausible (expect 31-server_id)")
    if cand.get("raddr") not in (None, "0.0.0.0"):
        reasons.append(f"raddr {cand['raddr']} not redacted to 0.0.0.0")
    return (not reasons), reasons


def creep_get_ipaddress(sdp):
    """Faithful port of CreepJS's getIPAddress(sdp): connection line first, then
    the first candidate IP; '0.0.0.0' counts as blocked. Returns None if blocked
    - i.e. exactly what makes CreepJS render 'stun connection: blocked'."""
    blocked = "0.0.0.0"
    conn = (re.findall(r"c=IN\s.+\s", sdp) or [""])[0].strip().split(" ")
    conn_ip = conn[2] if len(conn) > 2 else ""
    if conn_ip and conn_ip != blocked:
        return conn_ip
    m = re.search(r"(udp|tcp)\s\w+\s([\w.:]+)(?=\s)", sdp, re.I)
    ip = m.group(2) if m else None
    return ip if (ip and ip != blocked) else None


# ──────────────────────────────────────────────────────────────────────────
#  Golden samples - real priority/foundation values, TEST-NET IPs (RFC 5737)
#  so no real address is ever committed (feedback_pre_push_privacy_check).
# ──────────────────────────────────────────────────────────────────────────
HOST_MDNS = "candidate:0 1 UDP 2122252543 1460e928-16b3-4c66-80ad-04abcdef0000.local 54551 typ host"
HOST_RAW_IP = "candidate:0 1 UDP 2122252543 192.168.1.20 54551 typ host"  # §9.4 leak form
VANILLA_SRFLX = "candidate:1 1 UDP 1685987327 203.0.113.50 3755 typ srflx raddr 0.0.0.0 rport 0"
OURS_SRFLX = "candidate:1 1 UDP 1686052863 203.0.113.7 58555 typ srflx raddr 0.0.0.0 rport 0"
# Pre-fix injection: local_pref hardcoded to 0xFFFF (priority 1694498815). The tell.
OLD_BAD_SRFLX = "candidate:2 1 UDP 1694498815 203.0.113.7 58555 typ srflx raddr 0.0.0.0 rport 0"

SDP_GOOD = (
    "v=0\r\nc=IN IP4 0.0.0.0\r\n"
    f"a={HOST_MDNS}\r\na={OURS_SRFLX}\r\n"
)
SDP_BLOCKED = "v=0\r\nc=IN IP4 0.0.0.0\r\n" f"a={HOST_MDNS}\r\n"  # host-only, no srflx


# ──────────────────────────────────────────────────────────────────────────
#  UNIT sentinels (run on GitHub CI)
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_parse_and_decode_basics():
    c = parse_candidate(OURS_SRFLX)
    assert c["typ"] == "srflx" and c["proto"] == "UDP"
    assert c["address"] == "203.0.113.7" and c["raddr"] == "0.0.0.0" and c["rport"] == 0
    p = decode_priority(c["priority"])
    assert p["type_pref"] == 100 and p["stun_priority"] == 31 and p["component"] == 1


@pytest.mark.unit
def test_genuine_srflx_passes():
    for line in (VANILLA_SRFLX, OURS_SRFLX):
        ok, reasons = srflx_realness(parse_candidate(line), expected_ip=parse_candidate(line)["address"])
        assert ok, reasons


@pytest.mark.unit
def test_old_0xffff_srflx_is_rejected():
    """Fix A sentinel: local_pref == 0xFFFF must be flagged as fake."""
    ok, reasons = srflx_realness(parse_candidate(OLD_BAD_SRFLX))
    assert not ok
    assert any("0xFFFF" in r for r in reasons), reasons


@pytest.mark.unit
def test_host_must_be_mdns_not_raw_ip():
    """§9.4 sentinel: raw-IP host candidate is a leak; .local is required."""
    assert host_is_mdns(candidates([HOST_MDNS])) is True
    assert host_is_mdns(candidates([HOST_RAW_IP])) is False


@pytest.mark.unit
def test_srflx_foundation_distinct_from_host():
    """Fix B sentinel: srflx foundation must differ from the host foundations."""
    cands = candidates([HOST_MDNS, OURS_SRFLX])
    host_fnds = {c["foundation"] for c in host_candidates(cands)}
    srflx_fnds = {c["foundation"] for c in srflx_candidates(cands)}
    assert srflx_fnds and srflx_fnds.isdisjoint(host_fnds)


@pytest.mark.unit
def test_creep_resolver_returns_egress_when_srflx_present():
    assert creep_get_ipaddress(SDP_GOOD) == "203.0.113.7"


@pytest.mark.unit
def test_creep_resolver_reports_blocked_for_host_only():
    """The exact false-green we shipped: host-only (.local) SDP → no public IP
    → CreepJS shows 'blocked'. The resolver must return None here."""
    assert creep_get_ipaddress(SDP_BLOCKED) is None


@pytest.mark.unit
def test_mdns_host_is_invisible_to_creep_resolver():
    """A .local host must NOT be mis-read as an IP (the hyphen in the UUID is
    what makes CreepJS skip it and fall through to the srflx)."""
    assert creep_get_ipaddress("v=0\r\nc=IN IP4 0.0.0.0\r\n" f"a={HOST_MDNS}\r\n") is None


# ──────────────────────────────────────────────────────────────────────────
#  SHIPPED-BASELINE guard - the cheap unit test that would have caught the
#  2026-06-10 gap (baseline obfuscate=False, dead disableIPv6, orphan prefs).
#  These lock the shipped wrapper config to the manually-validated one so a
#  future edit / merge can't silently un-ship it. Run in tests.yml.
# ──────────────────────────────────────────────────────────────────────────
from invisible_core._fpforge import generate_profile  # noqa: E402
from invisible_core.prefs import translate_profile_to_prefs  # noqa: E402


@pytest.mark.unit
def test_shipped_webrtc_baseline_is_the_validated_config():
    prefs = translate_profile_to_prefs(generate_profile(seed=42), timezone="America/New_York")
    # host candidate must be mDNS .local like vanilla Firefox (manually
    # validated on BrowserLeaks/CreepJS through a residential proxy) - not a
    # raw LAN IP.
    assert prefs["media.peerconnection.ice.obfuscate_host_addresses"] is True
    # IPv6 is removed from ICE gathering, but NOT from a pref: since 2026-08-25
    # the native bridge reads a single source, the environment. The same-named
    # pref is no longer emitted - the bridge no longer read it and it would
    # have become an orphan, which is exactly what test_no_orphan_prefs_in_baseline
    # below forbids. Before there were two sources, and which one decided
    # depended on the presence of the proxy: with a proxy the environment won,
    # without a proxy the pref did.
    assert "zoom.stealth.webrtc.disable_ipv6" not in prefs
    assert "media.peerconnection.ice.disableIPv6" not in prefs
    # The single source is the environment (STEALTHFOX_WEBRTC_NETWORK, drawn from
    # the seed), declared with or without a proxy.
    from invisible_core import network_to_declare
    from invisible_core.launch import build_launch_env
    from invisible_playwright._session import build_env
    for build_fn in (lambda **k: build_launch_env({}, **k), build_env):
        env = build_fn(network_declared=network_to_declare(42), base_env={})
        assert env["STEALTHFOX_WEBRTC_NETWORK"] == network_to_declare(42)
    # peerconnection stays ON (a disabled WebRTC is itself a tell).
    assert prefs["media.peerconnection.enabled"] is True


@pytest.mark.unit
def test_no_orphan_prefs_in_baseline():
    """zoom.stealth.timezone / zoom.stealth.seed are read by NO C++ - they must
    not be written (juggler.timezone.override + zoom.stealth.fpp.hw_seed are the
    real ones). Guards against re-introducing a pref the binary ignores."""
    prefs = translate_profile_to_prefs(generate_profile(seed=42), timezone="America/Chicago")
    assert "zoom.stealth.timezone" not in prefs
    assert "zoom.stealth.seed" not in prefs
    assert prefs["juggler.timezone.override"] == "America/Chicago"
    assert "zoom.stealth.fpp.hw_seed" in prefs


# ──────────────────────────────────────────────────────────────────────────
#  Fake-proxy infrastructure for e2e: a tiny TCP-only SOCKS5 server (Socks5TcpOnly, in _socks_servers.py).
# ──────────────────────────────────────────────────────────────────────────
# Same per-event probe CreepJS runs (kept tiny; raw string = one escape level).
_PROBE_JS = r"""async () => {
  const pc = new RTCPeerConnection({iceCandidatePoolSize:1, iceServers:[{urls:[
    'stun:stun4.l.google.com:19302','stun:stun3.l.google.com:19302']}]});
  pc.createDataChannel('');
  const cands = [];
  pc.addEventListener('icecandidate', e => { if (e.candidate && e.candidate.candidate) cands.push(e.candidate.candidate); });
  await pc.setLocalDescription(await pc.createOffer({offerToReceiveAudio:1, offerToReceiveVideo:1}));
  await new Promise(r => setTimeout(r, 3500));
  const sdp = (pc.localDescription && pc.localDescription.sdp) || '';
  try { pc.close(); } catch(e) {}
  return { candidates: cands, sdp };
}"""

_FAKE_EGRESS = "203.0.113.7"  # RFC 5737 TEST-NET-3


def _e2e_binary():
    # Honor both env vars so the whole e2e suite targets one binary from a single
    # setting (INVPW_BINARY_PATH is what conftest's firefox_binary uses).
    cand = os.environ.get("STEALTHFOX_E2E_BINARY") or os.environ.get("INVPW_BINARY_PATH")
    if cand and os.path.exists(cand):
        return cand
    built = r"C:\ff\source\obj-x86_64-pc-windows-msvc\dist\bin\firefox.exe"
    if os.path.exists(built):
        return built
    return None


def _launch(**extra):
    from invisible_playwright import InvisiblePlaywright

    kw = {"headless": True,
          # Fixed zone so the wrapper does NOT run timezone="auto" egress
          # discovery through the (fake) proxy - irrelevant here, we inject the
          # egress IP directly and want the launch deterministic/offline.
          "timezone": "America/New_York",
          # Where the core measures the UDP exit of a SOCKS5 proxy from (an explicit launch parameter, never the public
          # defaults in a hermetic test): a documentation address only the relay of a test gateway answers.
          "udp_stun_servers": ["%s:%d" % (MEASURE_NUMBER, STUN_PORT)],
          "extra_prefs": {"media.peerconnection.ice.obfuscate_host_addresses": True}}
    kw.update(extra)
    return InvisiblePlaywright(**kw)


def _launch_behind(gateway, **extra):
    """The launch behind a test gateway: its proxy, and the STUN server its relay answers for the measurement of its UDP
    exit (``udp_stun_servers``). ONE place for every e2e that needs the relay."""
    return _launch(proxy=gateway.proxy, udp_stun_servers=measure_servers(gateway), **extra)


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_the_gather_is_real_without_a_proxy(local_https_page):
    """No proxy: the engine keeps the real network. The gather completes, the
    host is mDNS, and an srflx, when one is born, is genuine (Fix A/B) and
    resolvable by the resolver of a studied system (not blocked)."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    with _launch(binary_path=binary) as browser:
        page = browser.new_context().new_page()
        page.goto(local_https_page, wait_until="domcontentloaded", timeout=60000)
        res = page.evaluate(_PROBE_JS)
    cands = candidates(res["candidates"])
    assert cands, "ICE produced ZERO candidates (blocked)"
    assert host_is_mdns(cands), [c["address"] for c in host_candidates(cands)]
    srflx = srflx_candidates(cands)
    for c in srflx:
        ok, reasons = srflx_realness(c, expected_ip=c["address"])
        assert ok, reasons
    # Two srflx for the same base must share ONE stable foundation (Fix B).
    assert len({c["foundation"] for c in srflx}) <= 1


@pytest.mark.e2e
def test_not_blocked_behind_tcp_only_socks(socks5_tcp_only):
    """Behind a TCP-only SOCKS proxy on a remote origin, ICE must still complete
    (host .local candidates), not return zero candidates: a fully blocked
    WebRTC is itself a tell. The session declares its network from the seed
    (STEALTHFOX_WEBRTC_NETWORK); that variable is read by the new engine only."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    proxy = {"server": f"socks5://127.0.0.1:{socks5_tcp_only.port}"}
    try:
        with _launch(binary_path=binary, proxy=proxy) as browser:
            page = browser.new_context().new_page()
            # remote origin loaded THROUGH the local SOCKS proxy (not localhost,
            # so no proxy-bypass) -> WebRTC proxy config active.
            page.goto("https://example.com/", wait_until="domcontentloaded", timeout=70000)
            res = page.evaluate(_PROBE_JS)
    except Exception as exc:  # network/proxy unavailable in this environment
        pytest.skip(f"proxy/network path unavailable: {exc!r}")
    cands = candidates(res["candidates"])
    assert cands, "behind SOCKS the gather returned ZERO candidates (blocked)"
    assert host_is_mdns(cands)


# The gather the next tests read: one STUN server, by number, and everything the engine says about it.
_RELAY_PROBE_JS = r"""async (stun) => {
  const t0 = performance.now();
  const pc = new RTCPeerConnection({iceServers: [{urls: [stun]}]});
  pc.createDataChannel('');
  const cands = [], errors = [], at = {};
  pc.addEventListener('icecandidate', e => {
    if (e.candidate && e.candidate.candidate) {
      cands.push(e.candidate.candidate);
      const typ = (e.candidate.candidate.match(/ typ (\w+)/) || [])[1];
      if (typ && !(typ in at)) at[typ] = Math.round(performance.now() - t0);
    }
  });
  pc.addEventListener('icecandidateerror', e => { errors.push({code: e.errorCode, text: e.errorText, url: e.url}); at.error = Math.round(performance.now() - t0); });
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(resolve => {
    const timer = setTimeout(resolve, 25000);
    pc.addEventListener('icegatheringstatechange', () => {
      if (pc.iceGatheringState === 'complete') { at.complete = Math.round(performance.now() - t0); clearTimeout(timer); resolve(); }
    });
  });
  const sdp = (pc.localDescription && pc.localDescription.sdp) || '';
  try { pc.close(); } catch (e) {}
  return {candidates: cands, errors, sdp, at_ms: at};
}"""


# The two prefs that decide where ICE runs are LOCKED to the retail value by the engine (toolkit/xre/nsAppRunner.cpp, after the user
# prefs are loaded): a caller that asks for the ICE in the process of the page, or for no socket process, gets the socket process all the
# same, and the srflx is born through the relay. Both rows below are such callers; the first is the default.
_ICE_PREFS = [
    {},                                                          # the engine's default: ICE in the socket process
    {"media.peerconnection.mtransport_process": False},          # a caller asks for the ICE in the process of the page: locked
    {"network.process.enabled": False},                          # a caller asks for no socket process: locked
]
_ICE_IDS = ["ice_in_the_socket_process", "the_caller_asks_for_ice_in_the_content_process_and_the_lock_wins",
            "the_caller_asks_for_no_socket_process_and_the_lock_wins"]


def _gather_behind_a_coherent_gateway(monkeypatch, credentials, ice_prefs, rtt_ms, page_first_ms=None):
    """Launch the default engine behind a local gateway with coherent UDP, `rtt_ms` away, gather with a STUN server only
    its relay reaches, and say whether the page's request took the relay and what the gather looked like."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo

    from _socks_servers import STUN_NUMBER, STUN_PORT, coherent
    stun = f"stun:{STUN_NUMBER}:{STUN_PORT}"
    user, password = credentials or (None, None)
    # The relay sits on the address of this host, as a real gateway's does: the engine's ICE sockets are bound to an
    # interface address, and on Windows such a socket cannot send to a relay on loopback.
    with coherent(user=user, password=password, relay_host=this_host_address(), rtt_ms=rtt_ms) as gateway:
        exit_ip = gateway.tcp_exit
        # The session asks the gateway for its TCP exit over HTTPS, which no local gateway can answer as itself: the
        # answer is the gateway's. The UDP probe that follows is NOT replaced: it runs through the relay, for real.
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(gateway))
        extra_prefs = {"media.peerconnection.ice.obfuscate_host_addresses": True, **ice_prefs}
        with _launch_behind(gateway, binary_path=binary, extra_prefs=extra_prefs) as browser:
            page = browser.new_context().new_page()
            if page_first_ms is None:
                page.goto("about:blank")
            else:
                # A page that is loaded through the gateway, and a while later asks for WebRTC.
                page.goto("http://page.test/")
                page.wait_for_timeout(page_first_ms)
            res = page.evaluate(_RELAY_PROBE_JS, stun)
        carried = [(host, port) for host, port, _payload in gateway.datagrams]
        stun_requests = gateway.stun.requests
        associates = gateway.associates
    srflx = [c for c in srflx_candidates(candidates(res["candidates"])) if c["address"] == exit_ip]
    verdict = (f"srflx with the gateway's exit {exit_ip}: {srflx}; candidates: {res['candidates']}; "
               f"ICE errors: {res['errors']}; gather timings (ms from the offer): {res['at_ms']}; "
               f"datagrams the relay carried: {carried}; STUN requests the server answered: {stun_requests}; "
               f"UDP ASSOCIATEs the gateway saw: {associates}; round trip to the gateway: {rtt_ms} ms")
    return bool(srflx) and (STUN_NUMBER, STUN_PORT) in carried, verdict, res["at_ms"]


@pytest.mark.e2e
@pytest.mark.parametrize("credentials", [None, ("alice", "s3cret"), ("user~tcp-windows-65535", "not-a-real-password")],
                         ids=["no_auth", "user_and_password", "user_with_a_tilde"])
@pytest.mark.parametrize("ice_prefs", _ICE_PREFS, ids=_ICE_IDS)
@pytest.mark.usefixtures("the_udp_line")
def test_a_gateway_with_coherent_udp_gives_the_page_a_srflx_born_through_its_relay(monkeypatch, credentials, ice_prefs):
    """The default engine, a SOCKS5 gateway that relays UDP and whose UDP exit is its TCP exit: the page's STUN request
    goes THROUGH the relay and the srflx it gets is the gateway's exit.

    ICE runs in the socket process (the sockets are NrSocket's, where the layer is attached) whatever the caller asks:
    `media.peerconnection.mtransport_process` and `network.process.enabled` are locked to their retail value by the engine,
    so a caller who sets either to false (rows 2 and 3) gets the same srflx through the relay as the default. Each with a
    gateway that wants no credentials and with one that does. A request that does not take the relay ends in an ICE
    error 701 and no srflx; the failure message says what the gateway saw.

    The gateway is local (tests/_socks_servers.py): the TCP exit and the UDP exit it reports are documentation addresses,
    and the STUN server the page is given is a documentation address too, so the only road to it is the relay. The page
    reaches the proxy by the session the Juggler holds, as every page does; nothing here sets a pref of the engine."""
    took_the_relay, verdict, _timings = _gather_behind_a_coherent_gateway(monkeypatch, credentials, ice_prefs, rtt_ms=0)
    assert took_the_relay, "the page's STUN request did not take the relay. " + verdict


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_a_host_with_ipv6_addresses_gathers_through_an_ipv4_relay(monkeypatch):
    """The ICE sockets are opened with the family of the host candidate they carry (NrSocket::create, PR_OpenUDPSocket
    with the address's family): an IPv6 host gets AF_INET6 sockets next to its AF_INET ones, while the relay of the
    gateway is IPv4. With the host addresses in the clear, the IPv4 STUN server must still be answered through the relay."""
    took_the_relay, verdict, _timings = _gather_behind_a_coherent_gateway(
        monkeypatch, ("alice", "s3cret"), {"media.peerconnection.ice.obfuscate_host_addresses": False}, 0)
    print("[families] " + verdict)
    assert took_the_relay, "the page's STUN request did not take the relay. " + verdict


@pytest.mark.e2e
@pytest.mark.parametrize("rtt_ms", [80, 150, 250])
@pytest.mark.parametrize("ice_prefs", _ICE_PREFS, ids=_ICE_IDS)
@pytest.mark.usefixtures("the_udp_line")
def test_a_gateway_that_is_far_away_still_gives_the_page_a_srflx_born_through_its_relay(monkeypatch, ice_prefs, rtt_ms):
    """The same gather with a gateway that is not on loopback: every answer on the control connection waits `rtt_ms`
    and the relay adds half of it each way. The layer opens one association per ICE socket, one after the other, each
    with a connect, a greeting, an authentication and the ASSOCIATE; a gather must not be over before they are done."""
    took_the_relay, verdict, _timings = _gather_behind_a_coherent_gateway(
        monkeypatch, ("alice", "s3cret"), ice_prefs, rtt_ms)
    print(f"[gather] rtt {rtt_ms} ms, {_ICE_IDS[_ICE_PREFS.index(ice_prefs)]}: {_timings}")
    assert took_the_relay, "the page's STUN request did not take the relay. " + verdict


@pytest.mark.e2e
@pytest.mark.parametrize("ice_prefs", _ICE_PREFS, ids=_ICE_IDS)
@pytest.mark.usefixtures("the_udp_line")
def test_the_first_candidate_of_a_gather_does_not_wait_for_a_gateway_that_is_far_away(monkeypatch, ice_prefs):
    """⛔ THE KNOWN-BAD INPUT: a gateway 250 ms away. The layer made its associations one after the other on the thread of
    the ICE sockets, a connect, a greeting, an authentication and the ASSOCIATE each, so the first candidate of the page
    came out after about twelve round trips (host candidate at 2.3 s on Linux and 3.0 s on Windows, against well under a
    second next to the gateway), and a page that times `icecandidate` reads it. A candidate does not wait for the proxy:
    the host candidate of the far gather comes within a round trip and a quarter of a second of the near one, and the
    srflx within the handshake (four round trips) and a STUN request (one) of its host candidate."""
    rtt_ms = 250
    near_ok, near_verdict, near = _gather_behind_a_coherent_gateway(monkeypatch, ("alice", "s3cret"), ice_prefs, 0)
    far_ok, far_verdict, far = _gather_behind_a_coherent_gateway(monkeypatch, ("alice", "s3cret"), ice_prefs, rtt_ms)
    print(f"[first candidate] near {near}, {rtt_ms} ms away {far}")
    assert near_ok, near_verdict
    assert far_ok, far_verdict
    assert far["host"] - near["host"] <= rtt_ms + 250, (
        f"the first candidate waited for the gateway: host candidate at {far['host']} ms behind a gateway {rtt_ms} ms away, "
        f"{near['host']} ms next to it. {far_verdict}")
    assert far["srflx"] - far["host"] <= 5 * rtt_ms + 250, (
        f"the srflx came {far['srflx'] - far['host']} ms after the host candidate. {far_verdict}")


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_a_gather_after_the_page_loaded_through_the_gateway_costs_only_the_road_to_the_server(monkeypatch):
    """⛔ THE KNOWN-BAD INPUT: a gateway 250 ms away, a page loaded through it, and two seconds later the page asks for
    WebRTC. Next to a gateway the srflx comes at once; behind this one it may come later by the road itself, the round
    trip of the relay to the STUN server and back, and by nothing else: the connect, the greeting, the authentication and
    the ASSOCIATE of each socket (about 0.56 s against the real driver) are made before the page asks, when the proxy
    of the context is first used, not after. A target without the proxy has no such cost to show."""
    rtt_ms = 250
    near_ok, near_verdict, near = _gather_behind_a_coherent_gateway(
        monkeypatch, ("alice", "s3cret"), {}, 0, page_first_ms=2000)
    far_ok, far_verdict, far = _gather_behind_a_coherent_gateway(
        monkeypatch, ("alice", "s3cret"), {}, rtt_ms, page_first_ms=2500)
    print(f"[page first] near {near}, {rtt_ms} ms away {far}")
    assert near_ok, near_verdict
    assert far_ok, far_verdict
    assert far["host"] - near["host"] <= 100, f"the host candidate waited: {far_verdict}"
    assert far["srflx"] - near["srflx"] <= rtt_ms + 250, (
        f"the srflx came {far['srflx']} ms after the offer behind a gateway {rtt_ms} ms away, {near['srflx']} ms next to "
        f"it: more than the road to the server ({rtt_ms} ms). {far_verdict}")


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_two_contexts_with_two_proxies_each_gets_the_srflx_of_its_own(monkeypatch):
    """⛔ THE KNOWN-BAD INPUT: a session whose proxy is one gateway and a context whose proxy is another. The proxy a
    page's traffic takes is the one of its context, UDP included: the srflx of each context is the exit of its own
    gateway. (The engine took one proxy for the whole session and handed over nothing when two differed.)"""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo

    from _socks_servers import STUN_NUMBER, STUN_PORT, coherent
    stun = f"stun:{STUN_NUMBER}:{STUN_PORT}"
    host = this_host_address()
    with coherent(exit_ip="203.0.113.7", user="alice", password="s3cret", relay_host=host) as first,             coherent(exit_ip="198.51.100.77", user="bob", password="hunter2", relay_host=host) as second:
        # Each gateway reports the TCP exit of its own: the context proxy is measured against ITS exit, not the session's.
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(first, second))
        extra_prefs = {"media.peerconnection.ice.obfuscate_host_addresses": True}
        with _launch_behind(first, binary_path=binary, extra_prefs=extra_prefs) as browser:
            of_the_session = browser.new_context().new_page()
            of_its_own = browser.new_context(proxy=second.proxy).new_page()
            res_session = of_the_session.evaluate(_RELAY_PROBE_JS, stun)
            res_own = of_its_own.evaluate(_RELAY_PROBE_JS, stun)

    def exits(res):
        return sorted(c["address"] for c in srflx_candidates(candidates(res["candidates"])))

    assert exits(res_session) == [first.tcp_exit], f"the context of the session: {res_session}"
    assert exits(res_own) == [second.tcp_exit], f"the context with a proxy of its own: {res_own}"


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_the_turn_over_tcp_of_a_context_goes_through_the_proxy_of_that_context(monkeypatch):
    """⛔ THE KNOWN-BAD INPUT: the proxy lookup of the TCP socket of a TURN server was made with a channel of the system
    principal, so the filter of the session answered for context 0 and a SOCKS5 proxy of the context was not the one the
    TURN connection took. The page below sits in a context with a proxy of its own and asks for a TURN server over TCP:
    the CONNECT must reach the gateway of that context and not the one of the session."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo

    from _socks_servers import coherent
    host = this_host_address()
    turn_host, turn_port = "198.51.100.9", 3478
    with coherent(exit_ip="203.0.113.7", user="alice", password="s3cret", relay_host=host) as session,             coherent(exit_ip="198.51.100.77", user="bob", password="hunter2", relay_host=host) as context:
        monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(session, context))
        extra_prefs = {"media.peerconnection.ice.obfuscate_host_addresses": True}
        with _launch_behind(session, binary_path=binary, extra_prefs=extra_prefs) as browser:
            page = browser.new_context(proxy=context.proxy).new_page()
            page.goto("about:blank")
            page.evaluate("""async (turn) => {
              const pc = new RTCPeerConnection({iceServers: [{urls: [turn], username: 'u', credential: 'p'}]});
              pc.createDataChannel('');
              await pc.setLocalDescription(await pc.createOffer());
              await new Promise(r => setTimeout(r, 6000));
              pc.close();
            }""", f"turn:{turn_host}:{turn_port}?transport=tcp")
        in_context = [c for c in context.connects if c == (turn_host, turn_port)]
        in_session = [c for c in session.connects if c == (turn_host, turn_port)]
    assert in_context and not in_session, (
        f"the TURN connection of the context: by the gateway of the context {context.connects}, "
        f"by the gateway of the session {session.connects}")


def _gather_where_the_udp_is_dropped(gateway, monkeypatch, measured):
    """Launch behind ``gateway`` (a SOCKS5 proxy whose UDP is not coherent), gather with a STUN server only its relay could
    reach, and say what the target must look like: a retail Windows at home on a network that blocks UDP.

    The core measured the proxy before the launch declared it, and it is ``measured`` (``no`` or ``unmeasured``): the engine
    sends the page's UDP nowhere, not to the relay and not direct, so the STUN request retransmits and ends in 701 in nICEr's
    own time. The page gets host candidates and no srflx: the session does not invent one (a candidate with the exit of TCP and
    no allocation behind it, raddr 0.0.0.0, is a value no browser makes)."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    from invisible_core import _geo

    from _socks_servers import STUN_NUMBER
    stun = f"stun:{STUN_NUMBER}:{STUN_PORT}"
    monkeypatch.setattr(_geo, "discover_egress_ip", egress_by_proxy(gateway))
    extra_prefs = {"media.peerconnection.ice.obfuscate_host_addresses": True}
    with _launch_behind(gateway, binary_path=binary, extra_prefs=extra_prefs) as browser:
        page = browser.new_context().new_page()
        page.goto("about:blank")
        res = page.evaluate(_RELAY_PROBE_JS, stun)
    carried = [(host, port) for host, port, _payload in gateway.datagrams]
    declared = declared_udp(gateway.proxy, measure_servers(gateway))
    assert declared == measured, f"the core declared udp={declared} for this proxy, the measurement is {measured}"
    assert (STUN_NUMBER, STUN_PORT) not in carried, (
        f"the engine sent the page's UDP to a relay that was not measured coherent: {carried}")
    typ = [c["typ"] for c in candidates(res["candidates"])]
    assert "srflx" not in typ and "relay" not in typ, f"a candidate that nothing allocated: {res['candidates']}"
    assert "host" in typ, f"no host candidate at all: {res['candidates']}"
    assert [e["code"] for e in res["errors"]] == [701], f"a network that blocks UDP ends the STUN request in 701: {res['errors']}"
    # The shape of a retail Firefox on a network that drops UDP (notes/retail-udp-blocked-reference.md): udp and tcp host
    # candidates, and the 701 of the UDP request at nICEr's own time, 11.1-11.3 s, together with the end of the gather: never
    # an immediate error, never a candidate that nothing allocated.
    protocols = sorted({c["proto"].upper() for c in candidates(res["candidates"]) if c["typ"] == "host"})
    assert protocols == ["TCP", "UDP"], f"host candidates of a retail on a network that drops UDP: {res['candidates']}"
    assert 10_000 <= res["at_ms"]["error"] <= 13_000, (
        f"the 701 of the UDP request came at {res['at_ms']['error']} ms; nICEr's time on a network that drops UDP is 11.1-11.3 s")


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_a_proxy_that_does_not_relay_udp_gives_no_srflx_at_all(monkeypatch):
    """⛔ THE KNOWN-BAD INPUT: a gateway that refuses UDP ASSOCIATE (reply 7). The core measures it ``no``: the target is a
    retail Windows at home on a network that blocks UDP (see :func:`_gather_where_the_udp_is_dropped`)."""
    from _socks_servers import tcp_only
    with tcp_only(user="alice", password="s3cret") as gateway:
        _gather_where_the_udp_is_dropped(gateway, monkeypatch, "no")


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_a_proxy_whose_udp_leaves_from_another_address_gives_no_srflx_at_all(monkeypatch):
    """⛔ THE KNOWN-BAD INPUT: a gateway that grants UDP ASSOCIATE and answers STUN, but with an address that is not its TCP
    exit: the classic leak (a srflx that is not the address of the page's HTTP). The core measures it ``no`` and the engine
    treats it as a router that drops UDP: the relay is granted but the page's datagrams never go to it."""
    from _socks_servers import mismatched
    with mismatched(user="alice", password="s3cret", relay_host=this_host_address()) as gateway:
        _gather_where_the_udp_is_dropped(gateway, monkeypatch, "no")


@pytest.mark.e2e
@pytest.mark.usefixtures("the_udp_line")
def test_a_proxy_whose_relay_never_answers_gives_no_srflx_at_all(monkeypatch):
    """⛔ THE KNOWN-BAD INPUT: a gateway that grants UDP ASSOCIATE and carries datagrams, but no STUN server ever answers (a
    router that drops UDP behind a working handshake). The measurement ends within its budget in ``unmeasured``: not a
    refusal, but the engine acts on ``coherent`` only, so the page is where it is on a network that blocks UDP."""
    from _socks_servers import silent_udp
    with silent_udp(user="alice", password="s3cret", relay_host=this_host_address()) as gateway:
        _gather_where_the_udp_is_dropped(gateway, monkeypatch, "unmeasured")




# ──────────────────────────────────────────────────────────────────────────
#  The egress IP must not change MID-SESSION.
#
#  Measured on 2026-08-25 on browserleaks, behind a residential proxy: the
#  page exited from 203.0.113.30 while a second reading of the exit said
#  216.131.76.64, and the site says so plainly ("WebRTC IP doesn't match your
#  Remote IP"). It was not a regression in the binary - interleaved A/B between
#  the release binary and the new one: 4 runs out of 4 consistent on both.
#
#  The cause is that the egress behind the timezone and the locale is photographed
#  ONCE, at launch, and residential proxies do not promise to keep it: the docs of the
#  two providers tried declare TIME-based sticky sessions (60 minutes max for
#  one; a sliding timeout for the other, which also expires early if the peer
#  disconnects). On a long session the drift is a certainty, not a risk - which
#  is why short probes never saw it.
#
#  Owner's decision, 2026-08-25: it is not updated on the fly. A timezone that
#  changes in front of the site's own eyes is as unnatural as the mismatch
#  itself. A proxy that does not hold the session is not fit for purpose, and
#  the product must SAY SO instead of carrying on.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_an_unchanged_egress_triggers_nothing(monkeypatch):
    """The check must STAY SILENT when the proxy behaves well."""
    from invisible_playwright import _session
    monkeypatch.setattr("invisible_core._geo.discover_egress_ip",
                        lambda *a, **k: "203.0.113.5")
    outcome, current = _session.egress_ancora_valido(
        {"server": "http://p:1"}, "203.0.113.5")
    assert outcome == _session.USCITA_REGGE
    assert current == "203.0.113.5"


@pytest.mark.unit
def test_a_changed_egress_is_detected(monkeypatch):
    """The KNOWN-BAD INPUT. Without this, the check above proves nothing."""
    from invisible_playwright import _session
    monkeypatch.setattr("invisible_core._geo.discover_egress_ip",
                        lambda *a, **k: "198.51.100.9")
    outcome, current = _session.egress_ancora_valido(
        {"server": "http://p:1"}, "203.0.113.5")
    assert outcome == _session.USCITA_DERIVATA
    assert current == "198.51.100.9"


@pytest.mark.unit
def test_a_failed_discovery_is_not_a_drift(monkeypatch):
    """A network problem does not turn into an accusation against the proxy.

    Raising `ProxyEgressDrifted` here would mean killing a healthy session
    every time an echo endpoint is unreachable for an instant.

    ⛔ BUT THIS TEST'S NAME WAS RIGHT AND ITS ASSERTION WAS NOT. Until
    2026-08-25 it expected `(True, None)`, i.e. it asked the function to
    answer **holds** after a measurement that had never happened. "It is not
    a drift" is true; "therefore it is parity" does not follow, and that was
    the second half-step nobody had written. The test now asserts the two
    things separately: it is not a drift, AND it is not a confirmation.
    """
    from invisible_playwright import _session

    def explode(*a, **k):
        raise RuntimeError("network is down")

    monkeypatch.setattr("invisible_core._geo.discover_egress_ip", explode)
    outcome, current = _session.egress_ancora_valido(
        {"server": "http://p:1"}, "203.0.113.5")
    assert outcome != _session.USCITA_DERIVATA, "a timeout is not a drift"
    assert outcome != _session.USCITA_REGGE, (
        "and it is not a confirmation either: no measurement took place")
    assert outcome == _session.USCITA_NON_MISURABILE
    assert current is None


@pytest.mark.unit
def test_both_classes_are_checked(monkeypatch):
    """The defect that `_session.py` exists to not repeat.

    Three real bugs were born from a fix that reached only one of the two
    classes. This test watches that the check is wired into BOTH.

    ⛔ AND IT READS THE SYNTAX TREE, not the source text. The first draft did
    `sorgente.count("_assert_uscita_invariata") == 2`, and that count has two
    defects: it expects an EXACT number, so it goes red the moment a legitimate
    checkpoint is added, and it also counts mentions in COMMENTS, so the
    number is not even the number of calls. Once surveillance on
    `context.new_page` was added the count became 4: three calls and one
    mention in a comment.

    The checkpoints are THREE, and the third is the one that was missing:
    `browser.new_context`, `browser.new_page`, and **`context.new_page`**,
    which is the NORMAL way to open a tab. Without the third, a session that
    opens one context and then N pages ran ONE check only, at the very first
    instant. Measured on 2026-08-25: at launch the egress was one address,
    nine tabs later another, and the two showed up together on the same
    detector page.
    """
    import ast
    import inspect
    import textwrap
    from invisible_playwright import async_api, launcher

    def _calls(fn):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", getattr(n.func, "id", None))
                == "_assert_uscita_invariata"]

    for module in (launcher, async_api):
        fn = module.InvisiblePlaywright._patch_new_context_defaults
        calls = _calls(fn)
        assert len(calls) == 3, (
            f"{module.__name__}: the egress check must be wired into THREE "
            f"places - browser.new_context, browser.new_page and "
            f"context.new_page - and I counted {len(calls)}. "
            f"context.new_page is the normal way to open a tab: without it, "
            f"a long session stops watching after the very first instant")


# ---------------------------------------------------------------------------
# The egress check has THREE outcomes, and the third is the one that was
# missing.
# ---------------------------------------------------------------------------

def _with_probe(monkeypatch, behavior):
    """Replaces the network probe with `behavior`, which may also raise."""
    from invisible_core import _geo
    monkeypatch.setattr(_geo, "discover_egress_ip", behavior)


def test_a_dropped_probe_is_not_a_confirmation(monkeypatch):
    """⛔ THE DEFECT THIS TEST EXISTS TO KEEP CLOSED.

    Until 2026-08-25 `egress_ancora_valido` returned `(True, None)` when the
    probe FAILED. The comment beside it argued the correct half - a failed
    discovery is not a drift, and that is true - but the returned value said
    `holds`, i.e. it asserted parity on the basis of a measurement that had
    never happened. It is the same class as `fppro_consistency.py`, which
    printed CONSISTENCY PASS when `visitor_id` was silent on both runs.
    """
    from invisible_playwright import _session

    def explode(*a, **k):
        raise OSError("network is down")

    _with_probe(monkeypatch, explode)
    outcome, ip = _session.egress_ancora_valido({"server": "socks5://x"}, "203.0.113.5")
    assert outcome == _session.USCITA_NON_MISURABILE, (
        "a dropped probe cannot answer 'holds': it measured nothing")
    assert outcome != _session.USCITA_REGGE
    assert ip is None


def test_the_three_outcomes_are_distinct_and_all_reachable(monkeypatch):
    from invisible_playwright import _session

    _with_probe(monkeypatch, lambda *a, **k: "203.0.113.5")
    assert _session.egress_ancora_valido({"server": "s"}, "203.0.113.5")[0] == _session.USCITA_REGGE

    _with_probe(monkeypatch, lambda *a, **k: "198.51.100.9")
    drifted = _session.egress_ancora_valido({"server": "s"}, "203.0.113.5")
    assert drifted[0] == _session.USCITA_DERIVATA
    assert drifted[1] == "198.51.100.9", "the current IP feeds the rejection message"

    def explode(*a, **k):
        raise RuntimeError("boom")
    _with_probe(monkeypatch, explode)
    assert (_session.egress_ancora_valido({"server": "s"}, "203.0.113.5")[0]
            == _session.USCITA_NON_MISURABILE)

    assert len({_session.USCITA_REGGE, _session.USCITA_DERIVATA,
                _session.USCITA_NON_MISURABILE}) == 3


def test_without_a_proxy_there_is_nothing_to_betray():
    """Unlike the other two: here it is not a measurement that is missing, it
    is an obligation."""
    from invisible_playwright import _session
    assert _session.egress_ancora_valido(None, None)[0] == _session.USCITA_REGGE
    assert _session.egress_ancora_valido({"server": "s"}, None)[0] == _session.USCITA_REGGE


def test_a_burst_of_silent_probes_rejects_the_session():
    """One drop: network. Three in a row: blindness, and the session is no
    longer defensible."""
    from invisible_playwright import _session
    from invisible_playwright.launcher import InvisiblePlaywright

    assert InvisiblePlaywright._MAX_USCITE_NON_MISURABILI >= 2, (
        "rejecting on the FIRST silent probe turns every timeout into an error")
    assert issubclass(_session.ProxyEgressNonVerificabile, RuntimeError)
    assert _session.ProxyEgressNonVerificabile is not _session.ProxyEgressDrifted, (
        "unmeasurable and drifted are two different diagnoses and must be "
        "distinguished by type too, or a catch cannot react differently")
    # what goes unconfirmed is the timezone and the locale decided from the egress: the engine declares no egress address
    doc = " ".join(_session.ProxyEgressNonVerificabile.__doc__.split())
    assert "timezone" in doc and "locale" in doc and "an address" not in doc, doc
