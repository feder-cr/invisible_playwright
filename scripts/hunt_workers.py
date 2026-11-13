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
KINDS = ["classic-url", "blob", "data", "module", "nested", "shared", "iframe-srcdoc", "webgpu", "page-gl", "storm"]

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
      else if (kind === "iframe-srcdoc") {
        // a hidden same-origin frame posts a message to the page from an inline script: which comes first, its load or its message
        var order = [], f = document.createElement("iframe");
        f.style.display = "none";
        window.addEventListener("message", function (e) { if (e.data === "hunt-frame") { order.push("message"); got.nav = {v: order.slice(), page_ms: ms()}; first = first === null ? ms() : first; check(); } });
        f.addEventListener("load", function () { order.push("load"); got.gl = {v: order.slice(), page_ms: ms()}; first = first === null ? ms() : first; check(); });
        function check() { if (got.nav && got.gl) { got.nav.v = got.gl.v = order.join(","); finish("both"); } }
        f.srcdoc = "<script>parent.postMessage('hunt-frame','*')<\\/script>";
        document.body.appendChild(f);
      }
      else if (kind === "webgpu") {
        if (!navigator.gpu) { got.nav = {v: "navigator.gpu absent", page_ms: ms()}; got.gl = got.nav; finish("absent"); }
        else navigator.gpu.requestAdapter().then(function (a) { got.nav = {v: a ? "adapter" : "null", page_ms: ms()}; got.gl = got.nav; first = ms(); finish("resolved"); },
                                                 function (e) { events.push("error:" + e); finish("rejected"); });
      }
    } catch (err) { events.push("throw:" + err); finish("throw"); }
  });
}

function heavyLoad() {
  var out = {};
  var t = performance.now();
  try {
    var c = document.createElement("canvas"); c.width = c.height = 256;
    var gl = c.getContext("webgl");
    out.gl = !!gl;
    if (gl) {
      var vs = gl.createShader(gl.VERTEX_SHADER); gl.shaderSource(vs, "attribute vec2 p;varying vec2 v;void main(){v=p*.5+.5;gl_Position=vec4(p,0,1);}"); gl.compileShader(vs);
      var taps = ""; for (var i = 0; i < 96; i++) taps += "a+=texture2D(t,v+vec2(" + (i * 0.001) + "," + (i * 0.0007) + "));";
      var fs = gl.createShader(gl.FRAGMENT_SHADER); gl.shaderSource(fs, "precision mediump float;varying vec2 v;uniform sampler2D t;void main(){vec4 a=vec4(0);" + taps + "gl_FragColor=a/96.;}"); gl.compileShader(fs);
      var pr = gl.createProgram(); gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr); gl.useProgram(pr);
      var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      var loc = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      var tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([9, 9, 9, 255]));
      var px = new Uint8Array(4);
      for (var k = 0; k < 6; k++) { gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); }
    }
  } catch (e) { out.gl_error = String(e); }
  out.gl_ms = Math.round(performance.now() - t); t = performance.now();
  var x = 1; for (var i = 0; i < 1600000; i++) x = Math.imul(x, 1664525) + 1013904223 | 0;
  out.cpu_ms = Math.round(performance.now() - t); out.x = x;
  return out;
}
function runExtra(kind, limit) {
  if (kind === "page-gl") {
    var r = {kind: kind, status: "ok", stages: {}, events: [], first_ms: 0};
    try {
      var c = document.createElement("canvas"), g = c.getContext("webgl");
      var e = g && g.getExtension("WEBGL_debug_renderer_info");
      r.stages.nav = {v: g ? {renderer: g.getParameter(e ? e.UNMASKED_RENDERER_WEBGL : g.RENDERER)} : {error: "canvas getContext null"}};
      var o = new OffscreenCanvas(1, 1), og = o.getContext("webgl");
      var oe = og && og.getExtension("WEBGL_debug_renderer_info");
      r.stages.gl = {v: og ? {renderer: og.getParameter(oe ? oe.UNMASKED_RENDERER_WEBGL : og.RENDERER)} : {error: "page OffscreenCanvas getContext null"}};
      if (!g || !og) r.status = "gl-error";
    } catch (err) { r.status = "error"; r.events.push("throw:" + err); }
    r.events.push("hasFocus=" + document.hasFocus() + " visibility=" + document.visibilityState);
    return Promise.resolve(r);
  }
  // storm: every worker kind, the hidden frame and WebGPU started together while the page also renders and computes, as a
  // benchmark page does; a probe is "slow" when it answers after 2500 ms
  return new Promise(function (resolve) {
    var t0 = performance.now(), res = {}, kinds = ["classic-url", "blob", "data", "module", "nested", "shared", "iframe-srcdoc", "webgpu"], left = kinds.length;
    var ms = function () { return Math.round(performance.now() - t0); };
    kinds.forEach(function (k) {
      runProbe(k, limit).then(function (r) {
        res[k] = {status: r.status, first_ms: r.first_ms, done_ms: ms()};
        if (--left === 0) {
          var bad = kinds.filter(function (q) { return res[q].status !== "ok"; });
          var slow = kinds.filter(function (q) { return res[q].status === "ok" && res[q].first_ms > 2500; });
          resolve({kind: kind, status: bad.length ? "pending" : (slow.length ? "slow" : "ok"), first_ms: Math.max.apply(null, kinds.map(function (q) { return res[q].first_ms || 0; })),
                   stages: {}, events: [JSON.stringify(res)], total_ms: ms()});
        }
      });
    });
    setTimeout(function () { res.__load = heavyLoad(); }, 0);
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
                out.update(page.evaluate("([k, l]) => (k === 'storm' || k === 'page-gl') ? runExtra(k, l) : runProbe(k, l)", [kind, 25000 if kind == "storm" else 10000]))
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


def self_signed_context():
    """An SSL context for 127.0.0.1 with a throwaway certificate (the browser is told to accept it)."""
    import datetime
    import ipaddress
    import ssl
    import tempfile
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "c.pem"), "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(os.path.join(d, "k.pem"), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(d, "c.pem"), os.path.join(d, "k.pem"))
    return ctx


def main():
    who, config = sys.argv[1], sys.argv[2]
    https = config.endswith("_https")
    config_name = config
    if https:
        config = config[:-len("_https")]
    config = config.rstrip("2")  # "direct2" is a second run of "direct", to tell a cold first launch from the rest
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    srv.daemon_threads = True
    if https:
        srv.socket = self_signed_context().wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "%s://127.0.0.1:%d/" % ("https" if https else "http", srv.server_address[1])

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
            ctx = b.new_context(ignore_https_errors=True)
            run_probes(ctx.new_page, base, seen, who, config_name)
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
                factory = (lambda: browser.new_context(ignore_https_errors=True).new_page()) if https else browser.new_page
                run_probes(factory, base, seen, who, config_name)
    if gw is not None:
        gw.close()
    srv.shutdown()


if __name__ == "__main__":
    if sys.argv[1] == "table":
        table(sys.argv[2:])
    else:
        main()
