"""E2E: nothing a context's page makes the browser do arrives at a server except through the proxy of that context (the behavioural half of the
class gate; the static half is the ledger of birth points in the series).

The class. A page can make the browser open a socket, send a datagram or ask a name through code that was written for the browser, not for the
page: an ICE agent, an OCSP fetch, a DNS prefetch. Each such road picks its proxy and its resolver from the user context of its request, and a
request made with the default attributes or the system principal leaves OUTSIDE the proxy of the context it came from. Two were found one at a
time (the TCP socket of a TURN server's name, the OCSP request); this file holds the class, one probe per road.

The lab, with nothing but this host:

* the session has NO proxy (the default context is the machine's own network) and ONE context has a proxy of its own: the only proxy there is;
* a WITNESS (``_witness.Witness``) stands at an address of this host (``this_host_address``: no root, no address added to ``lo``, so the lab is
  the same on every host). No proxy of the test forwards to it: the SOCKS5 gateways refuse a CONNECT past loopback and relay no datagram, the HTTP
  proxy answers 403. So whatever the witness hears came from the browser, direct;
* every name a probe uses is ``<case>-<nonce>.test`` (or ``.local``) and the FakeDoh answers it with the witness's address: a name that is looked
  up by the DoH through the proxy leads to a CONNECT to the witness's address AT THE GATEWAY, listed there; a name looked up any other way is
  seen by the sink (``_egress_sink.py``): the engine's own log (``getaddrinfo`` calls, and the user context of each lookup) on every system, the
  resolver trap too where it runs (Linux, root);
* a probe is one mechanism of a page: ``fetch``, ``WebSocket``, ``stun:`` and ``turn:`` over UDP, ``turn:`` and ``turns:`` over TCP, each by NAME,
  by IP, and (``.local``) by a name the engine excludes from the DoH; behind SOCKS5 and behind an HTTP proxy where the road differs.

What each case asserts (``_egress_sink.EgressSink``): the witness heard nothing, the watcher saw no connection to it, no name of the probe was
looked up natively, none was looked up in user context 0, and the work WAS done (the DoH asked the name by the gateway of the context only, and
the gateway or the proxy was handed the target). The controls make the green mean something: the same probe by IP with NO proxy IS heard by the
witness, and a name looked up natively IS seen by the native sink.

Expected, case by case, on the published engine firefox-63 and on firefox-64 (the tree with the TURN-by-name fix): ``EXPECTED``, below. Nothing
here is skipped or marked as an expected failure: a case that is red on firefox-63 is red there, and says which road it is.

The Windows sink is ``_egress_sink.native_lookup_names``; it needs no change here.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Callable, Dict, Optional

import pytest

from invisible_playwright import InvisiblePlaywright

# The fixtures and the helpers of the DoH e2e (imported, not copied): the hermetic session (autouse) and the page in a context. `gateway`, `pages`,
# `trap` and `watcher` are in conftest.py.
from test_doh_through_the_proxy_e2e import (  # noqa: F401  (the hermetic session is autouse and imported by name)
    PREFS, ZONE, _hermetic_session, _is_address, _page_in, _why)

import _doh_servers as ds
from _doh_servers import QTYPE_A, FakeDoh
from _egress_sink import (LOG_FILE_NAME, MOZ_LOG, EgressSink, named_connects, native_lookup_names, new_nonce)
from _host_address import this_host_address
from _http_proxy import HttpProxy
from _witness import Witness

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

#: How long the page waits for the browser to try the target, in milliseconds (the ICE agent retransmits STUN and TURN at 0, 0.1, 0.3, 0.7 s...).
WAIT_MS = {"fetch": 2500, "websocket": 2500, "stun": 5000, "turn_udp": 5000, "turn_tcp": 5000, "turns_tcp": 5000}
#: How long to wait, in seconds, for the proxy or the DoH to have been handed what the probe asked for.
EVIDENCE_SECONDS = 20


@dataclasses.dataclass(frozen=True)
class Case:
    kind: str       # fetch | websocket | stun | turn_udp | turn_tcp | turns_tcp
    by: str         # name | ip | local
    proxy: str      # socks5 | http

    @property
    def id(self) -> str:
        return "%s-%s-%s" % (self.kind, self.by, self.proxy)

    @property
    def tcp(self) -> bool:
        return self.kind not in ("stun", "turn_udp")


CASES = [
    Case("fetch", "name", "socks5"), Case("fetch", "ip", "socks5"), Case("fetch", "local", "socks5"), Case("fetch", "local", "http"),
    Case("websocket", "name", "socks5"), Case("websocket", "ip", "socks5"),
    Case("stun", "name", "socks5"), Case("stun", "ip", "socks5"), Case("stun", "local", "socks5"), Case("stun", "local", "http"),
    Case("turn_udp", "name", "socks5"), Case("turn_udp", "ip", "socks5"),
    Case("turn_tcp", "name", "socks5"), Case("turn_tcp", "name", "http"), Case("turn_tcp", "ip", "socks5"), Case("turn_tcp", "ip", "http"),
    Case("turns_tcp", "name", "socks5"), Case("turns_tcp", "name", "http"), Case("turns_tcp", "ip", "socks5"),
]
BY_ID = {c.id: c for c in CASES}

#: What each case is expected to do, by engine. "green" is the contract; "RED" names the road that leaves the proxy. Held by
#: test_the_egress_sink_helpers.py (every id is a case, and a RED has its reason).
EXPECTED: Dict[str, Dict[str, str]] = {
    "firefox-63": {
        "turn_tcp-name-socks5": "RED: the name of the TURN server is looked up in user context 0 (WebrtcTCPSocket, native or the DoH of the wrong context)",
        "turns_tcp-name-socks5": "RED: same road as turn_tcp-name-socks5 (the TLS socket is made by the same code)",
        "fetch-local-socks5": "RED: *.local is excluded from the DoH (all.js network.trr.builtin-excluded-domains) and looked up natively (audit B4)",
        "stun-local-socks5": "RED: the ICE resolver asks the machine's resolver for *.local (audit B4)",
        "stun-local-http": "RED: same, the ICE resolver always resolves the name itself (audit B4)",
    },
    "firefox-64": {
        "fetch-local-socks5": "RED until B4 is decided: *.local goes to the machine's resolver",
        "stun-local-socks5": "RED until B4 is decided",
        "stun-local-http": "RED until B4 is decided",
    },
}

_FETCH_JS = """async ([host, port, waitMs]) => {
  fetch('http://' + host + ':' + port + '/f', {mode: 'no-cors'}).catch(() => {});
  await new Promise((resolve) => setTimeout(resolve, waitMs));
}"""

_WEBSOCKET_JS = """async ([host, port, waitMs]) => {
  try { const ws = new WebSocket('ws://' + host + ':' + port + '/s'); ws.onerror = () => {}; } catch (e) {}
  await new Promise((resolve) => setTimeout(resolve, waitMs));
}"""


def _ice_js(url_expression):
    return """async ([host, port, waitMs]) => {
  const pc = new RTCPeerConnection({iceServers: [{urls: [%s], username: 'u', credential: 'p'}]});
  pc.createDataChannel('d');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  pc.close();
}""" % url_expression


PROBE_JS = {
    "fetch": _FETCH_JS,
    "websocket": _WEBSOCKET_JS,
    "stun": _ice_js("'stun:' + host + ':' + port"),
    "turn_udp": _ice_js("'turn:' + host + ':' + port + '?transport=udp'"),
    "turn_tcp": _ice_js("'turn:' + host + ':' + port + '?transport=tcp'"),
    "turns_tcp": _ice_js("'turns:' + host + ':' + port + '?transport=tcp'"),
}


class Lab:
    """The witness, the nonce, and the names of the cases (every one answered with the witness's address)."""

    def __init__(self, witness: Witness, nonce: str):
        self.witness, self.nonce = witness, nonce

    def host_of(self, case: Case) -> str:
        if case.by == "ip":
            return self.witness.address
        return "%s-%s-%s.%s" % (case.kind, case.by, self.nonce, "local" if case.by == "local" else "test")

    @property
    def zone(self) -> Dict[str, str]:
        return {self.host_of(c): self.witness.address for c in CASES if c.by != "ip"}


# ------------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture
def lab():
    address = this_host_address()          # before any server starts: it skips, never leaves one listening
    witness = Witness(address)
    try:
        yield Lab(witness, new_nonce())
    finally:
        witness.close()


@pytest.fixture
def doh(lab):
    """The FakeDoh with the names of the cases (overrides the one of conftest.py)."""
    import h2  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip

    server = FakeDoh({**ZONE, **lab.zone})
    yield server
    server.close()


@pytest.fixture
def engine_log(tmp_path, monkeypatch):
    """The engine logs its resolver, one file per process, in a folder of the test (the environment reaches the browser through the launch)."""
    directory = tmp_path / "moz"
    directory.mkdir()
    monkeypatch.setenv("MOZ_LOG", MOZ_LOG)
    monkeypatch.setenv("MOZ_LOG_FILE", str(directory / LOG_FILE_NAME))
    return directory


def _launch(binary, proxy=None):
    """The session of the lab: NO proxy unless given (the default context is the machine's own network), the DoH provider of the FakeDoh."""
    return InvisiblePlaywright(seed=42, binary_path=binary, proxy=proxy, headless=True,
                               extra_prefs={**PREFS, "media.peerconnection.ice.obfuscate_host_addresses": True})


def _the_proxy_of(case: Case, doh, gateway):
    if case.proxy == "http":
        return HttpProxy("http1", doh)
    return gateway("gw-context")


def _was_handed(case: Case, proxy, host: str, port: int) -> bool:
    """The road was the proxy's: a SOCKS5 gateway lists every CONNECT it got (even the ones it refuses), the HTTP proxy every request."""
    if case.proxy == "http":
        return any(s.host == host and s.port == port for s in proxy.seen)
    # SOCKS5: the browser resolved the name and connects by ADDRESS.
    return (host, port) in proxy.connects


def _the_work_was_done(case: Case, lab: Lab, doh, proxy) -> Optional[Callable[[], bool]]:
    """What shows that the probe reached the road it was meant to try, or None when only the control can show it (a UDP probe by IP)."""
    host, port = lab.host_of(case), lab.witness.port
    if case.proxy == "http":
        if case.tcp:
            return lambda: _was_handed(case, proxy, host, port)
        return None
    if case.by == "ip":
        return (lambda: _was_handed(case, proxy, host, port)) if case.tcp else None
    if case.tcp:
        return lambda: (lab.witness.address, port) in proxy.connects
    return lambda: bool(doh.queries_for(host, QTYPE_A))


# ----------------------------------------------------------------------------------------------------------- tests
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_a_probe_of_a_context_leaves_only_through_its_proxy(case, firefox_binary, lab, doh, pages, gateway, trap, watcher, engine_log):
    proxy = _the_proxy_of(case, doh, gateway)
    host, port = lab.host_of(case), lab.witness.port
    with _launch(firefox_binary) as browser:
        context, page = _page_in(browser, proxy=proxy.proxy)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        page.evaluate(PROBE_JS[case.kind], [host, port, WAIT_MS[case.kind]])
        done = _the_work_was_done(case, lab, doh, proxy)
        reached = ds.wait_until(done, EVIDENCE_SECONDS) if done else None
        context.close()
    watcher.stop()

    sink = EgressSink(lab.witness, watcher, engine_log, lab.nonce, trap)
    why = _why(doh, proxy) + "\n  case %s: target %s:%d, witness at %s" % (case.id, host, port, lab.witness.address)
    assert sink.blind_problems() == [], "the sink cannot be trusted: %s" % sink.blind_problems() + why
    assert sink.leak_problems() == [], "; ".join(sink.leak_problems()) + why

    # The work was done, by the road it was meant to take.
    if reached is not None:
        assert reached, "the proxy was never handed %s:%d (the probe did not run, or it took another road)" % (host, port) + why
    if case.proxy == "socks5":
        named = named_connects(proxy.connects, _is_address, allowed_names=[ds.DOH_HOST])
        assert named == [], "the gateway was sent a NAME in a CONNECT: %s (the browser must resolve with the DoH and connect by address)" % named + why
        if case.by != "ip":
            assert doh.vias_of(host, QTYPE_A) == {proxy.name}, (
                "%s was not asked of the DoH by the gateway of the context only: %s" % (host, sorted(doh.vias_of(host, QTYPE_A))) + why)
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy" + why


@pytest.mark.parametrize("kind", ["fetch", "stun", "turn_tcp"])
def test_the_same_probe_with_no_proxy_is_heard_by_the_witness(kind, firefox_binary, lab, watcher, engine_log):
    """The control of the witness: with no proxy at all the probe by IP reaches it. Without this a silent witness proves nothing."""
    case = Case(kind, "ip", "socks5")
    with _launch(firefox_binary) as browser:
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.goto("about:blank")
        page.evaluate(PROBE_JS[kind], [lab.witness.address, lab.witness.port, WAIT_MS[kind]])
        reached = ds.wait_until(lambda: lab.witness.heard()["tcp" if case.tcp else "udp"], EVIDENCE_SECONDS)
    watcher.stop()
    assert reached, "no proxy, and the %s probe by IP did not reach the witness at %s: %s" % (kind, lab.witness.target, lab.witness.heard())


def test_the_native_sink_hears_a_native_lookup(firefox_binary, lab, trap, watcher, engine_log):
    """The control of the native sink: a context with NO proxy looks a name up with the machine's resolver (nothing answers, the page fails), and
    ``native_lookup_names`` says so. Without this an empty answer proves nothing, on Linux (the log, and the trap as root) and on Windows."""
    name = "native-control-%s.test" % lab.nonce
    with _launch(firefox_binary) as browser:
        page = browser.new_context().new_page()
        try:
            page.goto("http://%s:%d/" % (name, lab.witness.port), timeout=15000)
        except Exception:  # noqa: BLE001 - the name has no address: the lookup is what the test is about
            pass
        heard = ds.wait_until(lambda: native_lookup_names(engine_log, lab.nonce, trap), EVIDENCE_SECONDS)
    watcher.stop()
    assert heard and name in native_lookup_names(engine_log, lab.nonce, trap), (
        "a context with no proxy looked %s up natively and the sink did not hear it: %s; log files: %s"
        % (name, native_lookup_names(engine_log, lab.nonce, trap), sorted(os.listdir(str(engine_log)))))
