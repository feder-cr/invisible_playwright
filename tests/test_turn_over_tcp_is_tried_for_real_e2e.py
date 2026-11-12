"""A TURN server reached over TCP is really tried, as a retail Firefox tries it.

The connection's local address is the DECLARED one (the interface list is declared, never read from the host), which is not an
address of this machine. The TCP socket that talks to a TURN server was bound to it, the bind failed, the socket closed before it
connected, and the page heard `icecandidateerror` 701 within milliseconds, with the gathering complete, without a single packet
having left (measured on the firefox-63 candidate, direct and behind a SOCKS5 gateway: the server saw no connection at all).

A retail Firefox connects, sends its Allocate request, and hears 701 when the server closes: here the stand-in server accepts,
reads, and closes the connection N milliseconds after it accepted it. So:

* the server sees a connection and the request (the network was tried, direct and through the gateway);
* the page hears 701 no sooner than N milliseconds after the accept (it waited for the server, it did not give up on its own);
* the gathering does not complete before that error.

Hermetic: the server listens on this machine's address (a loopback TURN server is refused by Firefox itself, retail too), the page is
local, the gateway relays TCP only. Red on an engine before webrtc/declared-network "WebrtcTCPSocket binds the wildcard".
"""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from _socks_servers import Socks5TcpOnly
from test_webrtc_realness import _e2e_binary, _launch  # noqa: F401

pytestmark = pytest.mark.e2e

DECLARED = "v4=192.168.31.7;v6=0;p6="
N_MS = 800
WAIT_MS = 3000
SLACK_MS = 150


def _host_ip():
    """The address this machine uses to leave: nothing is sent, the kernel only chooses the route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    except OSError as exc:
        pytest.fail("this machine has no address but loopback (%s): the test needs one for the TURN stand-in" % exc, pytrace=False)
    finally:
        s.close()
    if ip.startswith("127."):
        pytest.fail("the only address of this machine is loopback: the test needs one for the TURN stand-in", pytrace=False)
    return ip


class _TurnTcp:
    """Accepts TCP connections, reads what comes, closes N ms after the accept."""

    def __init__(self, n_ms):
        self.n_ms, self.accepts, self.bytes_in = n_ms, [], 0
        self._s = socket.socket()
        self._s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._s.bind(("0.0.0.0", 0))
        self._s.listen(16)
        self.port = self._s.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._s.accept()
            except OSError:
                return
            self.accepts.append(time.time() * 1000)
            threading.Thread(target=self._hold, args=(conn,), daemon=True).start()

    def _hold(self, conn):
        end = time.time() + self.n_ms / 1000
        conn.settimeout(0.05)
        while time.time() < end:
            try:
                data = conn.recv(4096)
                if not data:
                    break
                self.bytes_in += len(data)
            except socket.timeout:
                pass
            except OSError:
                break
        try:
            conn.close()
        except OSError:
            pass

    def close(self):
        self._s.close()


_JS = """async ([url, waitMs]) => {
  const ev = [];
  const pc = new RTCPeerConnection({iceServers: [{urls: url, username: 'u', credential: 'c'}]});
  pc.addEventListener('icecandidateerror', e => ev.push(['error', Date.now(), e.errorCode]));
  pc.addEventListener('icegatheringstatechange', () => ev.push(['gathering', Date.now(), pc.iceGatheringState]));
  pc.createDataChannel('x');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => setTimeout(r, waitMs));
  pc.close();
  return ev;
}"""


@pytest.fixture
def local_page():
    class H(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>turn</body></html>")

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % httpd.server_address[1]
    httpd.shutdown()


@pytest.mark.parametrize("through_socks5", [False, True], ids=["direct", "socks5"])
def test_a_turn_server_over_tcp_is_connected_to_and_waited_for(local_page, monkeypatch, through_socks5):
    binary = _e2e_binary()
    if not binary:
        pytest.fail("no patched binary: set STEALTHFOX_E2E_BINARY", pytrace=False)
    monkeypatch.setenv("STEALTHFOX_WEBRTC_NETWORK", DECLARED)
    turn = _TurnTcp(N_MS)
    socks = Socks5TcpOnly() if through_socks5 else None
    kw = {}
    if socks:
        monkeypatch.setenv("STEALTHFOX_WEBRTC_PUBLIC_IP", "203.0.113.7")
        kw = {"proxy": {"server": "socks5://127.0.0.1:%d" % socks.port},
              "extra_prefs": {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": "",
                              "media.peerconnection.ice.obfuscate_host_addresses": True}}
    try:
        with _launch(binary_path=binary, seed=7, **kw) as browser:
            page = browser.new_context().new_page()
            page.goto(local_page, wait_until="domcontentloaded", timeout=60000)
            events = page.evaluate(_JS, ["turn:%s:%d?transport=tcp" % (_host_ip(), turn.port), WAIT_MS])
    finally:
        turn.close()
        if socks:
            socks.close()

    assert turn.accepts, "the TURN server never saw a connection: the network was not tried (events: %r)" % events
    assert turn.bytes_in > 0, "the connection carried nothing: no Allocate request was sent"
    errors = [e for e in events if e[0] == "error"]
    assert errors and errors[0][2] == 701, "expected the 701 of a server that closed: %r" % events
    first_accept = turn.accepts[0]
    assert errors[0][1] >= first_accept + N_MS - SLACK_MS, \
        "701 came %d ms after the accept, the server held the connection for %d ms: %r" % (
            errors[0][1] - first_accept, N_MS, events)
    complete = [e for e in events if e[0] == "gathering" and e[2] == "complete"]
    assert not complete or complete[0][1] >= errors[0][1] - 50, "the gathering completed before the TURN error: %r" % events
