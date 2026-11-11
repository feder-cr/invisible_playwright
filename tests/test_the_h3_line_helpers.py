"""The server and the judge of the HTTP/3 e2e work, without a browser (hermetic, loopback).

A retail Firefox visits a page three times in one context: the first visit is ``h2`` (the TCP answer carries the Alt-Svc),
the two later ones are ``h3`` when UDP is free (retail 159 Linux, 15 of 15) and ``h2`` every time when it is dropped (15 of
15), with no delay on the later loads. The e2e asserts that sequence of ``nextHopProtocol``. These tests hold what it stands
on: the local server really speaks HTTP/2 on its TCP port (if it spoke HTTP/1.1 the first visit could never read ``h2``), says
``Alt-Svc: h3`` there, and answers the same document over HTTP/3; and the function that judges three visits names what is wrong.
"""
from __future__ import annotations

import asyncio
import socket
import ssl

import pytest

import _udp_line as line
from _h3_server import PAGE, H3Server

pytestmark = pytest.mark.unit


# -- the server ---------------------------------------------------------------------------------------------------------
def _tls_client(server, alpn):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(alpn)
    raw = socket.create_connection((server.address, server.port), timeout=10)
    return context.wrap_socket(raw, server_hostname=server.address)


def _h2_get(server, path):
    import h2.config
    import h2.connection
    import h2.events

    tls = _tls_client(server, ["h2", "http/1.1"])
    try:
        assert tls.selected_alpn_protocol() == "h2", "the TCP side did not negotiate h2"
        conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8"))
        conn.initiate_connection()
        conn.send_headers(1, [(":method", "GET"), (":authority", server.address), (":scheme", "https"), (":path", path)],
                          end_stream=True)
        tls.sendall(conn.data_to_send())
        headers, body, ended = {}, b"", False
        while not ended:
            data = tls.recv(65536)
            assert data, "the server closed the connection before the end of the stream"
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.ResponseReceived):
                    headers = dict(event.headers)
                elif isinstance(event, h2.events.DataReceived):
                    body += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    ended = True
            out = conn.data_to_send()
            if out:
                tls.sendall(out)
        return headers, body
    finally:
        tls.close()


def test_the_tcp_side_speaks_http2_and_says_where_h3_is():
    with H3Server("127.0.0.1") as server:
        headers, body = _h2_get(server, "/?v=1")
        assert headers[":status"] == "200"
        assert headers["alt-svc"] == 'h3=":%d"; ma=3600' % server.port
        assert headers["content-type"].startswith("text/html")
        assert body == PAGE
        assert server.tcp_alpn == ["h2"] and server.tcp_requests == ["/?v=1"]


def test_a_second_request_on_the_same_connection_is_answered_too():
    with H3Server("127.0.0.1") as server:
        for path in ("/?v=1", "/?v=2"):
            assert _h2_get(server, path)[1] == PAGE
        assert server.tcp_requests == ["/?v=1", "/?v=2"]


def test_a_client_that_offers_only_http1_is_answered_with_the_alt_svc_too():
    with H3Server("127.0.0.1") as server:
        tls = _tls_client(server, ["http/1.1"])
        try:
            tls.sendall(b"GET /?v=1 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            data = b""
            while True:
                chunk = tls.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            tls.close()
        assert data.startswith(b"HTTP/1.1 200 OK") and data.endswith(PAGE)
        assert ('Alt-Svc: h3=":%d"; ma=3600' % server.port).encode() in data
        assert server.tcp_alpn == ["http/1.1"]


def test_the_same_document_is_answered_over_http3_and_the_peer_is_recorded():
    from aioquic.asyncio.client import connect
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.h3.connection import H3_ALPN, H3Connection
    from aioquic.h3.events import DataReceived, HeadersReceived
    from aioquic.quic.configuration import QuicConfiguration

    class Client(QuicConnectionProtocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.http = None
            self.status, self.body = None, b""
            self.finished = asyncio.Event()

        def quic_event_received(self, event):
            if self.http is None:
                return
            for http_event in self.http.handle_event(event):
                if isinstance(http_event, HeadersReceived):
                    self.status = dict(http_event.headers).get(b":status")
                elif isinstance(http_event, DataReceived):
                    self.body += http_event.data
                    if http_event.stream_ended:
                        self.finished.set()

        async def get(self, authority, path):
            self.http = H3Connection(self._quic)
            stream = self._quic.get_next_available_stream_id()
            self.http.send_headers(stream, [(b":method", b"GET"), (b":scheme", b"https"),
                                            (b":authority", authority.encode()), (b":path", path.encode())], end_stream=True)
            self.transmit()
            await asyncio.wait_for(self.finished.wait(), 10)

    async def fetch(server):
        configuration = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, verify_mode=ssl.CERT_NONE)
        async with connect(server.address, server.port, configuration=configuration, create_protocol=Client) as client:
            await client.get("%s:%d" % (server.address, server.port), "/?v=2")
            return client.status, client.body

    with H3Server("127.0.0.1") as server:
        status, body = asyncio.run(fetch(server))
        assert status == b"200" and body == PAGE
        assert server.quic_peers == ["127.0.0.1"] and server.h3_requests == ["/?v=2"]


# -- the sequence a retail Firefox reads, and the judge -------------------------------------------------------------------
@pytest.mark.parametrize("road, sequence", [
    ("direct", ["h2", "h3", "h3"]),
    ("socks5_coherent", ["h2", "h3", "h3"]),
    ("socks5_no_udp", ["h2", "h2", "h2"]),
    ("socks5_mismatched", ["h2", "h2", "h2"]),
    ("socks5_unmeasured", ["h2", "h2", "h2"]),
    ("http_proxy", ["h2", "h2", "h2"]),
])
def test_the_sequence_of_each_road(road, sequence):
    assert line.expected_protocols(road) == sequence
    assert line.judge_visits(road, sequence, [900, 300, 310]) == []


def test_an_unknown_road_is_refused():
    with pytest.raises(KeyError):
        line.expected_protocols("socks5_maybe")


def test_h3_on_the_first_visit_is_an_error_and_says_so():
    problems = line.judge_visits("socks5_coherent", ["h3", "h3", "h3"], [900, 300, 310])
    assert any("visit 1 read h3" in p for p in problems), problems
    problems = line.judge_visits("direct", ["h3", "h3", "h3"], [900, 300, 310])
    assert any("visit 1 read h3" in p for p in problems), problems


def test_h3_where_udp_is_dropped_is_an_error():
    for road in ("socks5_no_udp", "socks5_mismatched", "socks5_unmeasured", "http_proxy"):
        problems = line.judge_visits(road, ["h2", "h3", "h3"], [900, 300, 310])
        assert any("retail Firefox reads ['h2', 'h2', 'h2']" in p for p in problems), (road, problems)


def test_h2_where_udp_is_free_is_an_error():
    for road in ("direct", "socks5_coherent"):
        assert line.judge_visits(road, ["h2", "h2", "h2"], [900, 300, 310]), road
        assert line.judge_visits(road, ["h2", "h3", "h2"], [900, 300, 310]), road


def test_a_later_load_that_waits_for_a_fallback_is_an_error_where_udp_is_dropped_and_only_there():
    slow = [900, 900 + line.LOAD_MARGIN_MS + 1, 310]
    problems = line.judge_visits("socks5_no_udp", ["h2", "h2", "h2"], slow)
    assert len(problems) == 1 and "visit 2 loaded" in problems[0], problems
    assert line.judge_visits("socks5_no_udp", ["h2", "h2", "h2"], [900, 900 + line.LOAD_MARGIN_MS, 310]) == []
    assert line.judge_visits("socks5_no_udp", ["h2", "h2", "h2"], [900, 300, 900 + line.LOAD_MARGIN_MS + 1])
    # where UDP is free the loads are not judged (an h3 handshake has its own cost)
    assert line.judge_visits("direct", ["h2", "h3", "h3"], slow) == []


def test_a_series_of_the_wrong_length_is_an_error():
    assert line.judge_visits("direct", ["h2", "h3"], [900, 300])
    assert line.judge_visits("direct", ["h2", "h3", "h3"], [900, 300])
