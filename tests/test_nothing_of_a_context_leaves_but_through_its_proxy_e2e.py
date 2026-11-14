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

The window of a torn-down document (defect B3). A PeerConnection built from ``W.RTCPeerConnection``, where ``W`` is the window of an iframe that
was removed or of a popup that was closed, had NO proxy config on the engine firefox-63: its sockets were plain (direct UDP and TCP from the host)
and its ICE server names were resolved in the default context. ``WINDOW_CASES`` makes that window two ways (a detached iframe, a closed popup) behind
SOCKS5 and behind an HTTP proxy and builds the PeerConnection with a STUN and a TURN-over-TCP server named by name; ``CONTROL_CASES`` builds the
same PeerConnection from an ATTACHED iframe of the same page (proof that the lab sees the PeerConnection of a sub-window at all). A dead window may
refuse to build a PeerConnection, or build one that never gathers: that passes the egress assertions (nothing left) but is never hidden, the test
prints which one happened (``DEAD-WINDOW`` line) and records it as the property ``dead_window_outcome``. ``EXPECTED_WINDOW`` says what each case does
on firefox-63 (red: the direct UDP/TCP is heard by the witness, the names are looked up natively or in user context 0) and on firefox-64 (green).

Two contexts alive at once. Two contexts of ONE browser, each with its OWN proxy and its OWN FakeDoh (a different zone: the name of the other context is
unknown to it), each with a PeerConnection whose TURN server is given by a DIFFERENT name over TCP (the name of A at the port of witness A, the name of
B at the port of witness B, both witnesses at the address of this host: the PORT of a CONNECT says which context's TURN it was). Each name must reach
only the DoH of its own context through its own proxy, no name the machine's resolver, each TURN CONNECT only the proxy of its context, and nothing the
witnesses direct. Behind SOCKS5 it is RED on firefox-63 (the TURN names are resolved by the machine, as ``turn_tcp-name-socks5``) and green on
firefox-64; behind an HTTP proxy the name goes to the proxy in the CONNECT and the case is green on both (as ``turn_tcp-name-http``). ``EXPECTED_TWO``.

The Windows sink is ``_egress_sink.native_lookup_names``, the same function as on Linux: the engine's own log (the getaddrinfo lines are in the
parent process's file under the sandbox of both systems, run 43574494194), with ``test_the_native_sink_hears_a_native_lookup`` as its positive
control on both. The suite runs on Linux and on Windows.

The OCSP request of a trusted page. A page whose TLS certificate names an OCSP responder (Authority Information Access) makes the PARENT fetch that
URL, with the system principal. The lab holds a CA the engine trusts (``_lab_pki``: put in the profile by the engine's own xpcshell, then the session
is launched with that ``profile_dir``, no ``ignore_https_errors``, which would switch the check off) and two leaves: the responder named by a NAME
(``ocsp-<nonce>.lab.test``, answered by the FakeDoh of the context) and by an IP (an address of this host). A context behind the gateway loads the
page; the request must leave through the gateway (its CONNECT lists the responder, the name was asked of the DoH by that gateway) and never be
looked up natively, in user context 0, or arrive at the responder (which stands at an address no proxy forwards to). ``OCSP_CASES``; red on
firefox-63 (``EXPECTED_OCSP``: the request drops the attributes of its context and leaves in context 0), green on firefox-64. Controls: the same
page in a context with NO proxy IS asked of the responder (``POST application/ocsp-request``); and a page signed by an issuer the engine does NOT
trust makes no OCSP request at all (the chain fails first), so a silent responder in the cases above cannot mean "OCSP is off".
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
from _egress_sink import (LOG_FILE_NAME, MOZ_LOG, BlindSink, EgressSink, named_connects, native_lookup_names, new_nonce)
from _host_address import this_host_address
from _http_proxy import HttpProxy
from _lab_pki import OCSP_PREFS, LabCa, OcspResponder, TlsPage, add_ca_to_profile
from _witness import Witness

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

#: How long the page waits for the browser to try the target, in milliseconds (the ICE agent retransmits STUN and TURN at 0, 0.1, 0.3, 0.7 s...).
WAIT_MS = {"fetch": 2500, "websocket": 2500, "stun": 5000, "turn_udp": 5000, "turn_tcp": 5000, "turns_tcp": 5000}
#: How long to wait, in seconds, for the proxy or the DoH to have been handed what the probe asked for.
EVIDENCE_SECONDS = 20
#: The lab's own prefs, on top of the DoH provider of ``PREFS``. Every name of a probe is answered with the address of THIS host (the witness), and on
#: a LAN or a runner that is an RFC 1918 address: the engine's DoH client DROPS such an answer unless ``network.trr.allow-rfc1918`` is true
#: (TRR.cpp / DNSPacket.cpp: ``aAllowRFC1918 || !addr.IsIPAddrLocal()``), so the name would resolve to nothing and the probe would never connect
#: (the first run on a runner, 10.1.0.150: the DoH was asked, the gateway never got a CONNECT). The lab declares it; no egress assertion depends on it.
LAB_PREFS = {"network.trr.allow-rfc1918": True, "media.peerconnection.ice.obfuscate_host_addresses": True}


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

    @property
    def refused_by_the_browser(self) -> bool:
        """``turns:`` by an IP: the browser never tries it (nricectx.cpp, ``ToNicerTurnStruct``: "TLS requires FQDN, not IP address"), on every
        proxy and none: no connection is made to prove a road. The case keeps every egress assertion (witness, watcher, native lookup)."""
        return self.kind == "turns_tcp" and self.by == "ip"


CASES = [
    Case("fetch", "name", "socks5"), Case("fetch", "ip", "socks5"), Case("fetch", "local", "socks5"), Case("fetch", "local", "http"),
    Case("websocket", "name", "socks5"), Case("websocket", "ip", "socks5"),
    Case("stun", "name", "socks5"), Case("stun", "ip", "socks5"), Case("stun", "local", "socks5"), Case("stun", "local", "http"),
    Case("turn_udp", "name", "socks5"), Case("turn_udp", "ip", "socks5"),
    Case("turn_tcp", "name", "socks5"), Case("turn_tcp", "name", "http"), Case("turn_tcp", "ip", "socks5"), Case("turn_tcp", "ip", "http"),
    Case("turns_tcp", "name", "socks5"), Case("turns_tcp", "name", "http"), Case("turns_tcp", "ip", "socks5"),
]
BY_ID = {c.id: c for c in CASES}


@dataclasses.dataclass(frozen=True)
class WindowCase:
    window: str     # iframe (detached) | popup (closed) | attached (control: an iframe still in its page)
    proxy: str      # socks5 | http

    @property
    def id(self) -> str:
        return "%s-%s" % (self.window, self.proxy)

    @property
    def dead(self) -> bool:
        return self.window != "attached"


WINDOW_CASES = [WindowCase(w, p) for w in ("iframe", "popup") for p in ("socks5", "http")]
CONTROL_CASES = [WindowCase("attached", p) for p in ("socks5", "http")]
#: The two ICE servers of a window case, by name: the STUN server over UDP and the TURN server over TCP.
ICE_KINDS = ("stun", "turn")
#: How long the page waits for the gathering to end, in milliseconds (the witness answers nothing: the ICE agent retries until this ends).
GATHER_MS = 6000
#: How long the page waits for a closed popup to say ``closed``, in milliseconds.
CLOSED_MS = 5000

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
    # B4 is closed on this engine (the core declares ``network.trr.builtin-excluded-domains`` = localhost: a ``*.local`` name goes to the DoH through
    # the proxy): the three ``*-local`` probes are green (run 43488527832: the DoH is asked, no native lookup).
    "firefox-64": {},
}

#: The same for the window cases (``WindowCase.id``). On firefox-64 (fix of B3) every case is green, so it has no row. The controls are green everywhere.
EXPECTED_WINDOW: Dict[str, Dict[str, str]] = {
    "firefox-63": {c.id: "RED: the PeerConnection of a torn-down window has no proxy config: its UDP and TCP sockets are direct (the witness hears "
                         "them) and its ICE server names are looked up in the default context (B3)" for c in WINDOW_CASES},
    "firefox-64": {},
}

#: Two contexts alive at once, each with its own proxy and its own DoH, a TURN server by a different name in each (``test_two_contexts_...``).
EXPECTED_TWO: Dict[str, Dict[str, str]] = {
    "firefox-63": {"socks5": "RED: the TURN name is looked up in user context 0 (same road as turn_tcp-name-socks5): the machine's resolver is asked, no DoH "
                             "of either context hears it, and no CONNECT of the TURN reaches either gateway"},
    "firefox-64": {},
}

#: The OCSP responder of the leaf, by a NAME (``ocsp-<nonce>.lab.test``) or by an IP (an address of this host).
OCSP_CASES = ("name", "ip")
#: What each OCSP case does on firefox-63 (red: the request is made with the system principal and drops the attributes of its context, so it takes
#: the default context's road: the name is looked up in user context 0 and the connection is direct) and on firefox-64 (green). Like the other
#: tables, the gate does not skip or xfail: a case that is red on firefox-63 is red there and says which road it is.
EXPECTED_OCSP: Dict[str, Dict[str, str]] = {
    "firefox-63": {
        "name": "RED: the OCSP request of a trusted page is made with the system principal and loses the attributes of its context: the name of "
                "the responder is looked up natively, in user context 0, outside the proxy of the page's context",
        "ip": "RED: the OCSP request of a trusted page is made with the system principal and loses the attributes of its context: the address "
              "of the responder is connected to directly, outside the proxy of the page's context",
    },
    "firefox-64": {},
}
#: How long the negative control waits for an OCSP request that must not come, in seconds (the soft-fail timeout of the engine is 2 s).
OCSP_SETTLE_SECONDS = 4

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


#: A PeerConnection with a TURN server over TCP by name that STAYS OPEN (two contexts must each hold one at the same time): it returns when the offer is set.
_TURN_KEPT_JS = """async ([host, port]) => {
  const pc = new RTCPeerConnection({iceServers: [{urls: ['turn:' + host + ':' + port + '?transport=tcp'], username: 'u', credential: 'p'}]});
  window.__pc = pc;
  pc.createDataChannel('d');
  await pc.setLocalDescription(await pc.createOffer());
}"""

#: Step 1 of the popup variant: a button whose handler opens the popup and closes it at once. It is CLICKED (a real click holds the user gesture; a
#: ``window.open`` from an ``evaluate`` has none and the popup blocker answers null, which would test the blocker).
_POPUP_BUTTON_JS = """() => {
  const b = document.createElement('button');
  b.id = 'open-dead'; b.textContent = 'open';
  b.onclick = () => { window.__popup = window.open('about:blank'); if (window.__popup) window.__popup.close(); };
  document.body.append(b);
}"""

#: Steps 1 and 2 of a window case: the window (an iframe that is removed, a popup that was closed, or an iframe that stays), the PeerConnection
#: built from ITS ``RTCPeerConnection`` with a STUN and a TURN-over-TCP server by name, the offer, and the wait for the end of the gathering. It
#: returns what happened, so the test can say it: ``made`` (the window exists), ``closed`` (it is dead), ``constructed`` (the constructor did not
#: throw), ``error``, ``state`` (``iceGatheringState`` at the end), ``started`` (the gathering ran).
_WINDOW_ICE_JS = """async ([how, stunHost, turnHost, port, gatherMs, closedMs]) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const out = {window: how, made: false, closed: null, constructed: false, error: null, state: null, started: false};
  let W = null;
  if (how === 'popup') {
    W = window.__popup || null;
    out.made = !!W;
    const t0 = Date.now();
    while (W && !W.closed && Date.now() - t0 < closedMs) await sleep(50);
    out.closed = !!W && W.closed === true;
  } else {
    const f = document.createElement('iframe');
    document.body.append(f);
    W = f.contentWindow;
    out.made = !!W;
    if (how === 'iframe') { f.remove(); out.closed = f.contentWindow === null; }
  }
  if (!out.made || (how !== 'attached' && !out.closed)) return out;
  let pc = null;
  try {
    pc = new W.RTCPeerConnection({iceServers: [
      {urls: 'stun:' + stunHost + ':' + port},
      {urls: 'turn:' + turnHost + ':' + port + '?transport=tcp', username: 'u', credential: 'p'}]});
    out.constructed = true;
    pc.createDataChannel('x');
    await pc.setLocalDescription(await pc.createOffer());
  } catch (e) { out.error = String(e && e.name ? e.name + ': ' + e.message : e); }
  if (pc) {
    try {
      const t0 = Date.now();
      while (pc.iceGatheringState !== 'complete' && Date.now() - t0 < gatherMs) await sleep(100);
      out.state = pc.iceGatheringState;
      out.started = out.state !== 'new';
      pc.close();
    } catch (e) { out.error = (out.error ? out.error + '; ' : '') + String(e && e.name ? e.name + ': ' + e.message : e); }
  }
  return out;
}"""


class Lab:
    """The witness, the nonce, and the names of the cases (every one answered with the witness's address)."""

    def __init__(self, witness: Witness, nonce: str):
        self.witness, self.nonce = witness, nonce

    def host_of(self, case: Case) -> str:
        if case.by == "ip":
            return self.witness.address
        return "%s-%s-%s.%s" % (case.kind, case.by, self.nonce, "local" if case.by == "local" else "test")

    @property
    def ocsp_name(self) -> str:
        """The name the leaf of the OCSP case by NAME gives its responder (answered with the address of this host by the FakeDoh of the case)."""
        return "ocsp-%s.lab.test" % self.nonce

    def tls_name(self, by: str) -> str:
        """The name of the TLS page of an OCSP case (answered with the loopback, where the page's server stands)."""
        return "tls-%s-%s.lab.test" % (by, self.nonce)

    def ice_host(self, case: WindowCase, kind: str) -> str:
        """The name of the STUN (``kind="stun"``) or TURN server of a window case."""
        return "%s-%s-%s.test" % (kind, case.window, self.nonce)

    @property
    def zone(self) -> Dict[str, str]:
        names = {self.host_of(c): self.witness.address for c in CASES if c.by != "ip"}
        names.update({self.ice_host(c, k): self.witness.address for c in WINDOW_CASES + CONTROL_CASES for k in ICE_KINDS})
        return names


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
                               extra_prefs={**PREFS, **LAB_PREFS})


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
    """What shows that the probe reached the road it was meant to try, or None when only the control can show it (a UDP probe by IP), or when
    the browser refuses the probe itself (``Case.refused_by_the_browser``)."""
    host, port = lab.host_of(case), lab.witness.port
    if case.refused_by_the_browser:
        return None
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


def _native_or_nothing(directory, nonce, trap):
    """The native lookups so far, or nothing while the log has not started yet (a poll must not die of it; the assertion after it still raises)."""
    try:
        return native_lookup_names(directory, nonce, trap)
    except BlindSink:
        return []


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
        heard = ds.wait_until(lambda: _native_or_nothing(engine_log, lab.nonce, trap), EVIDENCE_SECONDS)
    watcher.stop()
    assert heard and name in native_lookup_names(engine_log, lab.nonce, trap), (
        "a context with no proxy looked %s up natively and the sink did not hear it: %s; log files: %s"
        % (name, _native_or_nothing(engine_log, lab.nonce, trap), sorted(os.listdir(str(engine_log)))))


# ------------------------------------------------------------------------------------- the window of a torn-down document (B3)
def _run_window_case(case: WindowCase, firefox_binary, lab: Lab, doh, pages, gateway, watcher):
    """The page of a proxied context makes the window, builds the PeerConnection from it and waits. Returns (proxy, outcome, reached), where
    ``reached`` is None when no road shows that the probe ran (a window that did not gather)."""
    proxy = HttpProxy("http1", doh) if case.proxy == "http" else gateway("gw-context")
    stun_host, turn_host, port = lab.ice_host(case, "stun"), lab.ice_host(case, "turn"), lab.witness.port
    reached = None
    with _launch(firefox_binary) as browser:
        context, page = _page_in(browser, proxy=proxy.proxy)
        page.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
        if case.window == "popup":
            page.evaluate(_POPUP_BUTTON_JS)
            page.click("#open-dead", timeout=10000)
        outcome = page.evaluate(_WINDOW_ICE_JS, [case.window, stun_host, turn_host, port, GATHER_MS, CLOSED_MS])
        if outcome["started"]:
            def done() -> bool:
                # the TURN server over TCP was handed to the proxy, and the STUN name was asked of the DoH (SOCKS5: by the gateway of the context)
                if case.proxy == "http":
                    return _was_handed(case, proxy, turn_host, port)
                return (lab.witness.address, port) in proxy.connects and bool(doh.queries_for(stun_host, QTYPE_A))
            reached = ds.wait_until(done, EVIDENCE_SECONDS)
        context.close()
    watcher.stop()
    return proxy, outcome, reached


def _what_the_window_did(outcome: dict) -> str:
    """The sentence that says which of the three things happened in the window (never hidden: printed, recorded, and part of every message)."""
    if outcome["started"]:
        return "gathered (iceGatheringState=%s)" % outcome["state"]
    if not outcome["constructed"]:
        return "constructor-threw (%s)" % outcome["error"]
    return "no-gathering (constructed, iceGatheringState=%s, error=%s)" % (outcome["state"], outcome["error"])


def _check_window_case(case: WindowCase, outcome: dict, proxy, reached, lab: Lab, doh, trap, watcher, engine_log, record_property):
    stun_host, turn_host, port = lab.ice_host(case, "stun"), lab.ice_host(case, "turn"), lab.witness.port
    said = _what_the_window_did(outcome)
    print("DEAD-WINDOW %s: %s" % (case.id, said))
    record_property("dead_window_outcome", said)
    why = (_why(doh, proxy) + "\n  case %s: STUN %s, TURN %s, port %d, witness at %s\n  the window: %s"
           % (case.id, stun_host, turn_host, port, lab.witness.address, said))
    assert outcome["made"], "the lab could not make the window (a popup blocked, or an iframe with no window): %s" % outcome + why
    if case.dead:
        assert outcome["closed"], "the window is not dead (the popup never said closed, or the iframe still has a window): %s" % outcome + why
    sink = EgressSink(lab.witness, watcher, engine_log, lab.nonce, trap)
    assert sink.blind_problems() == [], "the sink cannot be trusted: %s" % sink.blind_problems() + why
    assert sink.leak_problems() == [], "; ".join(sink.leak_problems()) + why
    if not case.dead:
        # The control: the PeerConnection of a sub-window that is still attached gathers, by the road of the context (the sibling cases' green).
        assert outcome["started"], "the control PeerConnection of an attached iframe never gathered: the lab does not see a sub-window: %s" % outcome + why
    if outcome["started"]:
        assert reached, "the proxy was never handed %s:%d, or the DoH never asked %s (the work did not take the road of the context)" % (turn_host, port, stun_host) + why
        if case.proxy == "socks5":
            named = named_connects(proxy.connects, _is_address, allowed_names=[ds.DOH_HOST])
            assert named == [], "the gateway was sent a NAME in a CONNECT: %s (the browser must resolve with the DoH and connect by address)" % named + why
            for host in (stun_host, turn_host):
                assert doh.vias_of(host, QTYPE_A) == {proxy.name}, (
                    "%s was not asked of the DoH by the gateway of the context only: %s" % (host, sorted(doh.vias_of(host, QTYPE_A))) + why)
    assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy" + why


@pytest.mark.parametrize("case", WINDOW_CASES, ids=[c.id for c in WINDOW_CASES])
def test_a_peerconnection_of_a_torn_down_window_leaves_only_through_the_proxy(
        case, firefox_binary, lab, doh, pages, gateway, trap, watcher, engine_log, record_property):
    """B3. RED on firefox-63 (the PeerConnection of a torn-down window has no proxy config: direct sockets, names looked up in the default
    context), GREEN on firefox-64. A window that refuses the PeerConnection, or one that never gathers, passes the egress assertions and says so."""
    proxy, outcome, reached = _run_window_case(case, firefox_binary, lab, doh, pages, gateway, watcher)
    _check_window_case(case, outcome, proxy, reached, lab, doh, trap, watcher, engine_log, record_property)


@pytest.mark.parametrize("case", CONTROL_CASES, ids=[c.id for c in CONTROL_CASES])
def test_the_same_peerconnection_of_an_attached_iframe_leaves_only_through_the_proxy(
        case, firefox_binary, lab, doh, pages, gateway, trap, watcher, engine_log, record_property):
    """The control of the test above: the same PeerConnection built from an iframe that is still in its page gathers and goes through the proxy of
    the context (green on firefox-63 and on firefox-64): the lab sees the PeerConnection of a sub-window."""
    proxy, outcome, reached = _run_window_case(case, firefox_binary, lab, doh, pages, gateway, watcher)
    _check_window_case(case, outcome, proxy, reached, lab, doh, trap, watcher, engine_log, record_property)


# ------------------------------------------------------------------------------------- two contexts alive at once, a TURN name each
@pytest.mark.parametrize("scheme", ["socks5", "http"])
def test_two_contexts_each_resolve_their_turn_name_through_their_own_proxy(
        scheme, firefox_binary, lab, doh, pages, gateway, trap, watcher, engine_log):
    """Context A (proxy A, DoH A, TURN ``turn-ctx-a-<nonce>.test`` at witness A's port) and context B (proxy B, DoH B, TURN ``turn-ctx-b-...`` at
    witness B's port), alive together in one browser, each with a PeerConnection kept open. Each name is asked only of the DoH of its own context
    through its own proxy (SOCKS5), each TURN CONNECT goes through the proxy of its context and never through the other, no name reaches the
    machine's resolver, nothing reaches either witness direct. RED on firefox-63 behind SOCKS5, green on firefox-64 (see ``EXPECTED_TWO``)."""
    import h2  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip

    address = lab.witness.address
    witness_b = Witness(address)               # the same address, another port: the port of a CONNECT says which context it came from
    try:
        name_a, name_b = ("turn-ctx-%s-%s.test" % (tag, lab.nonce) for tag in ("a", "b"))
        port_a, port_b = lab.witness.port, witness_b.port
        doh.names[name_a] = address            # DoH A knows its own name only ...
        doh_b = FakeDoh({**ZONE, name_b: address})   # ... and DoH B its own (and its own certificate)
        try:
            if scheme == "http":
                proxy_a, proxy_b = HttpProxy("http-a", doh), HttpProxy("http-b", doh_b)
            else:
                from _doh_servers import DohGateway
                proxy_a, proxy_b = gateway("gw-a"), DohGateway("gw-b", doh_b)
            try:
                def handed(proxy, name, port):
                    if scheme == "http":
                        return any(x.host == name and x.port == port for x in proxy.seen)
                    return (address, port) in proxy.connects

                with _launch(firefox_binary) as browser:
                    context_a, page_a = _page_in(browser, proxy=proxy_a.proxy)
                    context_b, page_b = _page_in(browser, proxy=proxy_b.proxy)
                    page_a.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
                    page_b.goto(pages.url("page-a.test"), wait_until="load", timeout=30000)
                    page_a.evaluate(_TURN_KEPT_JS, [name_a, port_a])
                    page_b.evaluate(_TURN_KEPT_JS, [name_b, port_b])       # both contexts, and both PeerConnections, alive now
                    reached_a = ds.wait_until(lambda: handed(proxy_a, name_a, port_a), EVIDENCE_SECONDS)
                    reached_b = ds.wait_until(lambda: handed(proxy_b, name_b, port_b), EVIDENCE_SECONDS)
                    context_a.close()
                    context_b.close()
                watcher.stop()

                sink = EgressSink(lab.witness, watcher, engine_log, lab.nonce, trap)      # the witness of A; the watcher and the log see both names
                why = (_why(doh, proxy_a) + "\n  --- context B ---" + _why(doh_b, proxy_b)
                       + "\n  scheme %s: A is %s:%d, B is %s:%d, witnesses at %s" % (scheme, name_a, port_a, name_b, port_b, address))
                assert sink.blind_problems() == [], "the sink cannot be trusted: %s" % sink.blind_problems() + why
                assert sink.leak_problems() == [], "; ".join(sink.leak_problems()) + why
                assert witness_b.heard() == {"tcp": [], "udp": []}, "the witness of context B was reached direct: %s" % witness_b.heard() + why

                # each TURN went through the proxy of its context, and only that one
                assert reached_a, "the proxy of context A was never handed the TURN %s:%d" % (name_a, port_a) + why
                assert reached_b, "the proxy of context B was never handed the TURN %s:%d" % (name_b, port_b) + why
                assert not handed(proxy_b, name_a, port_a), "the TURN of context A went through the proxy of context B" + why
                assert not handed(proxy_a, name_b, port_b), "the TURN of context B went through the proxy of context A" + why
                if scheme == "socks5":
                    for proxy in (proxy_a, proxy_b):
                        named = named_connects(proxy.connects, _is_address, allowed_names=[ds.DOH_HOST])
                        assert named == [], "%s was sent a NAME in a CONNECT: %s (resolve with the DoH, connect by address)" % (proxy.name, named) + why
                    # each name was asked of the DoH of its own context, by the gateway of that context, and of no other
                    assert doh.vias_of(name_a, QTYPE_A) == {proxy_a.name}, "%s: %s" % (name_a, sorted(doh.vias_of(name_a, QTYPE_A))) + why
                    assert doh_b.vias_of(name_b, QTYPE_A) == {proxy_b.name}, "%s: %s" % (name_b, sorted(doh_b.vias_of(name_b, QTYPE_A))) + why
                    assert not doh.queries_for(name_b), "the name of context B was asked of the DoH of context A" + why
                    assert not doh_b.queries_for(name_a), "the name of context A was asked of the DoH of context B" + why
                for server in (doh, doh_b):
                    assert "direct" not in {q.via for q in server.queries}, "something reached a DoH without the proxy" + why
            finally:
                proxy_b.close()                # the proxy of A is closed by the `gateway` fixture (SOCKS5) or here (HTTP)
                if scheme == "http":
                    proxy_a.close()
        finally:
            doh_b.close()
    finally:
        witness_b.close()


# ------------------------------------------------------------------------------------- the OCSP request of a trusted page
@pytest.fixture
def ocsp_responder(lab):
    """What the OCSP URL of a leaf points at, at the address of this host: a request that arrives is a direct one (no proxy of the tests forwards)."""
    responder = OcspResponder(lab.witness.address)
    yield responder
    responder.close()


def _launch_trusting(binary, profile):
    """A persistent session on ``profile`` (the lab CA is in its NSS database), with NO proxy: its default context is the machine's own network
    and ``context.browser.new_context(proxy=...)`` makes the contexts that have one. No ``ignore_https_errors`` anywhere."""
    return InvisiblePlaywright(seed=42, binary_path=binary, headless=True, profile_dir=profile,
                               extra_prefs={**PREFS, **LAB_PREFS, **OCSP_PREFS})


def _trusting_profile(binary, tmp_path, ca: LabCa):
    profile = tmp_path / "profile"
    for line in add_ca_to_profile(binary, profile, ca.ca_b64, tmp_path / "xpcshell"):
        print("LAB-CA %s" % line)
    return profile


@pytest.mark.parametrize("by", OCSP_CASES, ids=list(OCSP_CASES))
def test_the_ocsp_request_of_a_trusted_page_leaves_only_through_the_proxy(
        by, firefox_binary, lab, doh, gateway, trap, watcher, engine_log, ocsp_responder, tmp_path):
    """The page is served over TLS with a leaf the engine trusts and whose AIA names the OCSP responder ``by`` a name or an IP; a context behind the
    gateway loads it. RED on firefox-63 (``EXPECTED_OCSP``), green on firefox-64."""
    address = lab.witness.address
    tls_name, ocsp_name = lab.tls_name(by), lab.ocsp_name
    ocsp_url = "http://%s:%d/" % (ocsp_name if by == "name" else address, ocsp_responder.port)
    ca = LabCa(tmp_path)
    leaf, key = ca.leaf(by, [tls_name], ["127.0.0.1"], ocsp_url)
    server = TlsPage(leaf, key, "egress-ocsp-ok")
    try:
        doh.names[tls_name] = "127.0.0.1"
        doh.names[ocsp_name] = address
        proxy = gateway("gw-context")
        profile = _trusting_profile(firefox_binary, tmp_path, ca)
        url = "https://%s:%d/" % (tls_name, server.port)
        detail = "not loaded"
        with _launch_trusting(firefox_binary, profile) as session:
            browser = session.browser
            assert browser is not None, "the persistent session has no Browser to make a context with a proxy of its own (context.browser is None)"
            context = browser.new_context(proxy=proxy.proxy)
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="load", timeout=30000)
                detail = "status=%s text=%r" % (response.status if response else None, page.evaluate("() => document.body.innerText")[:40])
            except Exception as exc:  # noqa: BLE001 - the message of the failed assertion below carries it
                detail = "goto error: %s" % str(exc).splitlines()[0][:200]
            reached = ds.wait_until(lambda: (address, ocsp_responder.port) in proxy.connects, EVIDENCE_SECONDS)
            context.close()
        watcher.stop()

        sink = EgressSink(lab.witness, watcher, engine_log, lab.nonce, trap)
        why = (_why(doh, proxy) + "\n  case ocsp-%s: page %s (%s), OCSP URL %s, responder at %s, handshakes %s"
               % (by, url, detail, ocsp_url, address, server.handshakes[:4]))
        assert "egress-ocsp-ok" in detail, "the page did not load trusted (the lab CA is not trusted by the engine, or the page is unreachable)" + why
        assert sink.blind_problems() == [], "the sink cannot be trusted: %s" % sink.blind_problems() + why
        assert ocsp_responder.requests == [], "the OCSP responder was reached direct, outside the gateway: %s" % ocsp_responder.requests + why
        assert sink.leak_problems() == [], "; ".join(sink.leak_problems()) + why
        # The work was done, by the road of the context: the revocation check WAS asked, and the gateway was handed the responder.
        assert reached, ("the gateway was never handed the OCSP responder %s:%d (no revocation check was asked, or it took another road)"
                         % (address, ocsp_responder.port)) + why
        named = named_connects(proxy.connects, _is_address, allowed_names=[ds.DOH_HOST])
        assert named == [], "the gateway was sent a NAME in a CONNECT: %s (the browser must resolve with the DoH and connect by address)" % named + why
        if by == "name":
            assert doh.vias_of(ocsp_name, QTYPE_A) == {proxy.name}, (
                "%s was not asked of the DoH by the gateway of the context only: %s" % (ocsp_name, sorted(doh.vias_of(ocsp_name, QTYPE_A)))) + why
        assert "direct" not in {q.via for q in doh.queries}, "something reached the DoH without the proxy" + why
    finally:
        server.close()


def _control_page(ca: LabCa, ocsp_responder, tag: str):
    """A page over TLS at 127.0.0.1 signed by ``ca``, whose leaf names ``ocsp_responder`` by its address."""
    leaf, key = ca.leaf(tag, [], ["127.0.0.1"], ocsp_responder.url_by_address())
    return TlsPage(leaf, key, "egress-ocsp-ok")


def test_the_same_trusted_page_with_no_proxy_is_asked_of_the_ocsp_responder(firefox_binary, lab, ocsp_responder, watcher, engine_log, tmp_path):
    """The control of the cases above: the default context has NO proxy, and the same kind of page (a leaf of the trusted CA naming the responder by
    its address) makes the browser POST an OCSP request to it. Without this a silent responder proves nothing: OCSP could be off."""
    ca = LabCa(tmp_path)
    server = _control_page(ca, ocsp_responder, "control")
    try:
        profile = _trusting_profile(firefox_binary, tmp_path, ca)
        url = "https://127.0.0.1:%d/" % server.port
        with _launch_trusting(firefox_binary, profile) as session:
            page = session.pages[0] if session.pages else session.new_page()
            response = page.goto(url, wait_until="load", timeout=30000)
            text = page.evaluate("() => document.body.innerText")
            asked = ds.wait_until(lambda: ocsp_responder.asked, EVIDENCE_SECONDS)
        watcher.stop()
        assert response is not None and response.status == 200 and "egress-ocsp-ok" in text, (
            "the page did not load trusted: status=%s text=%r handshakes=%s" % (response.status if response else None, text[:40], server.handshakes[:4]))
        assert asked, "no proxy, a trusted page, and the responder at %s was never asked: %s" % (ocsp_responder.url_by_address(), ocsp_responder.requests)
        assert ocsp_responder.asked[0].content_type == "application/ocsp-request", ocsp_responder.requests
    finally:
        server.close()


def test_a_page_of_an_issuer_the_engine_does_not_trust_makes_no_ocsp_request(firefox_binary, lab, ocsp_responder, watcher, engine_log, tmp_path):
    """The negative control: the profile trusts one CA and the page is signed by another, so the chain fails before the revocation check and the
    responder is never asked, with NO proxy and the responder in plain reach. The OCSP cases hold only because the lab CA is trusted."""
    trusted, stranger = LabCa(tmp_path, "lab trusted CA"), LabCa(tmp_path, "lab stranger CA")
    server = _control_page(stranger, ocsp_responder, "stranger")
    try:
        profile = _trusting_profile(firefox_binary, tmp_path, trusted)
        url = "https://127.0.0.1:%d/" % server.port
        refused = None
        with _launch_trusting(firefox_binary, profile) as session:
            page = session.pages[0] if session.pages else session.new_page()
            try:
                page.goto(url, wait_until="load", timeout=30000)
            except Exception as exc:  # noqa: BLE001 - the certificate error is the expected outcome
                refused = str(exc).splitlines()[0][:200]
            ds.wait_until(lambda: False, OCSP_SETTLE_SECONDS)
        watcher.stop()
        assert refused, "a page signed by an issuer the engine does not trust loaded: the lab cannot tell trusted from untrusted"
        assert server.handshakes, "the browser never reached the page's server: the negative control proved nothing (refused: %s)" % refused
        assert ocsp_responder.requests == [], "an untrusted issuer made an OCSP request: %s" % ocsp_responder.requests
    finally:
        server.close()
