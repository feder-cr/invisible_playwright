"""A whole, hermetic road for a page that asks for a TURN allocation BY NAME behind a SOCKS5 gateway that carries UDP.

Nothing here is a real server of anybody: everything is on loopback, and the only way to anything is the gateway.

* :class:`TurnServer` - a TURN server (RFC 5766, the part a browser uses): Allocate answered 401 with a realm and a nonce, then
  answered with the relayed and the mapped address under a long-term credential, checked and signed (MESSAGE-INTEGRITY), so
  the browser's ICE agent accepts it as it accepts a real one. It records the SOURCE it saw for every datagram: that is the
  address a real TURN server would report as the browser's, and the one the verdict compares with.
* :class:`DnsServer` - answers an A query for the TURN name and for the page's name; the only resolver of the world.
* :class:`Chain` - the SOCKS5 gateway of ``_socks_servers.Gateway`` (greeting, ASSOCIATE, relay and the round trip of a gateway
  that is far away, ``rtt_ms``, are all there, not here), told where this world's names and datagrams go: TCP CONNECT to the
  page's name goes to the page server; a datagram to the TURN's documentation address goes to the TURN server, a datagram to
  port 53 goes to the DNS server, a datagram to the TURN NAME (SOCKS5 address type 3: the gateway resolves it) goes to the TURN
  server, and a STUN binding request to anything else is answered with the exit. Datagrams leave from ``EXIT_HOST`` (another
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

from _socks_servers import Gateway

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


def this_host_address() -> str:
    """The address of this host that a socket bound to an interface can send from: the relay's BND.ADDR (a loopback relay
    only suits a client whose UDP socket is not bound to an interface, and an engine's ICE sockets are)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    finally:
        s.close()


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


class DnsServer:
    """A resolver that knows two names. ``queries`` is every name asked, in order."""

    def __init__(self, names: Dict[str, str]) -> None:
        self.names = {k.lower(): v for k, v in names.items()}
        self.queries: List[str] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address: Tuple[str, int] = self.sock.getsockname()
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self) -> None:
        self.sock.close()

    def answer(self, q: bytes) -> Optional[bytes]:
        if len(q) < 12:
            return None
        i, labels = 12, []
        while i < len(q) and q[i]:
            labels.append(q[i + 1:i + 1 + q[i]].decode(errors="replace"))
            i += 1 + q[i]
        name = ".".join(labels).lower()
        qtype = struct.unpack("!H", q[i + 1:i + 3])[0]
        self.queries.append(name)
        question = q[12:i + 5]
        ip = self.names.get(name)
        if ip is None:
            return q[:2] + b"\x81\x83" + q[4:6] + b"\x00\x00\x00\x00\x00\x00" + question
        if qtype != 1:                                           # AAAA and the rest: the name exists, nothing of that type
            return q[:2] + b"\x81\x80" + q[4:6] + b"\x00\x00\x00\x00\x00\x00" + question
        return (q[:2] + b"\x81\x80" + q[4:6] + b"\x00\x01\x00\x00\x00\x00" + question +
                b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + socket.inet_aton(ip))

    def _serve(self) -> None:
        while True:
            try:
                q, src = self.sock.recvfrom(4096)
            except OSError:
                return
            r = self.answer(q)
            if r:
                self.sock.sendto(r, src)


# ── the gateway ────────────────────────────────────────────────────────────────

def _literal(host: str) -> str:
    """``host`` when it is an IPv4 literal; else the TURN address (a datagram sent by name is answered as from there)."""
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        return TURN_IP


class Chain(Gateway):
    """The SOCKS5 gateway, with the TURN server, the resolver and the page behind it. A context manager."""

    #: What the product asks "what is my address" of: answered with the exit.
    whoami_names = ("api.ipify.org", "ifconfig.me", "icanhazip.com")

    def __init__(self, *, rtt_ms: int = 130, relay_host: Optional[str] = None) -> None:
        self.turn = TurnServer()
        self.dns = DnsServer({TURN_NAME: TURN_IP, PAGE_NAME: "127.0.0.1"})
        self.page = Page(self)
        super().__init__(udp="relay", tcp_exit=EXIT_HOST, udp_exit=EXIT_HOST,
                         relay_host=relay_host or this_host_address(), rtt_ms=rtt_ms,
                         exit_host=EXIT_HOST, tcp_data_delay=True)

    def close(self) -> None:
        super().close()
        self.turn.close()
        self.dns.close()
        self.page.close()

    def _target(self, host: str, port: int) -> Tuple[str, int]:
        return ("127.0.0.1", self.page.port) if host.lower() == PAGE_NAME else (host, port)

    def _route(self, host, dport, atyp, payload, out_port, answer):
        is_turn = (host == TURN_IP or host.lower() == TURN_NAME) and dport == TURN_PORT
        if is_turn:
            target = self.turn.address
        elif dport == 53:
            target = self.dns.address
        elif len(payload) >= 20 and payload[:2] == b"\x00\x01" and struct.unpack("!I", payload[4:8])[0] == MAGIC:
            # a STUN binding request to anyone else: answered by the gateway, with the exit as the mapped address
            answer(_literal(host), dport, _message(0x0101, payload[8:20], _attr(0x0020, _xor_addr(EXIT_HOST, out_port))))
            return None
        else:
            return None
        return target, (_literal(TURN_IP if is_turn else host), dport)

    # --- what a failed test says ----------------------------------------------
    def explain(self, result: dict) -> str:
        """The road, as the three servers and the page saw it, and the sentence that says where it stopped."""
        turn_heard = [s for s in self.turn.seen]
        names_asked = [h for h, _p, a, _t in self.datagram_log if a == 3]
        dns_asked = self.dns.queries
        lines = ["", "THE ROAD, as the servers saw it:"]
        lines.append("  gateway: %d UDP ASSOCIATE (start -> answered, seconds): %s" % (
            len(self.associations), ", ".join("%.2f->%s" % (a, "%.2f" % b if b is not None else "-") for a, b in self.associations) or "none"))
        lines.append("  gateway: datagrams carried out: %d (to the TURN address/name: %d, DNS queries through it: %d, names sent by the relay-resolves road: %d)" % (
            len(self.datagram_log), sum(1 for h, p, _a, _t in self.datagram_log if p == TURN_PORT),
            len(dns_asked), len(names_asked)))
        lines.append("  TURN server heard %d datagram(s) %s; allocations: %s" % (
            len(turn_heard), [("%s:%d" % (s[0], s[1]), hex(s[2])) for s in turn_heard][:6],
            [("%s:%d" % (a[0], a[1])) for a in self.turn.allocations]))
        lines.append("  page verdicts: %s" % (self.page.verdicts or "none: no server-reflexive candidate came before the tick, or the POST did not finish"))
        lines.append("  page: %s" % json.dumps({k: result.get(k) for k in ("status", "timeline", "errors", "srflx", "candidates")}, default=str)[:900])
        if not turn_heard:
            if not dns_asked and not names_asked:
                why = ("the browser never tried to reach the TURN server: its name %r was not resolved through the relay "
                       "(no DNS query reached the resolver and no datagram carried the name), so no allocation was ever asked for; "
                       "the only server-reflexive candidate the page can have is one the engine made up" % TURN_NAME)
            else:
                why = "the name was asked (%s) but no datagram reached the TURN server: the answer did not come back, or came after the tick" % (dns_asked or names_asked)
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
    """The page server (the browser reaches it by NAME, through the gateway) and the verdict."""

    def __init__(self, chain: "Chain") -> None:
        outer = self
        self.verdicts: List[dict] = []

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *a):
                pass

            def do_GET(self):
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
