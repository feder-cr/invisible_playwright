"""A proxy the caller passed must be the road the PAGE goes out on.

**What went wrong.** `proxy=` travelled to the vendored client and stopped
there. The engine has carried `Browser.setBrowserProxy` and
`Browser.setContextProxy` all along, and the Node driver used to call them; the
Python server that replaced it read `executablePath`, `env`, `userDataDir`,
`firefoxUserPrefs` and `timeout` out of the launch message and never looked at
`proxy`. So the option was accepted, dropped, and nothing said so.

SOCKS was unaffected and that is why it went unnoticed: `configure_proxy`
writes `network.proxy.*` prefs for SOCKS, and prefs do reach the engine. For
`http` and `https` it deliberately writes no prefs and hands the endpoint back
for the driver to route per channel - which, with no driver, routes nothing.

**Why it is worse than a proxy that does not work.** Everything else in the
session is already built on the proxy's country: the timezone, the locale and
the WebRTC candidate are resolved THROUGH the proxy before the browser starts.
A browser that then connects directly does not lose the proxy, it announces one
country and connects from another - the `timezone_mismatch` this package exists
to avoid, manufactured by the package itself. Measured on 2026-08-30: three
egress lookups reached the proxy, and the page resolved its own DNS and went
out on the host address.

**These tests are in the POSITIVE form on purpose.** "The home address does not
appear" would have passed throughout the whole regression, because the page
never printed an address at all. What is asserted is that the bytes the page
displays CAME FROM the proxy: the navigation targets a host that exists in no
DNS, so only a request that went through the proxy can answer it.
"""
from __future__ import annotations

import socket
import struct
import threading

import pytest

from invisible_core import parse_proxy


def _endpoint(proxy):
    """The engine-command form of what the caller wrote.

    Both halves are the core's: the parser, and the rendering into the
    command's params. The wrapper holds neither - it holds the connection.
    """
    return parse_proxy(proxy).as_engine_command()

#: A name that resolves nowhere. `.invalid` is reserved for exactly this by
#: RFC 2606, so no future DNS change can make this test pass by accident.
_ONLY_VIA_PROXY = "solo-dal-proxy.invalid"
_MARKER = "RISPOSTA-DAL-PROXY-LOCALE"


class _CountingProxy:
    """An HTTP proxy that answers everything and remembers what it was asked.

    It does not need to forward anything: the question is whether the browser
    talks to it at all, and for a host that does not exist the only possible
    source of a 200 is this object.
    """

    def __init__(self) -> None:
        self.requests: list = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Port 0, read back off the listening socket: run_e2e.py runs four
        # workers at once and a port picked in advance is one two workers can
        # be handed at the same time.
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(32)
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(conn,),
                             daemon=True).start()

    def _answer(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            self.requests.append(data.split(b"\r\n")[0].decode("latin-1"))
            body = _MARKER.encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\n"
                         b"Content-Type: text/plain\r\n"
                         b"Content-Length: " + str(len(body)).encode() +
                         b"\r\nConnection: close\r\n\r\n" + body)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class _CountingSocks:
    """A SOCKS5 endpoint that answers everything and remembers the names.

    It exists because SOCKS is the scheme that USED to work, by a different
    road. Unifying on the engine command had to be measured on it too, or the
    repair of one branch would have been the breakage of the other - which is
    the shape of mistake this project keeps recording.
    """

    def __init__(self) -> None:
        self.asked: list = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(32)
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return "socks5://127.0.0.1:%d" % self.port

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(conn,),
                             daemon=True).start()

    def _answer(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(15)
            head = conn.recv(2)
            if len(head) < 2 or head[0] != 5:
                return
            conn.recv(head[1])
            conn.sendall(b"\x05\x00")                # no authentication
            req = conn.recv(4)
            if len(req) < 4:
                return
            atyp = req[3]
            if atyp == 1:
                host = socket.inet_ntoa(conn.recv(4))
            elif atyp == 3:
                # A NAME, not an address: the proxy resolves, we do not. That
                # is what `socks_remote_dns` buys and it must survive the move.
                host = conn.recv(conn.recv(1)[0]).decode("latin-1")
            else:
                conn.recv(16)
                host = "[v6]"
            port = struct.unpack("!H", conn.recv(2))[0]
            self.asked.append("%s:%d" % (host, port))
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            body = _MARKER.encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                         b"Content-Length: " + str(len(body)).encode() +
                         b"\r\nConnection: close\r\n\r\n" + body)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


@pytest.fixture
def counting_socks():
    proxy = _CountingSocks()
    try:
        yield proxy
    finally:
        proxy.close()


@pytest.fixture
def counting_proxy():
    proxy = _CountingProxy()
    try:
        yield proxy
    finally:
        proxy.close()


# ── what the caller wrote, in the shape the engine declares ──────────────────

@pytest.mark.parametrize("server,expected", [
    ("http://gate.example.com:8080",
     {"type": "http", "host": "gate.example.com", "port": 8080}),
    ("https://gate.example.com:3128",
     {"type": "https", "host": "gate.example.com", "port": 3128}),
    # `socks5` is what everybody writes and is NOT one of the four names the
    # engine declares. Translating it is the whole reason this function exists
    # rather than the dict being forwarded as it arrived.
    ("socks5://gate.example.com:1080",
     {"type": "socks", "host": "gate.example.com", "port": 1080}),
    ("socks4://gate.example.com:1080",
     {"type": "socks4", "host": "gate.example.com", "port": 1080}),
    # No scheme at all: Playwright documents http as the default.
    ("gate.example.com:9999",
     {"type": "http", "host": "gate.example.com", "port": 9999}),
    # No port: the default for the scheme, not a crash and not port 0.
    ("socks5://gate.example.com",
     {"type": "socks", "host": "gate.example.com", "port": 1080}),
    ("http://[2001:db8::1]:8080",
     {"type": "http", "host": "2001:db8::1", "port": 8080}),
])
def test_the_server_string_becomes_the_fields_the_engine_declares(server, expected):
    got = _endpoint({"server": server})
    for key, value in expected.items():
        assert got[key] == value, (key, got)
    assert got["bypass"] == []


def test_credentials_and_bypass_travel_in_the_shape_the_engine_wants():
    """`bypass` is a comma string on the wire and a list in the command."""
    got = _endpoint({"server": "http://h:1", "username": "u",
                         "password": "p", "bypass": "a.local, b.local"})
    assert got["username"] == "u" and got["password"] == "p"
    assert got["bypass"] == ["a.local", "b.local"]


def test_credentials_are_omitted_rather_than_sent_empty():
    """The engine declares them Optional; an empty string is not 'unset'."""
    got = _endpoint({"server": "http://h:1", "username": "", "password": None})
    assert "username" not in got and "password" not in got


@pytest.mark.parametrize("server", ["", "   ", "direct://"])
def test_no_proxy_is_the_only_case_a_caller_may_carry_on_from(server):
    """`None` back means "none was asked for", and nothing else may."""
    assert parse_proxy({"server": server}) is None


@pytest.mark.parametrize("server", [
    "ftp://h:1",           # a scheme the engine cannot express
    "http://:80",          # no host
    "http://h:abc",        # a port that is not a number
    "http://h:99999",      # a port outside the range
])
def test_a_proxy_that_cannot_be_expressed_raises_rather_than_returning_nothing(server):
    """Returning None here would be the regression, in a smaller place.

    The caller has no way to tell "no proxy was asked for" from "the proxy was
    dropped", and the second must stop the launch.
    """
    with pytest.raises(ValueError):
        _endpoint({"server": server})


# ── and the road the page actually goes out on ───────────────────────────────

@pytest.mark.e2e
def test_what_the_page_receives_came_through_the_proxy(firefox_binary, counting_proxy):
    """The positive form: the body IS the proxy's, for a host DNS cannot find.

    Before the fix this failed with NS_ERROR_UNKNOWN_HOST - the browser
    resolved the name itself, which is the proof that the proxy never reached
    it.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, timezone="UTC",
                             proxy={"server": counting_proxy.url}) as browser:
        page = browser.new_page()
        page.goto("http://%s/" % _ONLY_VIA_PROXY,
                  wait_until="domcontentloaded", timeout=30_000)
        assert _MARKER in page.locator("body").inner_text()

    assert any(_ONLY_VIA_PROXY in line for line in counting_proxy.requests), (
        "the proxy never saw the navigation: %r" % (counting_proxy.requests[:5],))


@pytest.mark.e2e
def test_a_context_proxy_is_the_road_that_context_goes_out_on(firefox_binary,
                                                              counting_proxy):
    """A per-context proxy has its own command and its own place to be lost."""
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, timezone="UTC") as browser:
        context = browser.new_context(proxy={"server": counting_proxy.url})
        page = context.new_page()
        page.goto("http://%s/" % _ONLY_VIA_PROXY,
                  wait_until="domcontentloaded", timeout=30_000)
        assert _MARKER in page.locator("body").inner_text()

    assert any(_ONLY_VIA_PROXY in line for line in counting_proxy.requests)


@pytest.mark.e2e
def test_a_proxy_that_cannot_be_applied_refuses_the_launch(firefox_binary):
    """Asked for by the reporter, and the half that stops the next regression.

    A browser that starts without the proxy it was given is the dangerous
    outcome, because the session around it is already built on that proxy's
    country. Refusing is loud; proceeding is a live session that lies.
    """
    from invisible_playwright import InvisiblePlaywright

    with pytest.raises(Exception) as caught:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 humanize=False, timezone="UTC",
                                 proxy={"server": "ftp://gate.example.com:21"}):
            pass
    assert "proxy" in str(caught.value).lower()


@pytest.mark.e2e
def test_socks_still_goes_out_through_the_proxy_after_the_roads_were_merged(
        firefox_binary, counting_socks):
    """The branch that already worked, on the road that replaced its own.

    And it asserts the NAME reached the proxy, not an address: SOCKS remote DNS
    was a property of the prefs road, and losing it would move name resolution
    back onto the host without changing a single visible byte.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, timezone="UTC",
                             proxy={"server": counting_socks.url}) as browser:
        page = browser.new_page()
        page.goto("http://%s/" % _ONLY_VIA_PROXY,
                  wait_until="domcontentloaded", timeout=30_000)
        assert _MARKER in page.locator("body").inner_text()

    assert any(a.startswith(_ONLY_VIA_PROXY + ":") for a in counting_socks.asked), (
        "the name never reached the SOCKS proxy: %r" % (counting_socks.asked[:5],))
