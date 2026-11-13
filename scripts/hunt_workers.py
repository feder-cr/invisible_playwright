"""HUNT SCAFFOLDING, NEVER MERGED: which kind of Web Worker answers, ours against stock, direct and behind a proxy.

Usage: python scripts/hunt_workers.py ours|stock CONFIG [BINARY]   (CONFIG: direct | socks_auth | socks | http)
       python scripts/hunt_workers.py table LOGFILE...

A local page starts one probe per worker kind, each in a page of its own with a 10 s limit: a classic worker from a same-origin URL,
from a blob: URL, from a data: URL, a module worker, a nested worker (a blob worker started by a blob worker) and a SharedWorker.
Each worker posts two messages: "nav" (hardwareConcurrency, language(s), Intl timezone, userAgent, platform) and "gl" (an
OffscreenCanvas WebGL context with the UNMASKED vendor and renderer), with the time since the probe started. The client side records
the page console, the page errors and the worker events the driver sees (page.on("worker")). One JSON line per probe: HUNT-WORKER.
"""
import http.server
import json
import os
import socket
import sys
import threading
import time

ROUNDS = 5
KINDS = ["classic-url", "blob", "data", "module", "nested", "shared"]

BODY = r"""
var __t0 = performance.now();
function __run(post) {
  var at = function () { return Math.round(performance.now() - __t0); };
  post({stage: "nav", at: at(), v: {hc: navigator.hardwareConcurrency, lang: navigator.language,
        langs: (navigator.languages || []).join(","), tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
        ua: navigator.userAgent, platform: navigator.platform}});
  try {
    var c = new OffscreenCanvas(1, 1);
    var gl = c.getContext("webgl");
    if (!gl) { post({stage: "gl", at: at(), v: {error: "getContext returned null"}}); return; }
    var e = gl.getExtension("WEBGL_debug_renderer_info");
    post({stage: "gl", at: at(), v: {vendor: gl.getParameter(e ? e.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
          renderer: gl.getParameter(e ? e.UNMASKED_RENDERER_WEBGL : gl.RENDERER)}});
  } catch (err) { post({stage: "gl", at: at(), v: {error: String(err)}}); }
}
"""
DEDICATED = BODY + "\n__run(function (m) { postMessage(m); });\n"
SHARED = BODY + "\nonconnect = function (ev) { var p = ev.ports[0]; __run(function (m) { p.postMessage(m); }); };\n"
NESTED = ("var INNER = " + json.dumps(DEDICATED) + ";\n"
          "var w = new Worker(URL.createObjectURL(new Blob([INNER], {type: 'text/javascript'})));\n"
          "w.onmessage = function (e) { postMessage(e.data); };\n"
          "w.onerror = function (e) { postMessage({stage: 'nested-error', at: 0, v: {error: String(e.message)}}); };\n")

PAGE = ("""<!doctype html><html><head><meta charset="utf-8"><title>worker hunt</title></head><body><script>
var DEDICATED = %s, SHARED = %s, NESTED = %s;
function runProbe(kind, limit) {
  return new Promise(function (resolve) {
    var t0 = performance.now(), got = {}, events = [], first = null, done = false, w = null, timer = null;
    var ms = function () { return Math.round(performance.now() - t0); };
    function finish(why) {
      if (done) return; done = true; clearTimeout(timer);
      try { if (w && w.terminate) w.terminate(); else if (w && w.port) w.port.close(); } catch (e) {}
      var status;
      if (got.nav && got.gl) status = got.gl.v.error ? "gl-error" : "ok";
      else if (got.nav) status = "pending-gl";
      else if (events.some(function (x) { return x.indexOf("error") === 0 || x.indexOf("throw") === 0; })) status = "error";
      else status = "pending";
      resolve({kind: kind, status: status, why: why, first_ms: first, stages: got, events: events, total_ms: ms()});
    }
    function onmsg(e) {
      var d = e.data || {}; if (first === null) first = ms();
      got[d.stage] = {v: d.v, page_ms: ms(), worker_ms: d.at};
      if (got.nav && got.gl) finish("both");
    }
    function wire(x, port) {
      x.onerror = function (e) { events.push("error:" + e.message + " @" + e.filename + ":" + e.lineno); if (e.preventDefault) e.preventDefault(); finish("onerror"); };
      port.onmessage = onmsg; port.onmessageerror = function () { events.push("messageerror"); };
    }
    timer = setTimeout(function () { finish("limit"); }, limit);
    try {
      if (kind === "classic-url") { w = new Worker("/w.js"); wire(w, w); }
      else if (kind === "blob") { w = new Worker(URL.createObjectURL(new Blob([DEDICATED], {type: "text/javascript"}))); wire(w, w); }
      else if (kind === "data") { w = new Worker("data:text/javascript," + encodeURIComponent(DEDICATED)); wire(w, w); }
      else if (kind === "module") { w = new Worker("/m.mjs", {type: "module"}); wire(w, w); }
      else if (kind === "nested") { w = new Worker(URL.createObjectURL(new Blob([NESTED], {type: "text/javascript"}))); wire(w, w); }
      else if (kind === "shared") { w = new SharedWorker("/s.js?" + Math.random()); wire(w, w.port); w.port.start(); }
    } catch (err) { events.push("throw:" + err); finish("throw"); }
  });
}
</script></body></html>
""" % (json.dumps(DEDICATED), json.dumps(SHARED), json.dumps(NESTED))).encode("utf-8")

ROUTES = {"/": (PAGE, "text/html; charset=utf-8"),
          "/w.js": (DEDICATED.encode(), "text/javascript"),
          "/m.mjs": (DEDICATED.encode(), "text/javascript"),
          "/s.js": (SHARED.encode(), "text/javascript")}


class Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        body, ctype = ROUTES.get(path, (b"not found", "text/plain"))
        self.send_response(200 if path in ROUTES else 404)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class HttpPipe:
    """A minimal HTTP proxy on 127.0.0.1 that forwards for real: CONNECT as a tunnel, a plain absolute-URI GET as a request."""

    def __init__(self):
        self.connects = []
        self._l = socket.socket()
        self._l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._l.bind(("127.0.0.1", 0))
        self._l.listen(32)
        self.port = self._l.getsockname()[1]
        self.proxy = {"server": "http://127.0.0.1:%d" % self.port}
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        try:
            self._l.close()
        except OSError:
            pass

    def _accept(self):
        while True:
            try:
                c, _ = self._l.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    @staticmethod
    def _pipe(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        finally:
            try:
                b.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _handle(self, c):
        try:
            c.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head:
                d = c.recv(65536)
                if not d:
                    return
                head += d
            first, _, rest = head.partition(b"\r\n")
            method, target, _ver = first.decode("latin-1").split(" ", 2)
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                self.connects.append((host, int(port)))
                up = socket.create_connection((host, int(port)), timeout=10)
                c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                rest_url = target.split("://", 1)[1]
                hostport, _, path = rest_url.partition("/")
                host, _, port = hostport.partition(":")
                self.connects.append((host, int(port or 80)))
                up = socket.create_connection((host, int(port or 80)), timeout=10)
                up.sendall(("%s /%s HTTP/1.1\r\n" % (method, path)).encode("latin-1") + rest.replace(b"Proxy-Connection", b"X-Pc"))
            c.settimeout(None)
            threading.Thread(target=self._pipe, args=(c, up), daemon=True).start()
            self._pipe(up, c)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass


def run_probes(page_factory, base, proxy_seen, who, config):
    """One fresh page per probe; prints one HUNT-WORKER line each."""
    page_port = int(base.rsplit(":", 1)[1].rstrip("/"))
    for rnd in range(1, ROUNDS + 1):
        for kind in KINDS:
            console, errors, workers = [], [], []
            before = len(proxy_seen())
            t = time.time()
            page = page_factory()
            page.on("console", lambda m: console.append("%s: %s" % (m.type, m.text[:200])))
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            page.on("worker", lambda w: workers.append(w.url[:80]))
            out = {"engine": who, "config": config, "round": rnd, "kind": kind}
            try:
                page.goto(base, wait_until="load", timeout=30000)
                out.update(page.evaluate("([k, l]) => runProbe(k, l)", [kind, 10000]))
            except Exception as e:  # noqa: BLE001
                out.update({"status": "driver-error", "why": "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:200])})
            out.update({"console": console[:8], "page_errors": errors[:5], "driver_workers": workers,
                        "proxy_connects": len([x for x in proxy_seen()[before:] if x[1] == page_port]),
                        "wall_s": round(time.time() - t, 1)})
            print("HUNT-WORKER " + json.dumps(out), flush=True)
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass


def table(files):
    rows = {}
    for f in files:
        for line in open(f, encoding="utf-8", errors="replace"):
            if line.startswith("HUNT-WORKER "):
                r = json.loads(line[len("HUNT-WORKER "):])
                rows.setdefault((r["kind"], r["engine"], r["config"]), []).append(r)
    configs = sorted({(k[1], k[2]) for k in rows})
    print("%-12s | %s" % ("probe", " | ".join("%s/%s" % c for c in configs)))
    for kind in KINDS:
        cells = []
        for (eng, cfg) in configs:
            cnt = {}
            for r in rows.get((kind, eng, cfg), []):
                cnt[r["status"]] = cnt.get(r["status"], 0) + 1
            cells.append(",".join("%s=%d" % kv for kv in sorted(cnt.items())) or "-")
        print("%-12s | %s" % (kind, " | ".join(cells)))
    print()
    for (eng, cfg) in configs:
        for kind in KINDS:
            rs = rows.get((kind, eng, cfg), [])
            if not rs:
                continue
            r = next((x for x in rs if x["status"] == "ok"), rs[0])
            st = r.get("stages", {})
            nav, gl = st.get("nav"), st.get("gl")
            firsts = sorted(x.get("first_ms") for x in rs if x.get("first_ms") is not None)
            print("%s/%s %s: first msg ms %s | nav=%s | gl=%s | events=%s | driver_workers=%d console=%s errs=%s proxy_connects=%s" % (
                eng, cfg, kind, firsts, json.dumps(nav["v"]) if nav else None, json.dumps(gl["v"]) if gl else None,
                r.get("events"), len(r.get("driver_workers", [])), (r.get("console") or [])[:2], (r.get("page_errors") or [])[:1],
                [x.get("proxy_connects") for x in rs]))


def main():
    who, config = sys.argv[1], sys.argv[2]
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/" % srv.server_address[1]

    gw, seen, proxy = None, (lambda: []), None
    if config in ("socks_auth", "socks"):
        from _socks_servers import coherent, tcp_only
        if who == "stock":  # stock Firefox cannot authenticate to a SOCKS5 proxy: no-auth gateway only
            assert config == "socks"
        gw = tcp_only(user="hunt_user", password="hunt_pw") if config == "socks_auth" else coherent()
        proxy = gw.proxy
        seen = lambda: list(gw.connects)  # noqa: E731
    elif config == "http":
        gw = HttpPipe()
        proxy = gw.proxy
        seen = lambda: list(gw.connects)  # noqa: E731
    prefs = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": ""}

    if who == "stock":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            kw = {"headless": True, "firefox_user_prefs": dict(prefs)}
            if proxy:
                kw["proxy"] = {"server": proxy["server"]}
            b = p.firefox.launch(**kw)
            ctx = b.new_context()
            run_probes(ctx.new_page, base, seen, who, config)
            b.close()
    else:
        from unittest import mock

        from invisible_core import SessionGeo, decide_session_locale
        from invisible_playwright import InvisiblePlaywright
        geo = SessionGeo("America/New_York", "203.0.113.7", latitude=40.7128, longitude=-74.0060,
                         locale=decide_session_locale("en-US"))
        kw = {"seed": 7, "binary_path": sys.argv[3], "headless": True, "extra_prefs": dict(prefs)}
        if proxy:
            kw["proxy"] = proxy
        if gw is not None and hasattr(gw, "measure_servers"):
            kw["udp_stun_servers"] = gw.measure_servers
        with mock.patch("invisible_playwright.launcher.prepare_session_geo", lambda tz, px, loc, **_k: geo), \
                mock.patch.object(InvisiblePlaywright, "_assert_uscita_invariata", lambda self: None), \
                mock.patch("invisible_core._geo.discover_egress_ip", lambda *_a, **_k: geo.egress_ip):
            with InvisiblePlaywright(**kw) as browser:
                run_probes(browser.new_page, base, seen, who, config)
    if gw is not None:
        gw.close()
    srv.shutdown()


if __name__ == "__main__":
    if sys.argv[1] == "table":
        table(sys.argv[2:])
    else:
        main()
