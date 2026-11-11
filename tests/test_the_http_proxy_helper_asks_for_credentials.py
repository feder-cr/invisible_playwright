"""The HTTP proxy of the DoH e2e (`_http_proxy.py`) refuses without credentials, serves with them, and records headers.

Tested with raw sockets and `http.client`: the e2e asserts on what this proxy recorded (the preemptive
`Proxy-Authorization` of the DoH CONNECT), so what it records has to be what the wire carried.
"""
from __future__ import annotations

import base64
import http.client
import socket
import ssl
import threading

import pytest

from _doh_servers import DOH_BOOTSTRAP, DOH_HOST, DOH_PATH, FakeDoh, QTYPE_A, encode_query, wait_until
from _http_proxy import HttpProxy, basic

pytestmark = pytest.mark.unit

USER, PASSWORD = "ferd:user", "pässword"      # a colon in the name and a non-ASCII password: UTF-8, as RFC 7617 says
NAMES = {"page-a.test": "127.0.0.1"}


@pytest.fixture
def doh():
    with FakeDoh(NAMES) as server:
        yield server


def _connect_raw(proxy, target, authorization=None):
    """CONNECT on a raw socket: (socket, status line, headers)."""
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
    head = "CONNECT %s HTTP/1.1\r\nHost: %s\r\n" % (target, target)
    if authorization:
        head += "Proxy-Authorization: %s\r\n" % authorization
    s.sendall((head + "\r\n").encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    lines = data.split(b"\r\n\r\n")[0].decode().split("\r\n")
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
    return s, lines[0], headers


def test_basic_is_base64_of_utf8_user_colon_password():
    assert basic(USER, PASSWORD) == "Basic " + base64.b64encode("ferd:user:pässword".encode("utf-8")).decode()


def test_a_connect_without_credentials_gets_407_with_a_basic_challenge(doh):
    with HttpProxy("http1", doh, user=USER, password=PASSWORD) as proxy:
        s, status, headers = _connect_raw(proxy, "%s:443" % DOH_BOOTSTRAP)
        s.close()
        assert status.split()[1] == "407"
        assert headers["proxy-authenticate"].lower().startswith("basic")
        assert proxy.connects == []
        assert [(r.method, r.authorization, r.status) for r in proxy.refused_for_credentials] == [("CONNECT", None, 407)]


def test_a_connect_with_the_wrong_credentials_gets_407_and_the_header_is_recorded(doh):
    with HttpProxy("http1", doh, user=USER, password=PASSWORD) as proxy:
        wrong = basic(USER, "not-it")
        s, status, _h = _connect_raw(proxy, "%s:443" % DOH_BOOTSTRAP, wrong)
        s.close()
        assert status.split()[1] == "407"
        assert proxy.authorizations_for(DOH_BOOTSTRAP, 443) == [wrong]


def test_a_connect_with_the_right_credentials_is_accepted_and_the_header_is_recorded(doh):
    with HttpProxy("http1", doh, user=USER, password=PASSWORD) as proxy:
        s, status, _h = _connect_raw(proxy, "%s:443" % DOH_BOOTSTRAP, basic(USER, PASSWORD))
        assert status.split()[1] == "200"
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(["http/1.1"])
        tls = context.wrap_socket(s, server_hostname=DOH_HOST)
        message = encode_query("page-a.test", QTYPE_A)
        tls.sendall(("POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/dns-message\r\n"
                     "Content-Length: %d\r\nConnection: close\r\n\r\n" % (DOH_PATH, DOH_HOST, len(message))).encode()
                    + message)
        reply = b""
        while True:
            chunk = tls.recv(65536)
            if not chunk:
                break
            reply += chunk
        tls.close()
        assert reply.startswith(b"HTTP/1.1 200")
        assert proxy.doh_authorizations() == [basic(USER, PASSWORD)]
        assert proxy.doh_connects() == [(DOH_BOOTSTRAP, 443)]
    assert doh.vias_of("page-a.test") == {"http1"}


def test_a_proxy_without_credentials_asks_for_none(doh):
    with HttpProxy("http1", doh) as proxy:
        s, status, _h = _connect_raw(proxy, "%s:443" % DOH_HOST)
        s.close()
        assert status.split()[1] == "200"
        assert proxy.doh_authorizations() == [None]
        assert proxy.proxy == {"server": proxy.server}


def test_the_endpoint_dict_carries_the_credentials(doh):
    with HttpProxy("http1", doh, user=USER, password=PASSWORD) as proxy:
        assert proxy.proxy == {"server": "http://127.0.0.1:%d" % proxy.port, "username": USER, "password": PASSWORD}


def test_a_connect_to_loopback_is_relayed_and_one_that_would_leave_it_is_refused(doh):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        c, _ = listener.accept()
        c.sendall(b"hello")
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    with HttpProxy("http1", doh) as proxy:
        s, status, _h = _connect_raw(proxy, "127.0.0.1:%d" % listener.getsockname()[1])
        assert status.split()[1] == "200"
        assert s.recv(10) == b"hello"
        s.close()
        s, status, _h = _connect_raw(proxy, "198.51.100.77:443")
        s.close()
        assert status.split()[1] == "403"
        s, status, _h = _connect_raw(proxy, "page-a.test:443")
        s.close()
        assert status.split()[1] == "403", "a name is refused, not resolved"
        assert wait_until(lambda: len(proxy.seen) == 3, 5)
    listener.close()


def test_a_plain_get_in_the_absolute_form_is_forwarded_by_the_zone_of_the_doh(doh):
    """The form a browser sends to an HTTP proxy for an http page: the name travels to the proxy, which resolves it."""
    import http.server

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = ("host=%s path=%s via=%s" % (self.headers["Host"], self.path,
                                                self.headers.get("Proxy-Authorization"))).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        with HttpProxy("http1", doh, user=USER, password=PASSWORD) as proxy:
            conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
            conn.request("GET", "http://page-a.test:%d/x?y=1" % port, headers={"Host": "page-a.test:%d" % port})
            assert conn.getresponse().status == 407
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
            conn.request("GET", "http://page-a.test:%d/x?y=1" % port,
                         headers={"Host": "page-a.test:%d" % port, "Proxy-Authorization": basic(USER, PASSWORD)})
            response = conn.getresponse()
            body = response.read().decode()
            conn.close()
            assert response.status == 200
            assert body == "host=page-a.test:%d path=/x?y=1 via=None" % port, "the proxy's header is not passed on"
            conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
            conn.request("GET", "http://not-in-the-zone.test:%d/" % port,
                         headers={"Proxy-Authorization": basic(USER, PASSWORD)})
            assert conn.getresponse().status == 502
            conn.close()
            assert [s.status for s in proxy.seen] == [407, 200, 502]
    finally:
        server.shutdown()
        server.server_close()
