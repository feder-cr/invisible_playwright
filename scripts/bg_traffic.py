"""The traffic a browser makes by itself behind a proxy: one browser, one proxy that records, one local page, then standing still.

  python scripts/bg_traffic.py ARM KIND RUN OUT_DIR [IDLE_SECONDS]

ARM   ours        the published engine and core, driven by the wrapper (Juggler), the DoH of the lab declared as the provider
      retail      the official Firefox, started as a user starts it: a new empty profile, the proxy in user.js, the page on the command line, no driver
      retail-gd   the official Firefox driven by geckodriver / Marionette (its automation prefs are on)
      stock       the Firefox of Playwright (playwright install firefox), headed, with the proxy of the launch
KIND  http        an HTTP proxy (CONNECT and absolute-URI GET)       socks   a SOCKS5 gateway
The proxy is on loopback and never reaches past it: a loopback target is relayed, the lab's DoH names (192.0.2.53:443, doh.gateway.test:443) go to a fake DoH server that
answers EVERY name with a fresh address of 198.51.100.0/24 (so a connection by address can be told back to the name that was asked), and any other CONNECT is ACCEPTED and its first
flight read (up to 1 s) and dropped: its host, port, the bytes the client sent and the server name of its TLS ClientHello (SNI) are recorded; a plain GET to another host is recorded with
the bytes of its request and answered 502. Every event is written with the second it happened at (from the start of the proxy).
Output: OUT_DIR/<arm>-<kind>-<run>.json  {arm, kind, run, loaded_at, idle, events: [...], doh: [...], error}.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))

arm, kind, run, out_dir = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
IDLE = float(sys.argv[5]) if len(sys.argv) > 5 else 120.0
assert arm in ("ours", "retail", "retail-gd", "stock") and kind in ("http", "socks")
os.makedirs(out_dir, exist_ok=True)

import _doh_servers as ds  # noqa: E402  (the lab's DoH server and its constants)

T0 = time.monotonic()
EVENTS: list = []
DOH: list = []
LOCK = threading.Lock()
NAME_OF: dict = {}
IP_OF: dict = {}


def now() -> float:
    return round(time.monotonic() - T0, 3)


def add(**event) -> None:
    event["t"] = now()
    with LOCK:
        EVENTS.append(event)


def allocate(name: str) -> str:
    with LOCK:
        if name not in IP_OF:
            ip = "198.51.100.%d" % (10 + len(IP_OF))
            IP_OF[name] = ip
            NAME_OF[ip] = name
        return IP_OF[name]


class Zone(dict):
    def get(self, name, default=None):          # every name resolves, to a fresh address of its own
        return allocate(name)


_original_answer_for = ds.answer_for


def _answer_for(names, query):
    if query.qtype in (ds.QTYPE_A, ds.QTYPE_AAAA, ds.QTYPE_HTTPS):
        DOH.append({"t": now(), "name": query.name, "qtype": query.qtype})
    return _original_answer_for(Zone(), query)


ds.answer_for = _answer_for


def sni_of(flight: bytes) -> str | None:
    """The server_name of a TLS ClientHello, if the flight is one."""
    try:
        if flight[0] != 0x16 or flight[5] != 0x01:
            return None
        p = 9 + 2 + 32
        p += 1 + flight[p]                                   # session id
        p += 2 + struct.unpack("!H", flight[p:p + 2])[0]     # cipher suites
        p += 1 + flight[p]                                   # compression
        end = p + 2 + struct.unpack("!H", flight[p:p + 2])[0]
        p += 2
        while p + 4 <= end:
            etype, elen = struct.unpack("!HH", flight[p:p + 4])
            if etype == 0:
                nlen = struct.unpack("!H", flight[p + 7:p + 9])[0]
                return flight[p + 9:p + 9 + nlen].decode("ascii", "replace")
            p += 4 + elen
    except (IndexError, struct.error):
        return None
    return None


def drain(conn: socket.socket, limit: float = 1.0) -> bytes:
    """What the client sends in its first flight (up to `limit` seconds, 64 kB)."""
    conn.settimeout(0.25)
    data, end = b"", time.monotonic() + limit
    while time.monotonic() < end and len(data) < 65536:
        try:
            chunk = conn.recv(65536)
        except socket.timeout:
            if data:
                break
            continue
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    return data


def pipe(a: socket.socket, b: socket.socket) -> None:
    ds.relay(a, b)


def serve_target(conn: socket.socket, host: str, port: int, scheme: str, via: str, ok, refuse) -> None:
    """The three roads of a CONNECT target: the lab's DoH, a loopback address, anything else."""
    if (host, port) in ds.DOH_TARGETS:
        add(kind="doh-connect", host=host, port=port, via=via)
        ok()
        fake_doh.serve(conn, via)
        return
    if ds.is_loopback(host):
        add(kind="local", host=host, port=port, via=via)
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            refuse()
            return
        ok()
        try:
            pipe(conn, upstream)
        finally:
            upstream.close()
        return
    ok()
    flight = drain(conn)
    name = NAME_OF.get(host)
    add(kind="connect", host=host, port=port, name=name, sni=sni_of(flight), bytes=len(flight), via=via)


fake_doh = ds.FakeDoh({})


class HttpProxy:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn: socket.socket):
        try:
            conn.settimeout(10)
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                head += chunk
            first = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ver = (first.split(" ") + ["", "", ""])[:3]
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                host = host.strip("[]")
                serve_target(conn, host, int(port or 0), "http", "http-proxy",
                             lambda: conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n"),
                             lambda: conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"))
            else:
                from urllib.parse import urlsplit
                u = urlsplit(target)
                host = u.hostname or ""
                if ds.is_loopback(host):
                    add(kind="local", host=host, port=u.port or 80, method=method, via="http-proxy")
                    body = b""
                    try:
                        up = socket.create_connection((host, u.port or 80), timeout=10)
                        path = (u.path or "/") + ("?" + u.query if u.query else "")
                        up.sendall(head.replace(target.encode(), path.encode(), 1))
                        pipe(conn, up)
                    except OSError:
                        pass
                else:
                    add(kind="get", host=host, port=u.port or 80, method=method, path=u.path, bytes=len(head), via="http-proxy")
                    conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


class SocksGateway:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    @staticmethod
    def recv_exact(conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise OSError("closed")
            data += chunk
        return data

    def handle(self, conn: socket.socket):
        try:
            conn.settimeout(10)
            ver, n = self.recv_exact(conn, 2)
            self.recv_exact(conn, n)
            conn.sendall(b"\x05\x00")
            ver, cmd, _rsv, atyp = self.recv_exact(conn, 4)
            if atyp == 1:
                host = socket.inet_ntoa(self.recv_exact(conn, 4))
            elif atyp == 3:
                host = self.recv_exact(conn, self.recv_exact(conn, 1)[0]).decode("ascii", "replace")
            else:
                host = socket.inet_ntop(socket.AF_INET6, self.recv_exact(conn, 16))
            port = struct.unpack("!H", self.recv_exact(conn, 2))[0]
            reply = lambda code: b"\x05" + bytes([code]) + b"\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", self.port)
            if cmd != 1:                       # UDP ASSOCIATE and BIND: refused, and said
                add(kind="socks-cmd", cmd=cmd, host=host, port=port, via="socks")
                conn.sendall(reply(7))
                return
            serve_target(conn, host, port, "socks", "socks", lambda: conn.sendall(reply(0)), lambda: conn.sendall(reply(5)))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


class Page:
    """The local page, served on its own loopback port."""
    def __init__(self):
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b"<!doctype html><title>page</title><p>a local page</p>"
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/" % self.port


proxy = HttpProxy() if kind == "http" else SocksGateway()
page = Page()
result = {"arm": arm, "kind": kind, "run": run, "idle": IDLE, "loaded_at": None, "error": None}
home = tempfile.mkdtemp(prefix="bgt-")


def proxy_prefs() -> dict:
    p = {"network.proxy.type": 1, "network.proxy.no_proxies_on": ""}
    if kind == "http":
        p.update({"network.proxy.http": "127.0.0.1", "network.proxy.http_port": proxy.port, "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": proxy.port})
    else:
        p.update({"network.proxy.socks": "127.0.0.1", "network.proxy.socks_port": proxy.port, "network.proxy.socks_version": 5, "network.proxy.socks_remote_dns": True})
    return p


def run_ours():
    os.environ.setdefault("INVISIBLE_CORE_PIN", "allow")
    os.environ.setdefault("INVISIBLE_CORE_AUTOFIX", "off")
    from invisible_core import _geo
    from invisible_playwright import InvisiblePlaywright
    import _realness_support as rs

    exit_ip = rs.EXITS["germany"]
    _geo.discover_egress_ip = lambda *a, **k: exit_ip     # the core's own lookup of its exit is the product's traffic, not the browser's: measured elsewhere
    server = ("http://127.0.0.1:%d" % proxy.port) if kind == "http" else ("socks5://127.0.0.1:%d" % proxy.port)
    with InvisiblePlaywright(seed=42, binary_path=os.environ["OURS_EXE"], humanize=False, headless=False, proxy={"server": server},
                             timezone="Europe/Berlin", locale="de-DE", extra_prefs=dict(ds.DOH_PROVIDER_PREFS)) as browser:
        pg = browser.new_context().new_page()
        pg.goto(page.url, wait_until="load", timeout=60000)
        result["loaded_at"] = now()
        time.sleep(IDLE)


def run_stock():
    from playwright.sync_api import sync_playwright
    server = ("http://127.0.0.1:%d" % proxy.port) if kind == "http" else ("socks5://127.0.0.1:%d" % proxy.port)
    with sync_playwright() as p:
        b = p.firefox.launch(headless=False, proxy={"server": server})
        try:
            pg = b.new_context().new_page()
            pg.goto(page.url, wait_until="load", timeout=60000)
            result["loaded_at"] = now()
            time.sleep(IDLE)
        finally:
            b.close()


def run_retail():
    profile = os.path.join(home, "profile")
    os.makedirs(profile)
    with open(os.path.join(profile, "user.js"), "w") as f:
        for k, v in proxy_prefs().items():
            f.write("user_pref(%s, %s);\n" % (json.dumps(k), json.dumps(v)))
    env = dict(os.environ, MOZ_CRASHREPORTER_DISABLE="1")
    child = subprocess.Popen([os.environ["RETAIL_EXE"], "-no-remote", "-profile", profile, page.url], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not any(e.get("kind") == "local" for e in EVENTS):
            time.sleep(0.2)
        result["loaded_at"] = now()
        time.sleep(IDLE)
    finally:
        child.terminate()
        try:
            child.wait(20)
        except subprocess.TimeoutExpired:
            child.kill()


def run_retail_gd():
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    options = Options()
    options.binary_location = os.environ["RETAIL_EXE"]
    for k, v in proxy_prefs().items():
        options.set_preference(k, v)
    driver = webdriver.Firefox(options=options)
    try:
        driver.get(page.url)
        result["loaded_at"] = now()
        time.sleep(IDLE)
    finally:
        driver.quit()


try:
    {"ours": run_ours, "stock": run_stock, "retail": run_retail, "retail-gd": run_retail_gd}[arm]()
except Exception as exc:   # a launch that failed is its own finding
    result["error"] = str(exc).splitlines()[0][:300] if str(exc) else repr(exc)
result["events"] = EVENTS
result["doh"] = DOH
result["names_allocated"] = len(IP_OF)
with open(os.path.join(out_dir, "%s-%s-%d.json" % (arm, kind, run)), "w") as f:
    json.dump(result, f)
print(arm, kind, run, "events", len(EVENTS), "doh", len(DOH), "error", result["error"], flush=True)
os._exit(0)
