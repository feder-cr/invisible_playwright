"""A minimal TURN server over UDP, for the measurement of a retail Firefox on a runner: just enough of RFC 5766 for a browser to get an
allocation (Allocate answered 401 with a realm and a nonce, then a success signed with the long-term credential), plus Binding.

Why it exists: the question measured with it is how many TURN-over-TCP allocations a retail Firefox attempts on a host with ONE IPv4 interface
and when they fail, with a TURN server of its own that answers over UDP and refuses TCP (nothing listens on the TCP port). It needs no
third-party server and no credentials; it hears and answers on one address given on the command line.

    python turn_server.py --host <ipv4> --port 3478        (stops on Ctrl-C)

As a library: ``TurnServer(host, port)`` starts a thread; ``.seen`` is every datagram as (source ip, source port, STUN message type),
``.allocations`` the successful Allocates as (source ip, source port, username); ``.close()`` stops it.
"""
import argparse
import hashlib
import hmac
import socket
import struct
import threading
import time

MAGIC = 0x2112A442
REALM = "measure"
NONCE = "6e6f6e6365"
USERNAME = "measure"
PASSWORD = "measure"
LIFETIME = 600


def attrs_of(data):
    out, i = {}, 20
    while i + 4 <= len(data):
        t, n = struct.unpack("!HH", data[i:i + 4])
        out[t] = data[i + 4:i + 4 + n]
        i += 4 + ((n + 3) & ~3)
    return out


def attr(t, v):
    return struct.pack("!HH", t, len(v)) + v + b"\x00" * (-len(v) % 4)


def xor_addr(ip, port):
    xp = port ^ (MAGIC >> 16)
    xa = struct.unpack("!I", socket.inet_aton(ip))[0] ^ MAGIC
    return b"\x00\x01" + struct.pack("!HI", xp, xa)


def message(mtype, tid, body, key=None):
    if key is None:
        return struct.pack("!HHI", mtype, len(body), MAGIC) + tid + body
    head = struct.pack("!HHI", mtype, len(body) + 24, MAGIC) + tid
    return head + body + attr(0x0008, hmac.new(key, head + body, hashlib.sha1).digest())


class TurnServer:
    def __init__(self, host, port=0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.address = self.sock.getsockname()
        self.seen = []
        self.allocations = []
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self._closed = True
        self.sock.close()

    def _serve(self):
        while not self._closed:
            try:
                data, src = self.sock.recvfrom(65536)
            except ConnectionResetError:
                # Windows reports the ICMP "port unreachable" of an earlier send (a client that closed its socket) as an error of the NEXT
                # recvfrom (WSAECONNRESET, 10054): the server goes on, it is not the end of the socket.
                continue
            except OSError:
                return
            if len(data) < 20 or struct.unpack("!I", data[4:8])[0] != MAGIC:
                continue
            mtype, tid = struct.unpack("!H", data[:2])[0], data[8:20]
            self.seen.append((src[0], src[1], mtype))
            at = attrs_of(data)
            if mtype == 0x0001:                                           # Binding
                self.sock.sendto(message(0x0101, tid, attr(0x0020, xor_addr(*src))), src)
            elif mtype == 0x0003:                                         # Allocate
                if 0x0006 not in at or 0x0008 not in at:                  # no credentials: 401 with a realm and a nonce
                    err = attr(0x0009, b"\x00\x00\x04\x01Unauthorized") + attr(0x0014, REALM.encode()) + attr(0x0015, NONCE.encode())
                    self.sock.sendto(message(0x0113, tid, err), src)
                    continue
                user = at[0x0006].decode(errors="replace")
                key = hashlib.md5(("%s:%s:%s" % (user, REALM, PASSWORD)).encode()).digest()
                relayed = (self.address[0], 50000)
                body = attr(0x0016, xor_addr(*relayed)) + attr(0x0020, xor_addr(*src)) + attr(0x000D, struct.pack("!I", LIFETIME))
                self.allocations.append((src[0], src[1], user, time.time()))
                self.sock.sendto(message(0x0103, tid, body, key), src)
            elif mtype in (0x0004, 0x0008, 0x0009):                        # Refresh, CreatePermission, ChannelBind
                user = at.get(0x0006, b"").decode(errors="replace")
                key = hashlib.md5(("%s:%s:%s" % (user, REALM, PASSWORD)).encode()).digest()
                body = attr(0x000D, struct.pack("!I", LIFETIME)) if mtype == 0x0004 else b""
                self.sock.sendto(message(mtype | 0x0100, tid, body, key), src)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=3478)
    a = ap.parse_args(argv)
    server = TurnServer(a.host, a.port)
    print("TURN over UDP on %s:%d" % server.address, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.close()


if __name__ == "__main__":
    main()
