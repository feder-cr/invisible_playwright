"""E2E: the patched Firefox AUTHENTICATES to an HTTP proxy and goes out through it.

**What went wrong (issue #206).** With an authenticated HTTP proxy the browser
started without an error and the page came out of the HOST address, while the
same credentials over `socks5` worked. The cause is in the engine, not here:
Gecko carries username and password on `nsIProxyInfo` for SOCKS only, so
supplying them for `http` made the construction throw inside the channel
filter, the filter never answered, and necko carried on with no proxy at all.
A direct connection, and nothing said so.

**Why no gate here could have seen it.** Measured on this tree before the file
was written: the two HTTP arms of `test_proxy_reaches_the_browser.py`
(`test_what_the_page_receives_came_through_the_proxy` and
`test_a_context_proxy_is_the_road_that_context_goes_out_on`) launch with no
credentials at all; the only authenticated end-to-end arm is SOCKS5, in
`test_proxy_socks_auth_e2e.py`; and the single test that puts `http` and
credentials together,
`test_credentials_and_bypass_travel_in_the_shape_the_engine_wants`, asserts on
a Python dict and never opens a browser. So the product had never once been
driven out through an authenticated HTTP proxy under test, and the one
combination that was broken is the one nothing exercised.

**The assertion is positive and double, on purpose.** "The proxy received
something" would have been GREEN on the leak: a browser that connects directly
leaves no trace on the proxy, so an empty recorder and a healthy proxy are the
same observation, and the negative form cannot tell them apart. What is
asserted instead is that this proxy ROUTED the page's own request, and that the
bytes the page displays are bytes only this proxy instance could have produced
- the target host is reserved by RFC 2606 and resolves nowhere, and the body
carries this proxy's own listening port.

Hermetic, like the SOCKS file beside it: a local HTTP proxy that refuses with
407 until it is shown the right `Proxy-Authorization`. No network, no external
site, nobody's real credentials.
"""
from __future__ import annotations

import base64
import socket
import threading

import pytest

_USER = "ferd_http_user"
_PASS = "ferd_http_pw_42"

#: Reserved by RFC 2606 to resolve nowhere, so a 200 for it can only have come
#: from the proxy. The same device as the neighbouring file, and the reason
#: both of them are written in the positive form.
_ONLY_VIA_PROXY = "only-via-proxy.invalid"
_MARKER = "ANSWER-FROM-THE-AUTHENTICATED-PROXY"


def _decode_basic(header: str) -> tuple[str, str]:
    """`Basic <base64>` back into the pair it carries.

    Anything that is not a decodable Basic credential is returned as a pair
    that cannot match the expected one, so a malformed header is recorded
    rather than swallowed: the test's failure message is the only place the
    reader learns what the browser actually offered.
    """
    scheme, _, blob = header.partition(" ")
    if scheme.lower() != "basic":
        return (header, "")
    try:
        decoded = base64.b64decode(blob).decode("utf-8", "replace")
    except Exception:
        return (header, "")
    user, _, password = decoded.partition(":")
    return (user, password)


class _AuthenticatingProxy:
    """An HTTP proxy that answers 407 until it is shown the right credentials.

    It records the two things a direct connection cannot produce: the
    credentials it was given, and the request lines it routed AFTER accepting
    them. It answers each request itself instead of forwarding it - what is in
    question is whether the browser talks to it at all, and for a host that
    exists in no DNS this object is the only possible source of a 200.
    """

    def __init__(self) -> None:
        self.credentials: list[tuple[str, str]] = []
        self.routed: list[str] = []
        self.refused: list[str] = []
        self._expected = "Basic " + base64.b64encode(
            ("%s:%s" % (_USER, _PASS)).encode()).decode()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Port 0, read back off the listening socket: run_e2e.py runs four
        # workers at once, and a port chosen in advance is one that two workers
        # can be handed in the same moment.
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(32)
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    @property
    def body(self) -> str:
        """What only THIS proxy instance can put on a page.

        The port is in it deliberately. A bare marker would also be served by a
        second copy of this fixture running in a parallel worker, and the point
        of the assertion is the road the page took, not the string.
        """
        return "%s:%d" % (_MARKER, self.port)

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
            lines = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
            request_line = lines[0]
            offered = None
            for line in lines[1:]:
                name, _, value = line.partition(":")
                if name.strip().lower() == "proxy-authorization":
                    offered = value.strip()
            if offered is not None:
                self.credentials.append(_decode_basic(offered))
            if offered != self._expected:
                self.refused.append(request_line)
                self._send(conn, "407 Proxy Authentication Required",
                           "proxy authentication required",
                           challenge='Basic realm="invisible-playwright"')
                return
            self.routed.append(request_line)
            self._send(conn, "200 OK", self.body)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _send(conn: socket.socket, status: str, text: str,
              challenge: str | None = None) -> None:
        body = text.encode()
        head = "HTTP/1.1 %s\r\nContent-Type: text/plain\r\n" % status
        if challenge is not None:
            head += "Proxy-Authenticate: %s\r\n" % challenge
        head += "Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body)
        conn.sendall(head.encode() + body)

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


@pytest.fixture
def authenticating_proxy():
    proxy = _AuthenticatingProxy()
    try:
        yield proxy
    finally:
        proxy.close()


@pytest.mark.e2e
def test_the_page_goes_out_through_an_authenticated_http_proxy(
        firefox_binary, authenticating_proxy):
    """The gate issue #206 needed and this repository did not have.

    The navigation error is caught rather than left to propagate, and that is
    not leniency - it is the whole diagnosis. Both known-bad engines fail
    INSIDE `goto`, so an uncaught error puts a bare Gecko code in the report
    and none of the assertions below ever print. The two codes mean opposite
    things and the message names them: NS_ERROR_UNKNOWN_HOST is the browser
    resolving the name itself, i.e. no proxy at all, which is issue #206;
    NS_ERROR_PROXY_CONNECTION_REFUSED is the browser reaching the proxy and
    being turned away, i.e. the credentials did not arrive. Nothing is
    swallowed: a caught error fails the first assertion, and `shown` stays
    empty so it would fail the second one too.
    """
    from invisible_playwright import InvisiblePlaywright

    navigation_error = None
    shown = ""
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, timezone="UTC",
                             proxy={"server": authenticating_proxy.url,
                                    "username": _USER,
                                    "password": _PASS}) as browser:
        page = browser.new_page()
        try:
            page.goto("http://%s/" % _ONLY_VIA_PROXY,
                      wait_until="domcontentloaded", timeout=30_000)
            shown = page.locator("body").inner_text()
        except Exception as exc:                # noqa: BLE001 - re-raised below
            navigation_error = exc

    assert navigation_error is None, (
        "the navigation never completed through the authenticated proxy: %s. "
        "NS_ERROR_UNKNOWN_HOST means the browser resolved the name itself and "
        "went out with no proxy at all, which is issue #206; "
        "NS_ERROR_PROXY_CONNECTION_REFUSED means it reached the proxy and was "
        "refused, i.e. the credentials never arrived. The proxy refused %r and "
        "was offered the credentials %r"
        % (navigation_error, authenticating_proxy.refused[:5],
           authenticating_proxy.credentials[:5]))
    assert authenticating_proxy.body in shown, (
        "the page did not receive the authenticated proxy's own body. It read "
        "%r, and only this proxy can serve %r for a host that resolves "
        "nowhere. Credentials the proxy was offered: %r"
        % (shown[:200], authenticating_proxy.body,
           authenticating_proxy.credentials[:5]))
    assert any(_ONLY_VIA_PROXY in line for line in authenticating_proxy.routed), (
        "the proxy never routed the navigation after authenticating it. "
        "Routed: %r, refused with 407: %r"
        % (authenticating_proxy.routed[:5], authenticating_proxy.refused[:5]))
    assert (_USER, _PASS) in authenticating_proxy.credentials, (
        "the browser never offered the configured proxy credentials; the proxy "
        "saw: %r" % (authenticating_proxy.credentials[:5],))
