"""What the tests of "the page behind a declared exit" share: the exits, a gateway that tells a local site which of its
connections came through it, the page's reads, and the judges that turn what was read into sentences.

THE QUESTION. A person who buys a proxy expects every site to see ONE place: the address of its exit, the time zone of that
address, the language of that country, no trace of the machine the browser runs on. The check used to be made by hand, through a
paid proxy, against pages on the internet. Here the proxy is a local SOCKS5 gateway that DECLARES its exit
(``Gateway.tcp_exit``, the address it answers to "what is my address"), and the pages are local ones that read the same values a
page on the internet reads.

THE TRUTH FOR THE ZONE AND THE COUNTRY is the core's own offline geo database (``invisible_core._geo.ip_to_timezone`` and
``ip_to_country``), not a second service: the tests ask whether the browser's pages say what the core's database says about the
declared exit, which is the path ``prepare_session_geo`` takes. Whether the database is right about the world is not asked here.

THE ADDRESSES BELOW ARE PUBLIC, and each one is an address whose zone and country the geo database knows (the tests need that:
a documentation address has no zone). They are built from parts so that no dotted public address stands in a file.

Nothing here opens a browser. ``test_the_realness_helpers.py`` holds the judges and the gateway to what they say.
"""
from __future__ import annotations

import http.server
import ipaddress
import re
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from _doh_servers import DOH_TARGETS, DohGateway, is_loopback, relay
from _servers import LocalServer
from _socks_servers import WEB_NAME, WHOAMI4, WHOAMI6
from test_webrtc_realness import candidates, host_candidates, is_mdns, srflx_candidates


def _address(*parts: int) -> str:
    return ".".join(str(part) for part in parts)


#: Six declared exits, each an address whose zone and country the geo database knows, on four continents: a zone west of UTC
#: with daylight saving, a zone with a half-hour offset, one east of the date line's neighbours, a southern-hemisphere zone whose
#: daylight saving runs the other way, and two in Europe/South America. The zone the tests expect is asked of the database at
#: test time; only the address is fixed here.
EXITS: Dict[str, str] = {
    "germany": _address(1, 178, 10, 5),
    "japan": _address(1, 0, 16, 5),
    "india": _address(1, 6, 0, 5),
    "australia": _address(1, 0, 4, 5),
    "brazil": _address(1, 178, 32, 5),
    "united_states": _address(1, 32, 239, 5),
}

#: The names the pages are reached by. The FakeDoh answers them with the loopback where the site is, so the only way to the site
#: is a name resolved by DoH through the gateway and a CONNECT by address.
SITE_NAME = "exit-site.test"
ZONE_NAMES = {SITE_NAME: "127.0.0.1"}

#: Two instants a page reads its offset at: 15 January and 15 July 2025, noon UTC, in milliseconds. One is in the northern
#: winter and one in the northern summer, so a zone whose daylight saving is wrong in one hemisphere reads a wrong offset at one
#: of them.
INSTANTS_MS = (1736942400000, 1752580800000)

#: What a retail Firefox reports as the name of a zone for which ICU keeps an older canonical name. The database says the first,
#: a retail browser in that zone prints the second (or the first): either is the zone of the exit.
ZONE_NAMES_OF_A_RETAIL_FIREFOX: Dict[str, Tuple[str, ...]] = {
    "Asia/Kolkata": ("Asia/Kolkata", "Asia/Calcutta"),
}

#: The primary language subtags that are plausible for the country of an exit: the national language of the country and
#: English, which a Firefox of any country can carry (the English build is the commonest one everywhere). A country that is not
#: in the table is checked for the agreement of the reads with one another only.
PLAUSIBLE_PRIMARY: Dict[str, Set[str]] = {
    "US": {"en", "es"}, "GB": {"en"}, "CA": {"en", "fr"}, "AU": {"en"}, "NZ": {"en"}, "IE": {"en"}, "ZA": {"en", "af"},
    "IN": {"en", "hi"}, "SG": {"en", "zh"}, "DE": {"de", "en"}, "AT": {"de", "en"}, "CH": {"de", "fr", "it", "en"},
    "FR": {"fr", "en"}, "BE": {"fr", "nl", "en"}, "NL": {"nl", "en"}, "IT": {"it", "en"}, "ES": {"es", "ca", "en"},
    "PT": {"pt", "en"}, "BR": {"pt", "en"}, "MX": {"es", "en"}, "AR": {"es", "en"}, "CL": {"es", "en"}, "CO": {"es", "en"},
    "JP": {"ja", "en"}, "KR": {"ko", "en"}, "CN": {"zh", "en"}, "TW": {"zh", "en"}, "PL": {"pl", "en"}, "SE": {"sv", "en"},
    "NO": {"nb", "nn", "no", "en"}, "DK": {"da", "en"}, "FI": {"fi", "en"}, "RU": {"ru", "en"}, "UA": {"uk", "ru", "en"},
    "TR": {"tr", "en"},
}


# ---------------------------------------------------------------------------------------------------------- the gateway
class ExitGateway(DohGateway):
    """A gateway that declares an exit AND remembers the local port of every connection it opened for a page.

    A real proxy's exit is the address a site sees as the peer of its connection. On loopback every peer is 127.0.0.1, so the
    gateway says which connections are its own instead: ``upstream_ports`` holds the source port of each connection it opened
    to a loopback server, recorded BEFORE it answers the CONNECT, so the server (which is asked only after the browser has the
    answer) always finds it. A connection of the browser that did not come through the gateway has a port that is not there:
    to the site it is a direct connection, a leak."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.upstream_ports: Set[int] = set()
        self._ports_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def came_through(self, source_port: int) -> bool:
        with self._ports_lock:
            return source_port in self.upstream_ports

    def _connect(self, c: socket.socket, host: str, port: int) -> None:
        special = (host, port) in DOH_TARGETS or (host, port) in (WHOAMI4, WHOAMI6, (WEB_NAME, 80))
        if special or not is_loopback(host):
            super()._connect(c, host, port)
            return
        self.connects.append((host, port))
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            self._send(c, self._reply(5))
            return
        with self._live_lock:
            self._live.append(upstream)
        with self._ports_lock:
            self.upstream_ports.add(upstream.getsockname()[1])
        self._send(c, self._reply(0))
        try:
            relay(c, upstream, self.rtt / 2 if self.tcp_data_delay else 0.0)
        finally:
            upstream.close()


# ------------------------------------------------------------------------------------------------------------- the reads
#: What a page and a worker read, as ONE function: the worker's source is made from it, so the two cannot drift. The offset is
#: read two ways: from the clock of the page (``getTimezoneOffset``) and from the wall clock ``Intl`` prints for the zone the
#: page itself names (the difference with UTC). ``0 - x`` and not ``-x``, so that a zero is +0 on the wire.
READ_JS = """() => {
  const instants = %s;
  const zone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
  const ahead = (t) => {
    const p = {};
    for (const x of new Intl.DateTimeFormat('en-US', {timeZone: zone, hourCycle: 'h23', year: 'numeric', month: 'numeric',
        day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric'}).formatToParts(new Date(t))) p[x.type] = x.value;
    return Math.round((Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour %% 24, +p.minute, +p.second) - t) / 60000);
  };
  return {
    zone,
    offsets_by_date: instants.map((t) => 0 - new Date(t).getTimezoneOffset()),
    offsets_by_intl: instants.map(ahead),
    language: navigator.language,
    languages: [...navigator.languages],
    intl: new Intl.DateTimeFormat().resolvedOptions().locale,
  };
}""" % list(INSTANTS_MS)

#: The page: asks the site what address it sees, makes one fetch (the header the site receives from a fetch), reads, and has a
#: dedicated worker read the same and fetch once (the header of a worker's request).
PAGE_JS = """async () => {
  const read = %s;
  const ip = (await (await fetch('/ip', {cache: 'no-store'})).text()).trim();
  await (await fetch('/fetch', {cache: 'no-store'})).text();
  const own = read();
  const worker = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('the worker never answered')), 20000);
    const w = new Worker('/worker.js');
    w.onmessage = (e) => { clearTimeout(timer); resolve(e.data); };
    w.onerror = (e) => { clearTimeout(timer); reject(new Error('worker error: ' + e.message)); };
    w.postMessage(0);
  });
  return {ip, page: own, worker};
}""" % READ_JS

WORKER_JS = ("const read = %s;\nonmessage = async () => {\n  await (await fetch('/worker-fetch', {cache: 'no-store'})).text();\n"
             "  postMessage(read());\n};\n" % READ_JS)


# --------------------------------------------------------------------------------------------------------------- the site
@dataclass(frozen=True)
class Arrival:
    """One request the site received: the path, the source port of the connection, and whether the gateway opened it."""
    path: str
    source: str
    via_gateway: bool
    accept_language: Optional[str]


class Site:
    """The local site: ``/ip`` says which address the site sees (the exit the gateway declares, or ``DIRECT <address>`` for a
    connection the gateway did not open), ``/fetch`` and ``/worker-fetch`` only count, ``/worker.js`` is the worker."""

    def __init__(self, gateway: ExitGateway):
        outer = self
        self.gateway = gateway
        self.arrivals: List[Arrival] = []
        self._lock = threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                host, port = self.client_address[:2]
                through = outer.gateway.came_through(port)
                with outer._lock:
                    outer.arrivals.append(Arrival(path, "%s:%d" % (host, port), through, self.headers.get("Accept-Language")))
                if path == "/ip":
                    body, kind = (outer.gateway.tcp_exit if through else "DIRECT %s" % host).encode(), "text/plain"
                elif path == "/worker.js":
                    body, kind = WORKER_JS.encode(), "text/javascript"
                elif path == "/":
                    body, kind = b"<!doctype html><title>exit</title><body>exit</body>", "text/html; charset=utf-8"
                else:
                    body, kind = b"ok", "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = LocalServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, name: str = SITE_NAME, path: str = "/") -> str:
        return "http://%s:%d%s" % (name, self.port, path)

    def headers_of(self, *paths: str) -> Dict[str, Optional[str]]:
        """The Accept-Language of the LAST request of each path (None when the request carried none or never came)."""
        with self._lock:
            seen = list(self.arrivals)
        return {path: next((a.accept_language for a in reversed(seen) if a.path == path), None) for path in paths}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ------------------------------------------------------------------------------------------------------- the host's own
def host_addresses() -> Set[str]:
    """Every address of every interface of this machine, normalised (loopback and link-local included). ``psutil`` is a
    dependency of the package, and answers the same on Linux and on Windows."""
    import psutil

    found: Set[str] = set()
    for addresses in psutil.net_if_addrs().values():
        for entry in addresses:
            if entry.family in (socket.AF_INET, socket.AF_INET6):
                try:
                    found.add(ipaddress.ip_address(entry.address.split("%")[0]).compressed)
                except ValueError:
                    continue
    return found


_IPV4_WORD = re.compile(r"(?<![0-9A-Za-z.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Za-z.])")
_IPV6_WORD = re.compile(r"(?<![0-9A-Za-z:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z:])")


def addresses_in(text: str) -> List[ipaddress._BaseAddress]:
    """Every IPv4 and IPv6 address written in ``text`` (a name such as ``<uuid>.local`` is none)."""
    found = []
    for token in _IPV4_WORD.findall(text) + _IPV6_WORD.findall(text):
        try:
            found.append(ipaddress.ip_address(token))
        except ValueError:
            continue
    return found


# -------------------------------------------------------------------------------------------------------------- the judges
def ip_problems(ip_seen: str, arrivals: Sequence[Arrival], direct: Sequence[str], exit_ip: str, connects: Sequence[Tuple[str, int]],
                site_port: int) -> List[str]:
    """What is wrong with "the site sees the exit and nothing reached it, or the network, directly"."""
    problems: List[str] = []
    if ip_seen != exit_ip:
        problems.append("the page was told its address is %r, the gateway's declared exit is %r" % (ip_seen, exit_ip))
    if not arrivals:
        problems.append("the site received no request at all")
    outside = [a for a in arrivals if not a.via_gateway]
    if outside:
        problems.append("the site received %d request(s) on connections the gateway did not open (a direct connection): %s"
                        % (len(outside), [(a.path, a.source) for a in outside]))
    if ("127.0.0.1", site_port) not in connects:
        problems.append("the gateway never saw a CONNECT to the site, it saw %s" % list(connects))
    if direct:
        problems.append("the browser opened connections past the gateway: %s" % list(direct))
    return problems


def webrtc_problems(probe: Dict[str, Any], exit_ip: str, host: Iterable[str]) -> List[str]:
    """What of this machine, or what incoherent with the exit, the candidates and the SDP of a gather show."""
    lines = list(probe.get("candidates") or [])
    sdp = probe.get("sdp") or ""
    cands = candidates(lines)
    problems: List[str] = []
    if not cands:
        return ["the gather produced no candidate at all (%s)" % probe.get("errors")]
    raw = [c["address"] for c in host_candidates(cands) if not is_mdns(c["address"])]
    if raw:
        problems.append("host candidates that are not mDNS names: %s" % raw)
    six = [c["address"] for c in cands if ":" in c["address"] and not is_mdns(c["address"])]
    if six and ipaddress.ip_address(exit_ip).version == 4:
        problems.append("IPv6 candidates behind an IPv4 exit: %s" % six)
    reflexive = sorted({c["address"] for c in srflx_candidates(cands)})
    if reflexive and reflexive != [exit_ip]:
        problems.append("the server-reflexive candidate is %s, the exit is %s" % (reflexive, exit_ip))
    written = addresses_in(sdp + "\n" + "\n".join(lines))
    private = sorted({str(a) for a in written if a.version == 4 and not a.is_unspecified and (a.is_private or a.is_link_local
                                                                                              or a.is_loopback)})
    if private:
        problems.append("private or loopback IPv4 addresses in the SDP or the candidates: %s" % private)
    mine = set(host)
    own = sorted({a.compressed for a in written if a.compressed in mine})
    if own:
        problems.append("addresses of this machine's interfaces in the SDP or the candidates: %s" % own)
    return problems


def acceptable_zones(zone: str) -> Tuple[str, ...]:
    return ZONE_NAMES_OF_A_RETAIL_FIREFOX.get(zone, (zone,))


def expected_offsets(zone: str) -> List[int]:
    """The minutes a zone is ahead of UTC at each of :data:`INSTANTS_MS`, from the system tz database (``zoneinfo``)."""
    info = ZoneInfo(zone)
    return [int(datetime.fromtimestamp(t / 1000, tz=info).utcoffset().total_seconds() // 60) for t in INSTANTS_MS]


def timezone_problems(reads: Dict[str, Dict[str, Any]], zone: str) -> List[str]:
    """``reads`` maps a scope name (page, worker) to what :data:`READ_JS` returned there; every scope must say ``zone`` and the
    offsets of ``zone`` at both instants, read both ways."""
    problems: List[str] = []
    want = expected_offsets(zone)
    for scope, read in reads.items():
        if read.get("zone") not in acceptable_zones(zone):
            problems.append("%s: Intl says the zone is %r, the exit's is %s" % (scope, read.get("zone"), zone))
        for way in ("offsets_by_date", "offsets_by_intl"):
            if read.get(way) != want:
                problems.append("%s: %s are %s, the offsets of %s at the two instants are %s" % (scope, way, read.get(way), zone, want))
    return problems


def _tags(header: str) -> List[str]:
    return [part.split(";")[0].strip() for part in header.split(",") if part.strip()]


def language_problems(reads: Dict[str, Dict[str, Any]], headers: Dict[str, Optional[str]], country: Optional[str]) -> List[str]:
    """The language of a session as everything a site can read says it: ``reads`` (page, worker), the Accept-Language of the
    requests (``headers``, by path), and the plausibility of the primary language for ``country``."""
    problems: List[str] = []
    page = reads["page"]
    languages = page.get("languages") or []
    if not languages:
        return ["navigator.languages is empty"]
    primary = languages[0]
    if page.get("language") != primary:
        problems.append("navigator.language is %r, navigator.languages starts with %r" % (page.get("language"), primary))
    for scope, read in reads.items():
        if read.get("intl") != primary:
            problems.append("%s: the Intl locale is %r, the language is %r" % (scope, read.get("intl"), primary))
        if scope != "page":
            for key in ("language", "languages"):
                if read.get(key) != page.get(key):
                    problems.append("%s: %s is %r, the page's is %r" % (scope, key, read.get(key), page.get(key)))
    for path, header in headers.items():
        if header is None:
            problems.append("%s: the request carried no Accept-Language" % path)
        elif _tags(header) != languages:
            problems.append("%s: Accept-Language is %r, navigator.languages is %s" % (path, header, languages))
    table = PLAUSIBLE_PRIMARY.get(country or "")
    if table is not None and primary.split("-")[0].lower() not in table:
        problems.append("the language %r is not plausible for %s (plausible: %s)" % (primary, country, sorted(table)))
    return problems


@dataclass
class Observation:
    """Everything one launch behind one declared exit showed, as plain data (the browser and the servers are closed)."""
    label: str
    exit_ip: str
    zone: str
    country: Optional[str]
    ip_seen: str = ""
    reads: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    arrivals: List[Arrival] = field(default_factory=list)
    headers: Dict[str, Optional[str]] = field(default_factory=dict)
    probe: Dict[str, Any] = field(default_factory=dict)
    connects: List[Tuple[str, int]] = field(default_factory=list)
    site_port: int = 0
    direct: List[str] = field(default_factory=list)
    watched: int = 0
    host: Set[str] = field(default_factory=set)
    doh_vias: Dict[str, Set[str]] = field(default_factory=dict)
    doh_connects: List[Tuple[str, int]] = field(default_factory=list)
    gateway_name: str = ""
    trap_ran: bool = False
    trap_queries: List[str] = field(default_factory=list)
    datagrams: List[Tuple[str, int]] = field(default_factory=list)
