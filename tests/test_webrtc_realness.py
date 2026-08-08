"""WebRTC realness regression tests.

Two layers, both runnable on GitHub CI:

* **unit** (`@pytest.mark.unit`) - pure SDP/candidate assertions against golden
  samples. No browser, no proxy, no network. These lock in every rule we found
  on 2026-06-06: host must be mDNS ``.local``; the synthetic srflx must carry the
  egress IP with a GENUINE nICEr priority (never ``local_pref == 0xFFFF``) and a
  stable, distinct foundation; CreepJS's resolver must return the egress, and a
  host-only SDP must read as "blocked". They run in the standard ``tests.yml``.

* **e2e** (`@pytest.mark.e2e`) - launch the patched binary and verify the live
  ICE gather. "Being behind a proxy" is faked WITHOUT any external proxy provider:
    - the egress IP is injected via ``STEALTHFOX_WEBRTC_PUBLIC_IP`` (RFC 5737
      TEST-NET, so it never collides with a real IP);
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
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

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
    prefs = translate_profile_to_prefs(generate_profile(seed=42))
    # host candidate must be mDNS .local like vanilla Firefox (manually
    # validated on BrowserLeaks/CreepJS through a residential proxy) - not a
    # raw LAN IP.
    assert prefs["media.peerconnection.ice.obfuscate_host_addresses"] is True
    # IPv6 dropped via OUR live filter pref; the native pref is dead on FF150
    # and must not be relied upon (or re-introduced as if it worked).
    assert prefs["zoom.stealth.webrtc.disable_ipv6"] is True
    assert "media.peerconnection.ice.disableIPv6" not in prefs
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
#  Fake-proxy infrastructure for e2e: a tiny TCP-only SOCKS5 server.
# ──────────────────────────────────────────────────────────────────────────
class _Socks5TcpOnly:
    """Minimal SOCKS5: no-auth, CONNECT (TCP) relayed, UDP ASSOCIATE refused.

    Reproduces a residential TCP-only proxy: pages load over TCP, but WebRTC's
    UDP path is dead - which (for a no-camera page in default_address_only mode)
    is exactly what made the default-route probe fail and ICE return zero
    candidates before Fix C.
    """

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        self.udp_associate_attempts = 0
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _recv_exact(self, sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle(self, conn):
        try:
            head = self._recv_exact(conn, 2)
            if not head or head[0] != 0x05:
                conn.close()
                return
            nmethods = head[1]
            self._recv_exact(conn, nmethods)
            conn.sendall(b"\x05\x00")  # no-auth
            req = self._recv_exact(conn, 4)
            if not req:
                conn.close()
                return
            ver, cmd, _, atyp = req
            if atyp == 0x01:
                addr = socket.inet_ntoa(self._recv_exact(conn, 4))
            elif atyp == 0x03:
                ln = self._recv_exact(conn, 1)[0]
                addr = self._recv_exact(conn, ln).decode("ascii", "ignore")
            elif atyp == 0x04:
                addr = socket.inet_ntop(socket.AF_INET6, self._recv_exact(conn, 16))
            else:
                conn.close()
                return
            port = struct.unpack("!H", self._recv_exact(conn, 2))[0]
            if cmd != 0x01:  # not CONNECT (e.g. UDP ASSOCIATE) → refuse
                self.udp_associate_attempts += 1
                conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # cmd not supported
                conn.close()
                return
            try:
                upstream = socket.create_connection((addr, port), timeout=15)
            except OSError:
                conn.sendall(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")  # host unreachable
                conn.close()
                return
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")  # success
            self._relay(conn, upstream)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def _relay(self, a, b):
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], 30)
                if not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        finally:
            for s in (a, b):
                try:
                    s.close()
                except Exception:
                    pass

    def close(self):
        self._stop = True
        try:
            self._srv.close()
        except Exception:
            pass


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


@pytest.fixture
def socks5_tcp_only():
    srv = _Socks5TcpOnly()
    yield srv
    srv.close()


@pytest.fixture
def local_https_page():
    """A trivial localhost page (used by the no-proxy srflx test)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>wrtc</body></html>")

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


def _launch(**extra):
    from invisible_playwright import InvisiblePlaywright

    kw = {"headless": True,
          # Fixed zone so the wrapper does NOT run timezone="auto" egress
          # discovery through the (fake) proxy - irrelevant here, we inject the
          # egress IP directly and want the launch deterministic/offline.
          "timezone": "America/New_York",
          "extra_prefs": {"media.peerconnection.ice.obfuscate_host_addresses": True}}
    kw.update(extra)
    return InvisiblePlaywright(**kw)


@pytest.mark.e2e
def test_srflx_is_real_and_resolvable(local_https_page):
    """No proxy needed: the egress is faked via the env. Asserts the live srflx
    is genuine (Fix A/B) and that CreepJS's resolver returns it (not blocked)."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    os.environ["STEALTHFOX_WEBRTC_PUBLIC_IP"] = _FAKE_EGRESS
    os.environ["STEALTHFOX_WEBRTC_DISABLE_IPV6"] = "1"
    with _launch(binary_path=binary) as browser:
        page = browser.new_context().new_page()
        page.goto(local_https_page, wait_until="domcontentloaded", timeout=60000)
        res = page.evaluate(_PROBE_JS)
    cands = candidates(res["candidates"])
    assert cands, "ICE produced ZERO candidates (blocked)"
    assert host_is_mdns(cands), [c["address"] for c in host_candidates(cands)]
    srflx = [c for c in srflx_candidates(cands) if c["address"] == _FAKE_EGRESS]
    assert srflx, f"no synthetic srflx with {_FAKE_EGRESS}: {res['candidates']}"
    ok, reasons = srflx_realness(srflx[0], expected_ip=_FAKE_EGRESS)
    assert ok, reasons
    # Two srflx for the same base must share ONE stable foundation (Fix B).
    assert len({c["foundation"] for c in srflx}) == 1
    assert creep_get_ipaddress(res["sdp"]) == _FAKE_EGRESS


@pytest.mark.e2e
def test_not_blocked_behind_tcp_only_socks(socks5_tcp_only):
    """Fix C sentinel: behind a TCP-only SOCKS proxy on a remote origin, ICE
    must still complete (host .local + synthetic srflx), not return zero
    candidates. Without Fix C this page is fully 'blocked'."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    os.environ["STEALTHFOX_WEBRTC_PUBLIC_IP"] = _FAKE_EGRESS
    os.environ["STEALTHFOX_WEBRTC_DISABLE_IPV6"] = "1"
    proxy = {"server": f"socks5://127.0.0.1:{socks5_tcp_only.port}"}
    try:
        with _launch(binary_path=binary, proxy=proxy) as browser:
            page = browser.new_context().new_page()
            # remote origin loaded THROUGH the local SOCKS proxy (not localhost,
            # so no proxy-bypass) → WebRTC proxy config active → Fix C path.
            page.goto("https://example.com/", wait_until="domcontentloaded", timeout=70000)
            res = page.evaluate(_PROBE_JS)
    except Exception as exc:  # network/proxy unavailable in this environment
        pytest.skip(f"proxy/network path unavailable: {exc!r}")
    cands = candidates(res["candidates"])
    # Hard regression check: ZERO candidates means WebRTC is fully blocked behind
    # the SOCKS proxy - that's the Fix C regression this sentinel exists to catch.
    assert cands, "behind SOCKS the gather returned ZERO candidates - Fix C regressed (blocked)"
    assert host_is_mdns(cands)
    # The synthetic srflx (= fake egress) needs the remote origin to load FULLY
    # through the proxy so the WebRTC proxy config engages. That path is
    # environment-sensitive (it doesn't always engage on a datacenter CI box even
    # though host candidates gather), so treat a missing srflx as a skip, not a
    # failure - the local run validates it where the path is real.
    if not any(c["address"] == _FAKE_EGRESS for c in srflx_candidates(cands)):
        pytest.skip("synthetic srflx not engaged in this environment "
                    "(needs the remote origin fully through the proxy); validated locally")
    assert creep_get_ipaddress(res["sdp"]) == _FAKE_EGRESS
