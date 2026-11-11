"""A whole, hermetic road for a page that asks for a TURN allocation BY NAME behind a SOCKS5 gateway that carries UDP.

Nothing here is a real server of anybody: everything is on loopback, and the only way to anything is the gateway.

The design this road keeps: a browser behind a proxy resolves every name with the DoH of Firefox, THROUGH the proxy
(``network.trr.mode`` 3, ``https://mozilla.cloudflare-dns.com/dns-query``, found with no DNS at 162.159.61.4). So the name of the
TURN server is a DoH question, carried by a SOCKS5 CONNECT; the answer is an ADDRESS, and the datagrams of the allocation go to
that address, by SOCKS5 address type 1, through the UDP relay. The gateway resolves nothing and answers no DNS query.

* :class:`TurnServer` - a TURN server (RFC 5766, the part a browser uses): Allocate answered 401 with a realm and a nonce, then
  answered with the relayed and the mapped address under a long-term credential, checked and signed (MESSAGE-INTEGRITY), so
  the browser's ICE agent accepts it as it accepts a real one. It records the SOURCE it saw for every datagram: that is the
  address a real TURN server would report as the browser's, and the one the verdict compares with.
* :class:`Chain` - the SOCKS5 gateway of the tests (``_socks_servers.Gateway``, with the DoH road of
  ``_doh_servers.DohGateway``: greeting, ASSOCIATE, relay and the round trip of a gateway that is far away, ``rtt_ms``, for the
  control connection, the datagrams and the TCP tunnels, are all there, not here). Told where this world's roads go: a CONNECT
  to the DoH goes to the :class:`_doh_servers.FakeDoh` of the chain, whose zone is the TURN name and the page name; a CONNECT
  to a loopback address (the page, once its name is resolved) is relayed; a datagram to the TURN's documentation address goes
  to the TURN server; a STUN binding request to anything else is answered with the exit. A datagram that carries a NAME or
  goes to port 53 is dropped and recorded (the roads the product must not use). Datagrams leave from ``EXIT_HOST`` (another
  loopback address), so the TURN server sees a source that is not the browser's own: if it sees the browser's, the road was
  not the gateway's.
* :class:`Page` - the page and the verdict, with the shape of a leak test's logic: a tick of 2 seconds after the page starts that
  marks TIMEOUT, cleared only after a POST made when a server-reflexive candidate arrives; the server compares the address
  of that candidate with the source the TURN saw.

The names and the addresses are RFC 2606 / RFC 5737: nothing in a test can be mistaken for a real host.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

from _doh_servers import DohGateway, FakeDoh
from _host_address import this_host_address
from _socks_servers import WHOAMI4, Gateway

TURN_NAME = "turn.relay-only.test"
PAGE_NAME = "page.relay-only.test"
#: RFC 5737 TEST-NET-2: unroutable, so the only road to it is the gateway, which maps it to the TURN server.
TURN_IP = "198.51.100.7"
TURN_PORT = 3478
RELAYED = ("198.51.100.7", 50000)
#: Where the gateway's datagrams come from. A browser that sends direct comes from 127.0.0.1.
EXIT_HOST = "127.0.0.2"
MAGIC = 0x2112A442
PASSWORD = "qwerty"
REALM = "hermetic"
NONCE = "6e6f6e6365"


# ── STUN / TURN wire ───────────────────────────────────────────────────────────

def _attrs(data: bytes) -> Dict[int, bytes]:
    out: Dict[int, bytes] = {}
    i = 20
    while i + 4 <= len(data):
        t, n = struct.unpack("!HH", data[i:i + 4])
        out[t] = data[i + 4:i + 4 + n]
        i += 4 + ((n + 3) & ~3)
    return out


def _attr(t: int, v: bytes) -> bytes:
    return struct.pack("!HH", t, len(v)) + v + b"\x00" * (-len(v) % 4)


def _xor_addr(ip: str, port: int) -> bytes:
    xp = port ^ (MAGIC >> 16)
    xa = struct.unpack("!I", socket.inet_aton(ip))[0] ^ MAGIC
    return b"\x00\x01" + struct.pack("!HI", xp, xa)


def _message(mtype: int, tid: bytes, attrs: bytes, key: Optional[bytes] = None) -> bytes:
    if key is None:
        return struct.pack("!HHI", mtype, len(attrs), MAGIC) + tid + attrs
    head = struct.pack("!HHI", mtype, len(attrs) + 24, MAGIC) + tid
    mac = hmac.new(key, head + attrs, hashlib.sha1).digest()
    return head + attrs + _attr(0x0008, mac)


class TurnServer:
    """UDP TURN server on 127.0.0.1. ``seen`` is every datagram as (source ip, source port, STUN type), in order;
    ``allocations`` the successful Allocates as (source ip, source port, username, time)."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address: Tuple[str, int] = self.sock.getsockname()
        self.seen: List[Tuple[str, int, int]] = []
        self.allocations: List[Tuple[str, int, str, float]] = []
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        self.sock.close()

    def _serve(self) -> None:
        while not self._closed:
            try:
                data, src = self.sock.recvfrom(65536)
            except OSError:
                return
            if len(data) < 20 or struct.unpack("!I", data[4:8])[0] != MAGIC:
                continue
            mtype, tid = struct.unpack("!H", data[:2])[0], data[8:20]
            self.seen.append((src[0], src[1], mtype))
            attrs = _attrs(data)
            if mtype == 0x0003:                                   # Allocate
                if 0x0008 not in attrs or 0x0006 not in attrs:   # no credentials: 401 with a realm and a nonce
                    err = _attr(0x0009, b"\x00\x00\x04\x01Unauthorized") + _attr(0x0014, REALM.encode()) + _attr(0x0015, NONCE.encode())
                    self.sock.sendto(_message(0x0113, tid, err), src)
                    continue
                user = attrs[0x0006].decode(errors="replace")
                key = hashlib.md5(("%s:%s:%s" % (user, REALM, PASSWORD)).encode()).digest()
                body = (_attr(0x0016, _xor_addr(*RELAYED)) + _attr(0x0020, _xor_addr(src[0], src[1])) +
                        _attr(0x000D, struct.pack("!I", 600)))
                self.allocations.append((src[0], src[1], user, time.monotonic()))
                self.sock.sendto(_message(0x0103, tid, body, key), src)
            elif mtype in (0x0004, 0x0008, 0x0009):                # Refresh, CreatePermission, ChannelBind
                user = attrs.get(0x0006, b"").decode(errors="replace")
                key = hashlib.md5(("%s:%s:%s" % (user, REALM, PASSWORD)).encode()).digest()
                body = _attr(0x000D, struct.pack("!I", 600)) if mtype == 0x0004 else b""
                self.sock.sendto(_message(mtype | 0x0100, tid, body, key), src)


# ── the gateway ────────────────────────────────────────────────────────────────

class Chain(DohGateway):
    """The one SOCKS5 gateway of the tests (``_socks_servers.Gateway`` through ``_doh_servers.DohGateway``), with UDP, the
    DoH, the TURN server and the page behind it. A context manager.

    What it does, and nothing else (it never resolves a name and never answers a DNS query):

    * a CONNECT to the DoH (``162.159.61.4:443`` or ``mozilla.cloudflare-dns.com:443``) is handed to :attr:`doh`, whose zone
      is the TURN name and the page name; a CONNECT to a loopback ADDRESS (the page, once its name is resolved) is relayed;
      a CONNECT that carries any other name or address is refused and listed in :attr:`refused`;
    * a datagram to ``TURN_IP:3478`` (address type 1) goes to the TURN server, which hears it from ``EXIT_HOST``; a STUN
      binding request to anyone else is answered with the exit; everything else is dropped. A datagram that carries a NAME
      (address type 3) or goes to port 53 is dropped and recorded: those are roads the product must not use.
    """

    #: What the product asks "what is my address" of: answered with the exit.
    whoami_names = ("api.ipify.org", "ifconfig.me", "icanhazip.com")

    def __init__(self, *, rtt_ms: int = 130, relay_host: Optional[str] = None) -> None:
        # The address of this host first: on a host with no default route the test skips, with no server started yet.
        relay_host = relay_host or this_host_address()
        self.turn = TurnServer()
        self.doh = FakeDoh({TURN_NAME: TURN_IP, PAGE_NAME: "127.0.0.1"})
        self.page = Page(self)
        super().__init__("chain", self.doh, udp="relay", tcp_exit=EXIT_HOST, udp_exit=EXIT_HOST,
                         relay_host=relay_host, rtt_ms=rtt_ms,
                         exit_host=EXIT_HOST, tcp_data_delay=True)

    def close(self) -> None:
        super().close()
        self.turn.close()
        self.doh.close()
        self.page.close()

    def _connect(self, c, host, port) -> None:
        if host.lower() in self.whoami_names and port == WHOAMI4[1]:
            Gateway._connect(self, c, host, port)       # the answer of "what is my address"
            return
        super()._connect(c, host, port)

    def _route(self, host, dport, atyp, payload, out_port, answer):
        if atyp == 1 and host == TURN_IP and dport == TURN_PORT:
            return self.turn.address, (host, dport)      # the TURN server answers as from the address it was asked at
        if (atyp == 1 and dport != 53 and len(payload) >= 20 and payload[:2] == b"\x00\x01"
                and struct.unpack("!I", payload[4:8])[0] == MAGIC):
            # a STUN binding request to anyone else: answered by the gateway, with the exit as the mapped address
            answer(host, dport, _message(0x0101, payload[8:20], _attr(0x0020, _xor_addr(EXIT_HOST, out_port))))
        return None                                      # a name, port 53, anything else: dropped (and in datagram_log)

    # --- what a failed test says ----------------------------------------------
    def explain(self, result: dict) -> str:
        """The road, as the DoH, the gateway, the TURN server and the page saw it, and the sentence that says where it stopped."""
        turn_heard = list(self.turn.seen)
        turn_asked = [q for q in self.doh.queries if q.name == TURN_NAME]
        names_on_relay = [(h, p) for h, p, a, _t in self.datagram_log if a == 3]
        dns_on_relay = [(h, p) for h, p, _a, _t in self.datagram_log if p == 53]
        to_turn = [1 for h, p, a, _t in self.datagram_log if a == 1 and h == TURN_IP and p == TURN_PORT]
        doh_connects = self.doh_connects()
        lines = ["", "THE ROAD, as each server saw it:"]
        lines.append("  DoH (reached only through the gateway): %d connection(s) %s, %d TLS failure(s) %s" % (
            len(self.doh.connections), self.doh.connections[:4], len(self.doh.tls_failures), self.doh.tls_failures[:2]))
        lines.append("  DoH queries (name, type, road): %s" % ([tuple(q) for q in self.doh.queries][:12] or "none"))
        lines.append("  gateway: CONNECTs %s; refused %s; the DoH ones: %d" % (self.connects[:12], self.refused[:6], len(doh_connects)))
        lines.append("  gateway: %d UDP ASSOCIATE (start -> answered, seconds): %s" % (
            len(self.associations), ", ".join("%.2f->%s" % (a, "%.2f" % b if b is not None else "-") for a, b in self.associations) or "none"))
        lines.append("  gateway: datagrams carried: %d (to %s:%d by address: %d; carrying a NAME, dropped: %s; to port 53, dropped: %s)" % (
            len(self.datagram_log), TURN_IP, TURN_PORT, len(to_turn), names_on_relay[:4] or "none", dns_on_relay[:4] or "none"))
        lines.append("  TURN server heard %d datagram(s) %s; allocations: %s" % (
            len(turn_heard), [("%s:%d" % (s[0], s[1]), hex(s[2])) for s in turn_heard][:6],
            [("%s:%d" % (a[0], a[1])) for a in self.turn.allocations]))
        lines.append("  page server: asked for %s; verdicts: %s" % (
            self.page.hits[:4] or "nothing", self.page.verdicts or "none: no server-reflexive candidate came before the tick, or the POST did not finish"))
        lines.append("  page: %s" % json.dumps({k: result.get(k) for k in ("status", "timeline", "errors", "srflx", "candidates")}, default=str)[:900])
        if not turn_asked:
            if names_on_relay or dns_on_relay:
                why = ("the name %r went along the UDP relay (%s) instead of being asked to the DoH: the gateway resolves nothing, so "
                       "no allocation could be asked for" % (TURN_NAME, names_on_relay or dns_on_relay))
            elif self.doh.tls_failures:
                why = "the browser reached the DoH but refused its certificate (%s): the context must accept it (ignore_https_errors)" % (self.doh.tls_failures[:1],)
            elif not doh_connects:
                why = ("the browser never asked the DoH: no CONNECT to the DoH reached the gateway (%s), so the name %r was not "
                       "resolved through the proxy and the browser never tried to reach the TURN server; the only "
                       "server-reflexive candidate the page can have is one the engine made up"
                       % (self.connects[:6] or "no CONNECT at all", TURN_NAME))
            else:
                why = ("the DoH was reached (%d connection(s)) but never asked for %r (it was asked %s): the lookup of the ICE "
                       "server name did not go to the DoH" % (len(self.doh.connections), TURN_NAME, self.doh.seen_names()))
        elif "direct" in {q.via for q in turn_asked}:
            why = "the name %r reached the DoH without the proxy (road 'direct')" % TURN_NAME
        elif not turn_heard:
            why = ("the name %r was resolved by the DoH (%s) but no datagram reached the TURN server: %s" % (
                TURN_NAME, sorted({q.via for q in turn_asked}),
                ("the browser sent none to %s:%d by address through the relay (%d association(s), datagrams %s)" % (
                    TURN_IP, TURN_PORT, len(self.associations), [(h, p, a) for h, p, a, _t in self.datagram_log][:4]))
                if not to_turn else "the answer did not come back"))
        elif not self.turn.allocations:
            why = "the TURN server heard the browser but no Allocate was answered with credentials: the 401 round trip did not finish"
        elif result.get("status") == "TIMEOUT":
            ends = [e for _s, e in self.associations if e is not None]
            why = ("the allocation succeeded but too late for the tick of 2000 ms: the association handshakes alone ended at %s s"
                   % ", ".join("%.2f" % e for e in ends))
        else:
            why = "the srflx of the page is not the address the TURN server saw (%s)" % [("%s:%d" % (a[0], a[1])) for a in self.turn.allocations]
        lines.append("WHY: " + why)
        return "\n".join(lines)


# ── the page and the verdict ───────────────────────────────────────────────────

PAGE_HTML = """<!doctype html><meta charset=utf-8><title>relay-only</title><script>
const t0 = performance.now(), mark = (k) => (window.__result.timeline[k] = Math.round(performance.now() - t0));
window.__result = {status: 'PENDING', timeline: {}, errors: [], candidates: [], srflx: null};
let status = 'PENDING';
const finish = (s) => { status = s; window.__result.status = s; window.__done = true; };
document.addEventListener('DOMContentLoaded', () => {
  mark('dom');
  // the tick of a leak test: 2 seconds after the page starts. It is cleared only after the POST made on a srflx.
  const tick = setInterval(() => { if (status === 'PENDING') { mark('tick'); finish('TIMEOUT'); } clearInterval(tick); }, 2000);
  const pc = new RTCPeerConnection({iceServers: [{urls: ['turn:%(turn)s:%(port)d?transport=udp'], username: 'challenge-' + Date.now(), credential: '%(password)s'}]});
  pc.createDataChannel('x');
  mark('pc');
  pc.onicegatheringstatechange = () => mark('gathering_' + pc.iceGatheringState);
  pc.onicecandidateerror = (e) => window.__result.errors.push([e.errorCode, e.url]);
  pc.onicecandidate = async (e) => {
    if (!e.candidate) return;
    window.__result.candidates.push(e.candidate.candidate);
    if (e.candidate.candidate.indexOf(' typ srflx ') < 0 || status !== 'PENDING') return;
    mark('srflx');
    window.__result.srflx = e.candidate.candidate;
    const r = await fetch('/verdict', {method: 'POST', body: JSON.stringify({srflx: e.candidate.candidate})});
    mark('posted');
    if (status !== 'PENDING') return;
    clearInterval(tick);
    finish((await r.json()).ok ? 'DONE' : 'REFUSED');
  };
  pc.createOffer().then((o) => pc.setLocalDescription(o));
});
</script>"""


class Page:
    """The page server (the browser asks the DoH for its NAME, then reaches the ADDRESS through the gateway) and the verdict."""

    def __init__(self, chain: "Chain") -> None:
        outer = self
        self.verdicts: List[dict] = []
        #: Every GET the page server answered: the Host header and the path.
        self.hits: List[Tuple[str, str]] = []

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append((self.headers.get("Host", ""), self.path))
                body = (PAGE_HTML % {"turn": TURN_NAME, "port": TURN_PORT, "password": PASSWORD}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                cand = json.loads(self.rfile.read(n) or b"{}").get("srflx", "")
                parts = cand.split()
                ip, port = (parts[4], int(parts[5])) if len(parts) > 5 else ("", 0)
                seen = [(a[0], a[1]) for a in chain.turn.allocations]
                ok = bool(seen) and (ip, port) in seen and ip == EXIT_HOST
                outer.verdicts.append({"srflx": "%s:%d" % (ip, port), "seen_by_the_turn": ["%s:%d" % s for s in seen], "ok": ok})
                body = json.dumps({"ok": ok}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port: int = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return "http://%s:%d/" % (PAGE_NAME, self.port)

    def close(self) -> None:
        self.httpd.shutdown()
