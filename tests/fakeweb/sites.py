"""Offline test sites that recreate what ad-heavy pages do to the network.

One process serves two origins on 127.0.0.1 (page origin "A" and "the other
origin" O). Every response is finite (about 3 s at most), so a correct browser
must reach networkidle on every page.

Usage:
    python sites.py --ports 8311 8312
    from sites import start; origin_a, origin_b, stop = start((8311, 8312))
"""
import argparse
import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

MAX_DELAY_MS = 3000
# Operations per burst in cancel_among_beacons (env FAKEWEB_BURST overrides).
BURST_OPS = int(os.environ.get("FAKEWEB_BURST", "40"))

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#888"/></svg>'
WOFF2 = b"wOF2" + b"\x00" * 60  # not a real font; the point is the request, not the glyphs

# Shared prelude for every page: A and O, plus a helper to run code after load.
PRELUDE = """
const afterLoad = (ms, f) => {
  const go = () => setTimeout(f, ms);
  if (document.readyState === 'complete') go(); else addEventListener('load', go);
};
const noop = () => {};
// Read the body so Chromium also finishes the request (an unread body can keep it pending).
const get = (u, o) => fetch(u, o).then((r) => r.text()).catch(noop);
"""

# name -> (description, js). The js runs with A (page origin) and O (other origin).
PATTERNS = {
    "quiet": ("nothing at all (the canary)", ""),
    "beacon_pair": (
        "two sendBeacon calls to O about 1 ms apart, 700 ms after load",
        """
afterLoad(700, () => {
  navigator.sendBeacon(O + '/beacon', 'a=1');
  setTimeout(() => navigator.sendBeacon(O + '/beacon', 'b=2'), 1);
});
""",
    ),
    "keepalive_fetch": (
        "fetch with keepalive and no-cors to O",
        """
fetch(O + '/r?kind=json&id=ka1', {keepalive: true, mode: 'no-cors'}).catch(noop);
afterLoad(200, () => fetch(O + '/r?kind=json&id=ka2', {keepalive: true, mode: 'no-cors'}).catch(noop));
""",
    ),
    "tracking_pixel": (
        "four new Image() pixels to O",
        """
for (let i = 0; i < 4; i++) { const im = new Image(); im.src = O + '/r?kind=png&id=px' + i; }
""",
    ),
    "dup_font": (
        "fontcss with 5 @font-face rules sharing one woff2 URL, injected 250 ms after load",
        """
afterLoad(250, () => {
  const l = document.createElement('link');
  l.rel = 'stylesheet'; l.href = O + '/r?kind=fontcss&id=fc';
  document.head.appendChild(l);
  for (const w of [400, 500, 600, 700, 800]) {
    const s = document.createElement('span');
    s.textContent = 'weight ' + w + ' ';
    s.style.cssText = 'font-family: FakeFont, serif; font-weight: ' + w;
    document.body.appendChild(s);
  }
});
""",
    ),
    "removed_iframe": (
        "iframe from O that starts 6 slow fetches and 6 slow images, removed after 150 ms",
        """
const f = document.createElement('iframe');
f.src = O + '/f/slow';
document.body.appendChild(f);
setTimeout(() => f.remove(), 150);
""",
    ),
    "cancelled_requests": (
        "aborted fetch, replaced img src, removed script element, all at 100 ms",
        """
const ac = new AbortController();
get(A + '/r?kind=json&delay=2000&id=c1', {signal: ac.signal});
setTimeout(() => ac.abort(), 100);
const im = new Image();
im.src = O + '/r?kind=png&delay=2000&id=c2';
document.body.appendChild(im);
setTimeout(() => { im.src = A + '/r?kind=png&id=c3'; }, 100);
const sc = document.createElement('script');
sc.src = O + '/r?kind=js&delay=2000&id=c4';
document.head.appendChild(sc);
setTimeout(() => sc.remove(), 100);
""",
    ),
    "cancel_burst": (
        "20 slow fetches aborted after 0-30 ms, 5 iframes added and removed at once",
        """
for (let i = 0; i < 20; i++) {
  const c = new AbortController();
  const base = i % 2 ? O : A;
  get(base + '/r?kind=json&delay=2000&id=cb' + i, {signal: c.signal});
  setTimeout(() => c.abort(), Math.round(i * 1.5));
}
const frames = [];
for (let i = 0; i < 5; i++) {
  const f = document.createElement('iframe');
  f.src = O + '/f/slow?n=' + i;
  document.body.appendChild(f);
  frames.push(f);
}
setTimeout(() => frames.forEach((f) => f.remove()), 0);
""",
    ),
    "redirect_chain": (
        "script and fetch behind 2-3 cross-origin 302 hops",
        """
const rs = document.createElement('script');
rs.src = A + '/r?kind=js&redirect=3&id=rc1';
document.head.appendChild(rs);
get(O + '/r?kind=json&redirect=2&id=rc2');
""",
    ),
    "late_requests": (
        "a fetch every 300 ms, 7 times",
        """
for (let i = 1; i <= 7; i++) {
  setTimeout(() => get(A + '/r?kind=json&id=late' + i), 300 * i);
}
""",
    ),
    "long_poll": (
        "one fetch that takes 2.5 s",
        """
get(A + '/r?kind=json&delay=2500&id=lp');
""",
    ),
    "ad_iframes": (
        "3 iframes from O, each with a beacon, a pixel, a delayed script and a nested iframe",
        """
for (let i = 0; i < 3; i++) {
  const f = document.createElement('iframe');
  f.src = O + '/f/ad?n=' + i + '&a=' + encodeURIComponent(A);
  document.body.appendChild(f);
}
""",
    ),
    "late_dom_resources": (
        "img, svg image and stylesheet inserted 600 ms after load",
        """
afterLoad(600, () => {
  const im = document.createElement('img');
  im.src = O + '/r?kind=svg&id=ld1';
  document.body.appendChild(im);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const image = document.createElementNS(ns, 'image');
  image.setAttribute('href', O + '/r?kind=png&id=ld2');
  svg.appendChild(image);
  document.body.appendChild(svg);
  const l = document.createElement('link');
  l.rel = 'stylesheet'; l.href = A + '/r?kind=css&delay=300&id=ld3';
  document.head.appendChild(l);
});
""",
    ),
    "unload_beacon": (
        "sendBeacon in a pagehide listener",
        """
addEventListener('pagehide', () => navigator.sendBeacon(O + '/beacon', 'bye'));
""",
    ),
    "leaving_beacons": (
        "beacons and keepalive POSTs fired from pagehide, visibilitychange(hidden), beforeunload and unload",
        """
const leave = (tag) => {
  const q = '?t=' + tag;
  navigator.sendBeacon(O + '/beacon' + q + '&n=1', 'a=1');
  navigator.sendBeacon(O + '/beacon' + q + '&n=2', 'b=2');
  navigator.sendBeacon(A + '/beacon' + q + '&n=3', new Blob(['{"leaving":true}'], {type: 'application/json'}));
  for (let i = 4; i <= 5; i++) {
    fetch(O + '/beacon' + q + '&n=' + i, {method: 'POST', keepalive: true, mode: 'no-cors', body: 'x'}).catch(noop);
  }
};
addEventListener('pagehide', () => leave('pagehide'));
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') leave('hidden'); });
addEventListener('beforeunload', () => leave('beforeunload'));
addEventListener('unload', () => leave('unload'));
""",
    ),
    "leaving_beacons_subframe": (
        "3 frames (2 from O, 1 from A) that beacon while leaving: one removed at 300 ms, one sent to about:blank at 500 ms, one left in place",
        """
const mkFrame = (base) => {
  const f = document.createElement('iframe');
  f.src = base + '/f/frame_leaver';
  document.body.appendChild(f);
  return f;
};
const fr1 = mkFrame(O), fr2 = mkFrame(O), fr3 = mkFrame(A);
afterLoad(300, () => fr1.remove());
afterLoad(500, () => { fr2.src = 'about:blank'; });
""",
    ),
    "cancel_among_beacons": (
        "bursts of aborted slow fetches interleaved with sendBeacon and keepalive fetches, 3 bursts 200 ms apart",
        """
const burst = (n) => {
  for (let i = 0; i < n; i++) {
    if (i % 2 === 0) {
      // slow fetch that is aborted at once (some at 1-5 ms)
      const c = new AbortController();
      get((i % 4 ? A : O) + '/r?kind=json&delay=2000&id=cab' + i, {signal: c.signal});
      if (i % 6 === 0) setTimeout(() => c.abort(), 1 + (i % 5)); else c.abort();
    } else if (i % 8 === 1) {
      fetch(O + '/r?kind=json&id=cak' + i, {keepalive: true, mode: 'no-cors'}).catch(noop);
    } else {
      navigator.sendBeacon(O + '/beacon?n=' + i, 'x');
    }
  }
};
for (let k = 0; k < 3; k++) setTimeout(() => burst(BURST_OPS), 200 * k);
""".replace("BURST_OPS", str(BURST_OPS)),
    ),
}


def _newsfront():
    parts = []
    for name, (_, js) in PATTERNS.items():
        if name != "quiet":
            parts.append("(() => {\n" + js + "\n})();")
    return "\n".join(parts)


PATTERNS["newsfront"] = ("all of the above JS on one page", _newsfront())


def page_html(name, a, o):
    desc, js = PATTERNS[name]
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>%s</title></head><body>"
        "<h1>%s</h1><p>%s</p>"
        "<script>const A=%s, O=%s;\n%s\n%s</script>"
        "<p id=tail>end of %s</p></body></html>"
    ) % (name, name, desc, json.dumps(a), json.dumps(o), PRELUDE, js, name)


def frame_html(kind, own, other, params):
    """Inner pages used by iframes. own = origin serving this frame, other = the opposite origin."""
    if kind == "slow":
        body = (
            "for (let i = 0; i < 6; i++) {"
            " fetch('/r?kind=json&delay=2000&id=sf' + i).then((r) => r.text()).catch(() => {});"
            " const im = new Image(); im.src = '/r?kind=png&delay=2000&id=si' + i;"
            " }"
        )
    elif kind == "ad":
        n = params.get("n", ["0"])[0]
        body = (
            "navigator.sendBeacon('/beacon', 'ad=%s');"
            " const im = new Image(); im.src = '/r?kind=png&id=adpx%s';"
            " const s = document.createElement('script'); s.src = '/r?kind=js&delay=800&id=ads%s';"
            " document.head.appendChild(s);"
            " const f = document.createElement('iframe'); f.src = %s + '/f/nested?n=%s';"
            " document.body.appendChild(f);"
        ) % (n, n, n, json.dumps(other), n)
    elif kind == "frame_leaver":
        # Beacons and a keepalive POST fired while this frame is being left or detached.
        body = (
            "const own = %s, other = %s;"
            " const leave = (tag) => {"
            "  navigator.sendBeacon(other + '/beacon?t=' + tag + '&n=1', 'a=1');"
            "  navigator.sendBeacon(own + '/beacon?t=' + tag + '&n=2', 'b=2');"
            "  fetch(other + '/beacon?t=' + tag + '&n=3', {method: 'POST', keepalive: true, mode: 'no-cors', body: 'x'}).catch(() => {});"
            " };"
            " addEventListener('pagehide', () => leave('pagehide'));"
            " document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') leave('hidden'); });"
            " addEventListener('beforeunload', () => leave('beforeunload'));"
            " addEventListener('unload', () => leave('unload'));"
        ) % (json.dumps(own), json.dumps(other))
    else:  # nested
        n = params.get("n", ["0"])[0]
        body = (
            "const s = document.createElement('script'); s.src = '/r?kind=js&delay=500&id=nest%s';"
            " document.head.appendChild(s);"
        ) % n
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>frame %s</title></head>"
        "<body><p>frame %s</p><script>%s</script></body></html>"
    ) % (kind, kind, body)


class State:
    def __init__(self):
        self.origins = ["", ""]
        self.hits = {}
        self.lock = threading.Lock()

    def hit(self, path):
        with self.lock:
            self.hits[path] = self.hits.get(path, 0) + 1


class Site(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, addr, handler, idx, state):
        super().__init__(addr, handler)
        self.idx = idx
        self.state = state


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body=b"", extra=None):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _guard(self, fn):
        # Browsers abort requests on purpose; a dropped socket is not an error here.
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            self.close_connection = True

    def do_GET(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def do_OPTIONS(self):
        self._guard(lambda: self._send(204, None, b"", {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }))

    def _post(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.server.state.hit(u.path)
        self._send(204, None, b"")

    def _get(self):
        st = self.server.state
        idx = self.server.idx
        own, other = st.origins[idx], st.origins[1 - idx]
        u = urlsplit(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        st.hit(u.path)
        if u.path == "/_patterns":
            body = json.dumps({k: v[0] for k, v in PATTERNS.items()}, indent=1).encode()
            return self._send(200, "application/json", body)
        if u.path == "/_hits":
            with st.lock:
                body = json.dumps(st.hits, indent=1, sort_keys=True).encode()
            return self._send(200, "application/json", body)
        if u.path.startswith("/p/"):
            name = u.path[3:]
            if name not in PATTERNS:
                return self._send(404, "text/plain", b"unknown pattern")
            html = page_html(name, st.origins[0], st.origins[1])
            return self._send(200, "text/html; charset=utf-8", html.encode())
        if u.path.startswith("/f/"):
            kind = u.path[3:]
            if kind not in ("slow", "ad", "nested", "frame_leaver"):
                return self._send(404, "text/plain", b"unknown frame")
            html = frame_html(kind, own, other, q)
            return self._send(200, "text/html; charset=utf-8", html.encode())
        if u.path == "/r":
            return self._resource(q, own, other)
        return self._send(404, "text/plain", b"not found")

    def _resource(self, q, own, other):
        hops = int(q.get("redirect", ["0"])[0] or 0)
        if hops > 0:
            nq = {k: v[0] for k, v in q.items()}
            nq["redirect"] = str(hops - 1)
            return self._send(302, None, b"", {"Location": other + "/r?" + urlencode(nq)})
        delay = min(int(q.get("delay", ["0"])[0] or 0), MAX_DELAY_MS)
        if delay > 0:
            time.sleep(delay / 1000.0)
        kind = q.get("kind", ["json"])[0]
        rid = q.get("id", [""])[0]
        if kind == "js":
            return self._send(200, "application/javascript", ("/* %s */\n" % rid).encode())
        if kind == "css":
            return self._send(200, "text/css", b".fw-x{color:#222}\n")
        if kind == "png":
            return self._send(200, "image/png", PNG_1X1)
        if kind == "svg":
            return self._send(200, "image/svg+xml", SVG)
        if kind == "woff2":
            return self._send(200, "font/woff2", WOFF2)
        if kind == "fontcss":
            font_url = self.server.state.origins[1] + "/r?kind=woff2&id=font"
            rules = "".join(
                "@font-face{font-family:FakeFont;font-weight:%d;src:url(%s) format('woff2');}\n" % (w, font_url)
                for w in (400, 500, 600, 700, 800)
            )
            return self._send(200, "text/css", rules.encode())
        if kind == "json":
            return self._send(200, "application/json", json.dumps({"ok": True, "id": rid}).encode())
        return self._send(400, "text/plain", b"bad kind")


def start(ports=(8311, 8312), bind="127.0.0.1", public_host=("127.0.0.1", "localhost")):
    """Start both origins on daemon threads. Returns (origin_a, origin_b, stop).

    bind: address to listen on (0.0.0.0 = all interfaces, e.g. to reach it from a container).
    public_host: host name written into the pages for A and O, one string for both or a pair (A, O). The default pair makes
    A `127.0.0.1` and O `localhost`: two different SITES for Fission (two ports of one host are one site, and a frame of the
    "other origin" would never leave the page's process).
    """
    hosts = (public_host, public_host) if isinstance(public_host, str) else tuple(public_host)
    state = State()
    servers = [Site((bind, p), Handler, i, state) for i, p in enumerate(ports)]
    state.origins = ["http://%s:%d" % (hosts[i], s.server_address[1]) for i, s in enumerate(servers)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()

    def stop():
        for s in servers:
            s.shutdown()
            s.server_close()

    return state.origins[0], state.origins[1], stop


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", nargs=2, type=int, default=[8311, 8312])
    ap.add_argument("--bind", default="127.0.0.1", help="address to listen on (0.0.0.0 = all interfaces)")
    ap.add_argument("--public-host", default="127.0.0.1", help="host name written into the pages for A and O")
    args = ap.parse_args()
    a, b, stop = start(tuple(args.ports), args.bind, args.public_host)
    print("A =", a, " O =", b, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop()
