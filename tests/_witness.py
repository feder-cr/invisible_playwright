"""A witness: a STUN server (UDP) and a TCP listener on ONE local address and port, that writes down who contacted it.

What it is for: a datagram or a connection a browser sends DIRECT, around its proxy, is invisible in a laboratory whose servers sit at
documentation addresses that nothing routes (a leak and a drop look the same: nothing arrives). The witness stands at an address the
browser can reach directly (a documentation address held on ``lo``, see ``_turn_chain.lab_exit_address``), so a leak ARRIVES, from a
source that is not the exit of the proxy, and a test can tell a leak from a drop. It answers a STUN Binding Request with the source it
saw, so a road that works also gives the page a server-reflexive candidate.

``udp`` is every datagram as (source ip, source port, length); ``tcp`` is every connection as (source ip, source port).
"""
from __future__ import annotations

import socket
import sys
import struct
import threading
from typing import Dict, Iterable, List, Tuple

_MAGIC = b"\x21\x12\xa4\x42"

#: How many port numbers the witness tries before it gives up, and where it draws them from after the system's own pick: the sequence of
#: tests/_ports.py, which the HTTP/3 server and the resolver trap walk too. Below the start of Windows' dynamic range (49152), where its excluded
#: ranges are reserved in blocks of a hundred, and below Linux's ephemeral range (32768): a number drawn there is not the next of a sequence the
#: system hands out.
from _ports import DRAW_FROM, TRIES, candidates  # noqa: E402


class Witness:
    def __init__(self, address: str, port: int = 0) -> None:
        self.address = address
        self.udp: List[Tuple[str, int, int]] = []
        self.tcp: List[Tuple[str, int]] = []
        self._lock = threading.Lock()
        self._closed = False
        # One port number for both, TCP and UDP. Each try binds TCP and then UDP on the same number; if either is refused, both
        # are released and another number is tried. The first try is the system's own pick (TCP, port 0). The others are drawn
        # at random: Windows hands out ephemeral TCP ports in sequence and keeps excluded ranges of a hundred ports for UDP
        # inside the same zone, so "the next pick" lands in the same refused range again and again (WinError 10013, twenty
        # tries in a row on a work machine). A given ``port`` is tried once and its refusal is raised as it is.
        self._udp, self._tcp, self.port = self._bind_both(address, port)
        threading.Thread(target=self._serve_udp, daemon=True).start()
        threading.Thread(target=self._serve_tcp, daemon=True).start()

    @staticmethod
    def _bind_both(address: str, port: int):
        refusals: List[str] = []
        for wanted in ([port] if port else candidates(TRIES)):
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            udp = None
            try:
                if sys.platform != "win32":    # on Windows SO_REUSEADDR is "share the port": it breaks bind
                    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp.bind((address, wanted))
                chosen = tcp.getsockname()[1]
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp.bind((address, chosen))
                tcp.listen(16)
                return udp, tcp, chosen
            except OSError as exc:
                if port:
                    tcp.close()
                    if udp is not None:
                        udp.close()
                    raise
                refusals.append("%s: %s" % (wanted or "system pick", exc))
                tcp.close()
                if udp is not None:
                    udp.close()
        raise OSError("the witness found no port number on %s where both TCP and UDP bind, after %d tries; the last refusals: %s"
                      % (address, TRIES, "; ".join(refusals[-3:])))

    # --- what a test reads ------------------------------------------------------------------------------------------
    def heard(self) -> Dict[str, list]:
        with self._lock:
            return {"udp": list(self.udp), "tcp": list(self.tcp)}

    def heard_from_other_than(self, allowed: Iterable[str]) -> Dict[str, list]:
        """What the witness heard from a source that is not one of ``allowed`` (the exit of the proxy): a direct send."""
        ok = set(allowed)
        heard = self.heard()
        return {"udp": [h for h in heard["udp"] if h[0] not in ok], "tcp": [h for h in heard["tcp"] if h[0] not in ok]}

    @property
    def target(self) -> str:
        return "%s:%d" % (self.address, self.port)

    # --- serving ----------------------------------------------------------------------------------------------------
    def close(self) -> None:
        self._closed = True
        for s in (self._udp, self._tcp):
            try:
                s.close()
            except OSError:
                pass

    def __enter__(self) -> "Witness":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _serve_udp(self) -> None:
        while not self._closed:
            try:
                data, peer = self._udp.recvfrom(2048)
            except ConnectionResetError:
                continue
            except OSError:
                return
            with self._lock:
                self.udp.append((peer[0], peer[1], len(data)))
            if len(data) >= 20 and data[0:2] == b"\x00\x01" and data[4:8] == _MAGIC:
                xport = struct.pack("!H", peer[1] ^ 0x2112)
                xaddr = bytes(a ^ b for a, b in zip(socket.inet_aton(peer[0]), _MAGIC))
                attr = struct.pack("!HH", 0x0020, 8) + b"\x00\x01" + xport + xaddr
                try:
                    self._udp.sendto(b"\x01\x01" + struct.pack("!H", len(attr)) + _MAGIC + data[8:20] + attr, peer)
                except OSError:
                    pass

    def _serve_tcp(self) -> None:
        while not self._closed:
            try:
                conn, peer = self._tcp.accept()
            except OSError:
                return
            with self._lock:
                self.tcp.append((peer[0], peer[1]))
            try:
                conn.close()
            except OSError:
                pass
