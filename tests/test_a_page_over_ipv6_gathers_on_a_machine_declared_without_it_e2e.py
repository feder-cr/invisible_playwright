"""A page reached over IPv6 still gathers host candidates on a machine declared without IPv6.

The default address is looked up for the family of the page's server. The real network may reach the page over IPv6 (a
site with an AAAA record, here a server on `[::1]`), while the declared machine has IPv4 only (`v6=0`): it has no default
address of that family, and the gathering found no address at all - it completed in a few milliseconds with zero
candidates, with no error, even without a proxy (measured on the firefox-63 candidate, Windows, direct).

The machine the persona declares answers with the family it has: one host name (UDP and TCP), ranked 127 (an IPv4-only
machine has one address), exactly as with the page served over IPv4. Hermetic: no proxy, no external network.
Red on an engine before webrtc/declared-interfaces ddaf867f03 (empty gathering).
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_webrtc_realness import _e2e_binary, _launch, candidates, host_candidates  # noqa: F401

pytestmark = pytest.mark.e2e

V4_ONLY = "v4=192.168.31.7;v6=0;p6="
UDP_127, TCP_127 = 2122252543, 2105524479

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


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True


class _Page(BaseHTTPRequestHandler):
    timeout = 5

    def do_GET(self):
        body = b"<!doctype html><title>over ipv6</title><p>page"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def page_over_ipv6():
    try:
        srv = _V6Server(("::1", 0), _Page)
    except OSError as exc:       # the environment has no IPv6 loopback: the test cannot mean anything, so it says so
        pytest.fail("no IPv6 loopback to serve the page on (%s): the test needs [::1]" % exc, pytrace=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://[::1]:%d/" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _gather(url, monkeypatch, declaration):
    binary = _e2e_binary()
    if not binary:
        pytest.fail("no patched binary: set STEALTHFOX_E2E_BINARY", pytrace=False)
    monkeypatch.setenv("STEALTHFOX_WEBRTC_NETWORK", declaration)
    with _launch(binary_path=binary, seed=42) as browser:
        page = browser.new_context().new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        assert page.evaluate("location.hostname") == "[::1]"
        return host_candidates(candidates(page.evaluate(_GATHER_JS)))


def test_a_page_served_over_ipv6_gets_the_host_candidates_of_an_ipv4_only_machine(page_over_ipv6, monkeypatch):
    hosts = _gather(page_over_ipv6, monkeypatch, V4_ONLY)
    assert hosts, "an empty gathering: the declared machine found no default address for the family of the page"
    by_proto = {proto: sorted((c["priority"] for c in hosts if c["proto"] == proto), reverse=True)
                for proto in ("UDP", "TCP")}
    assert by_proto == {"UDP": [UDP_127], "TCP": [TCP_127]}, hosts
    assert all(c["address"].endswith(".local") for c in hosts), "host candidates are mDNS names, never an address: %r" % hosts
