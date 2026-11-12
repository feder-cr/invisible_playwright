"""One ICE gather from a page, with the engine and the wrapper that are installed: no proxy, or behind a local SOCKS5 gateway that relays UDP. Prints ONE json line.

  python scripts/ice_gather_probe.py LABEL ENGINE_EXE [proxy|tcponly]

(proxy: behind a local SOCKS5 gateway whose UDP is coherent; tcponly: behind one that refuses UDP ASSOCIATE; none: no proxy)

The page gathers with no ICE server (host candidates only) and with the gateway's measurement STUN server when there is a gateway; the line says the candidates, the ICE errors and
the gathering state, so that "no candidate at all" can be told from "no srflx". The engine and the core may be the published ones (the line then does not pass `udp_stun_servers`, which
only the next engine takes). A diagnosis tool (a hunt), not a test.
"""
import inspect
import json
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "tests"))
sys.path.insert(0, os.path.join(here, "diag_plugin"))
import diag_browser_output  # noqa: E402,F401 - writes the browser output to $DIAG_OUT when it is set

label, exe = sys.argv[1], sys.argv[2]
kind = sys.argv[3] if len(sys.argv) > 3 else "none"
with_proxy = kind in ("proxy", "tcponly")

JS = """async (stun) => {
  const pc = new RTCPeerConnection(stun ? {iceServers: [{urls: [stun]}]} : {});
  pc.createDataChannel('');
  const cands = [], errors = [];
  pc.addEventListener('icecandidate', e => { if (e.candidate && e.candidate.candidate) cands.push(e.candidate.candidate); });
  pc.addEventListener('icecandidateerror', e => errors.push({code: e.errorCode, text: e.errorText}));
  const t0 = performance.now();
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(resolve => {
    const timer = setTimeout(resolve, 15000);
    pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') { clearTimeout(timer); resolve(); } });
  });
  const state = pc.iceGatheringState;
  try { pc.close(); } catch (e) {}
  return {candidates: cands, errors, state, ms: Math.round(performance.now() - t0)};
}"""

row = {"label": label, "proxy": kind, "engine": exe}
gateway = None
try:
    from invisible_core import _geo
    from invisible_playwright import InvisiblePlaywright

    row["core"] = __import__("importlib.metadata", fromlist=["version"]).version("invisible-core")
    kwargs = {}
    if with_proxy:
        from _host_address import this_host_address
        from _socks_servers import coherent, tcp_only
        host = this_host_address()
        gateway = (coherent if kind == "proxy" else tcp_only)(relay_host=host, exit_host=host)
        gateway.forward_only_to = {host}
        kwargs["proxy"] = gateway.proxy
        if "udp_stun_servers" in inspect.signature(InvisiblePlaywright.__init__).parameters:
            kwargs["udp_stun_servers"] = gateway.measure_servers
        exit_ip = "203.0.113.7"
    else:
        exit_ip = "203.0.113.7"
    _geo.discover_egress_ip = lambda *a, **k: exit_ip      # no echo service is reachable in a lab: the question is the gather
    with InvisiblePlaywright(seed=42, headless=True, binary_path=exe, **kwargs) as browser:
        page = browser.new_context().new_page()
        page.goto("about:blank", timeout=30000)
        stun = ("stun:%s:%d" % ("198.51.100.6", 19302)) if with_proxy else None
        row.update(page.evaluate(JS, stun))
        row["candidate_types"] = sorted({c.split(" typ ")[1].split()[0] for c in row["candidates"] if " typ " in c})
        row["gathered"] = bool(row["candidates"])
except Exception as e:      # a launch that failed is its own finding
    row["exception"] = str(e).splitlines()[0][:300]
finally:
    if gateway is not None:
        gateway.close()
print(json.dumps(row), flush=True)
