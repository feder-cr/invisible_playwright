r"""WebRTC gather on retail Firefox (Windows), local page, two public STUN servers by name, no TURN: every candidate with type and
protocol, every icecandidateerror with its code and time, the time the gather ended. Run once with UDP free and once with UDP dropped.

Ported from udp_blocked_timeline.py of the Linux reference (private study repo, captures/timeline/retail-159-udp-blocked,
target "local-stun"). The page (local_stun_page.html), the in-page recorder, the per-session files and their fields are
unchanged. Adapted for Windows and for this job:
  - the Linux script also ran a third-party test page and read its recorder and summarize() out of another script; only the
    local-stun target is kept, so the recorder (INIT_JS below) is the RTCPeerConnection part of that script, copied as is. The
    parts that hooked setInterval and fetch for the third-party page are dropped, and so is its summarize();
  - the local page is served by a thread of this process on 127.0.0.1 (an OS-chosen port), not by `python -m http.server :8000`;
  - Firefox and geckodriver come from --firefox / --geckodriver (or FIREFOX_BIN / GECKODRIVER_BIN); the version is read from
    application.ini (no --version on Windows); libxul.so becomes xul.dll; no xvfb (the browser runs with -headless, as before);
  - gathering_end_since_pc_created_ms is added to timeline.json and to the summary rows;
  - the code is behind main(), so the test suite can import the helpers.

Output under --out/<label> (label "webrtc" in the workflow):
  local-stun/run<N>/timeline.json  events, candidates, icecandidateerrors, gathering states, gathering end, final lines
  local-stun/run<N>/page.txt       visible text at the end of the wait
  local-stun/run<N>/meta.json      per session: start time, leftover processes after quit
  summary_runs.json                one row per session
  meta.json                        top level: versions, sha256, command
"""
import argparse, configparser, datetime, functools, hashlib, http.server, ipaddress, json, os, platform, re, sys, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE_NAME = "local_stun_page.html"

# The RTCPeerConnection recorder of the Linux reference, unchanged. performance.now() timestamps; pc_created is time zero.
INIT_JS = r"""
(() => {
  if (window.top !== window) return;
  const tl = [];
  Object.defineProperty(window, '__pa_tl', {value: tl, enumerable: false, configurable: true});
  const rec = (ev, data) => { tl.push(Object.assign({ev: ev, t: performance.now(), wall: Date.now()}, data || {})); };
  rec('init', {});
  document.addEventListener('DOMContentLoaded', () => rec('DOMContentLoaded', {}));
  window.addEventListener('load', () => rec('load', {}));

  const RTC = window.RTCPeerConnection;
  if (RTC) {
    const Wrapped = function (cfg, ...rest) {
      const pc = new RTC(cfg, ...rest);
      const urls = [];
      try { (cfg && cfg.iceServers || []).forEach(s => [].concat(s.urls).forEach(u => urls.push(u))); } catch (e) {}
      rec('pc_created', {iceUrls: urls});
      pc.addEventListener('icecandidate', e => {
        const c = e.candidate;
        if (!c) { rec('gathering_end', {}); return; }
        const parts = (c.candidate || '').split(' ');
        rec('icecandidate', {type: c.type, protocol: c.protocol, address: c.address, port: c.port,
          relayProtocol: c.relayProtocol, url: c.url, foundation: parts[0], line: parts.slice(0, 8).join(' ')});
      });
      pc.addEventListener('icecandidateerror', e => rec('icecandidateerror',
        {url: e.url, code: e.errorCode, text: e.errorText, address: e.address}));
      pc.addEventListener('icegatheringstatechange', () => rec('gathering_state', {state: pc.iceGatheringState}));
      return pc;
    };
    Wrapped.prototype = RTC.prototype;
    window.RTCPeerConnection = Wrapped;
  }
})();
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def firefox_version(firefox):
    ini = configparser.ConfigParser()
    ini.read(Path(firefox).parent / "application.ini", encoding="utf-8")
    return "Mozilla Firefox %s" % ini.get("App", "Version", fallback="?")


def address_kind(addr):
    if not addr:
        return "none"
    if addr.endswith(".local"):
        return "mDNS"
    try:
        return "IPv6" if ipaddress.ip_address(addr).version == 6 else "IPv4"
    except ValueError:
        return "other"


def candidate_table(events):
    t0 = next((e["t"] for e in events if e["ev"] == "pc_created"), 0)
    rows = []
    for e in events:
        if e["ev"] != "icecandidate":
            continue
        parts = e.get("line", "").split()
        rows.append({
            "t_ms": round(e["t"], 1), "since_pc_created_ms": round(e["t"] - t0, 1),
            "type": e.get("type"), "protocol": e.get("protocol"), "address_kind": address_kind(e.get("address")),
            "address": e.get("address"), "port": e.get("port"), "relayProtocol": e.get("relayProtocol"),
            "url": e.get("url"), "foundation": e.get("foundation"),
            "component": parts[1] if len(parts) > 1 else None, "priority": parts[3] if len(parts) > 3 else None,
        })
    errors = [{"t_ms": round(e["t"], 1), "since_pc_created_ms": round(e["t"] - t0, 1), "url": e.get("url"),
               "code": e.get("code"), "text": e.get("text")}
              for e in events if e["ev"] == "icecandidateerror"]
    states = [{"t_ms": round(e["t"], 1), "state": e.get("state")} for e in events if e["ev"] == "gathering_state"]
    end = next((round(e["t"], 1) for e in events if e["ev"] == "gathering_end"), None)
    return rows, errors, states, end


def serve_page_dir():
    """Serve this folder on 127.0.0.1 from a thread; returns (server, base_url)."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(HERE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def firefox_count():
    import psutil
    return sum(1 for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("firefox"))


def session(url, run_dir, wait, label, firefox, geckodriver):
    import psutil
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    opts = Options()
    opts.binary_location = firefox
    opts.add_argument("-headless")
    opts.enable_bidi = True
    started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    driver = webdriver.Firefox(options=opts, service=Service(executable_path=geckodriver))
    gecko = driver.service.process.pid
    result, events, body = {}, [], ""
    try:
        driver.set_page_load_timeout(90)
        driver.script.add_preload_script("() => {" + INIT_JS + "}")
        try:
            driver.get(url)
        except Exception as e:
            result["page_error"] = "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:200])
        time.sleep(wait)
        events = driver.execute_script("return (window.__pa_tl || []).slice()") or []
        final_t = driver.execute_script("return performance.now()")
        try:
            body = driver.execute_script("return document.body ? document.body.innerText : ''")
        except Exception as e:
            result["text_error"] = type(e).__name__
        events.append({"ev": "final_read", "t": final_t, "wall": int(time.time() * 1000)})
    finally:
        try:
            kids = [c.pid for c in psutil.Process(gecko).children(recursive=True)]
        except psutil.NoSuchProcess:
            kids = []
        try:
            driver.quit()
        except Exception:
            pass
        left = []
        for pid in kids:
            try:
                p = psutil.Process(pid)
                left.append(p.name())
                p.kill()
            except psutil.NoSuchProcess:
                pass
        time.sleep(1)
    after = firefox_count()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "page.txt").write_text(body, encoding="utf-8")
    status_lines = [ln.strip() for ln in body.splitlines()
                    if re.search(r"TIMEOUT|blocking|Leak|manipulated|timeout|VPN|firewall|detected|gathering|candidate|error", ln, re.I)]
    rows, errors, states, end = candidate_table(events)
    timeline = {"events": events, "candidates": rows, "icecandidateerrors": errors,
                "gathering_states": states, "gathering_end_t_ms": end, "final_status_lines": status_lines[:12]}
    pc_t = next((e["t"] for e in events if e["ev"] == "pc_created"), None)
    timeline["gathering_end_since_pc_created_ms"] = round(end - pc_t, 1) if end is not None and pc_t is not None else None
    timeline.update(result)
    (run_dir / "timeline.json").write_text(json.dumps(timeline, indent=1, ensure_ascii=False), encoding="utf-8")
    (run_dir / "meta.json").write_text(json.dumps({
        "target": label, "date_started": started, "wait_s": wait,
        "gecko_children_killed_after_quit": left, "firefox_processes_after_quit": after,
    }, indent=1), encoding="utf-8")
    return timeline, after


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--wait", type=int, default=60)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--firefox", default=os.environ.get("FIREFOX_BIN"))
    ap.add_argument("--geckodriver", default=os.environ.get("GECKODRIVER_BIN"))
    a = ap.parse_args(argv)
    if not a.firefox or not a.geckodriver:
        raise SystemExit("set --firefox/--geckodriver or FIREFOX_BIN/GECKODRIVER_BIN")
    import subprocess, selenium
    out = Path(a.out) / a.label
    out.mkdir(parents=True, exist_ok=True)
    gd_ver = subprocess.run([a.geckodriver, "--version"], capture_output=True, text=True, timeout=60).stdout.splitlines()[0]
    started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    srv, base = serve_page_dir()
    summary_rows, leftover_total = [], 0
    try:
        for run in range(1, a.runs + 1):
            tl, after = session(base + "/" + PAGE_NAME, out / "local-stun" / ("run%d" % run), a.wait, "local-stun",
                                a.firefox, a.geckodriver)
            leftover_total += after
            cands = tl["candidates"]
            row = {"target": "local-stun", "run": run,
                   "candidates": len(cands),
                   "host": sum(1 for c in cands if c["type"] == "host"),
                   "srflx": sum(1 for c in cands if c["type"] == "srflx"),
                   "relay": sum(1 for c in cands if c["type"] == "relay"),
                   "errors": len(tl["icecandidateerrors"]),
                   "first_error_ms_after_pc": tl["icecandidateerrors"][0]["since_pc_created_ms"] if tl["icecandidateerrors"] else None,
                   "gathering_end_ms": tl["gathering_end_t_ms"],
                   "gathering_end_since_pc_created_ms": tl["gathering_end_since_pc_created_ms"],
                   "status_lines": tl["final_status_lines"][:4], "page_error": tl.get("page_error")}
            summary_rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        srv.shutdown()
    (out / "summary_runs.json").write_text(json.dumps(summary_rows, indent=1, ensure_ascii=False), encoding="utf-8")
    fx_dir = Path(a.firefox).parent
    (out / "meta.json").write_text(json.dumps({
        "binary": "retail Firefox Windows, WebRTC gather",
        "label": a.label,
        "firefox_version": firefox_version(a.firefox),
        "firefox_sha256": sha256(a.firefox),
        "libxul_sha256": sha256(fx_dir / "xul.dll"),
        "geckodriver": gd_ver,
        "geckodriver_sha256": sha256(a.geckodriver),
        "selenium": selenium.__version__,
        "local": PAGE_NAME + " served from 127.0.0.1 by this process",
        "local_ice_servers": ["stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478"],
        "runs": a.runs, "wait_s": a.wait, "targets": ["local-stun"],
        "date_started": started,
        "machine": platform.platform(),
        "udp_block": os.environ.get("UDP_BLOCK", "?"),
        "proxy": "none: Firefox prefs at default, no proxy",
        "command": " ".join(["python"] + sys.argv),
        "firefox_processes_left_after_sessions": leftover_total,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print("done", json.dumps({"out": str(out), "firefox_left_total": leftover_total}), flush=True)


if __name__ == "__main__":
    main()
