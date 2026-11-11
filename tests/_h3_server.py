"""A local HTTPS server that speaks HTTP/3 as well, reached by its address.

The same port answers TCP (TLS, HTTP/2 by ALPN with an `Alt-Svc: h3` header, HTTP/1.1 for a client that offers nothing
else) and UDP (QUIC, HTTP/3, aioquic), with a self-signed certificate for the address it is given. The TCP side speaks HTTP/2
because that is what a real site does: the FIRST visit of a page reads `h2`, and only after the Alt-Svc it carries may a
later one read `h3` (retail Firefox 159, 15 of 15: h2, h3, h3). It records, for every QUIC connection, the address the packets
came from: a connection that comes from anywhere but the relay of a gateway on this host (127.0.0.1) did not go through
the proxy.

Needs `aioquic` and `h2` (pip install aioquic h2; the dev extra installs both).
"""
from __future__ import annotations

import asyncio
import datetime
import ipaddress
import socket
import socketserver
import ssl
import tempfile
import threading
from pathlib import Path
from typing import List


def _certificate(address: str, directory: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


#: The one document every path answers, over TCP and over QUIC.
PAGE = b"<!doctype html><meta charset=utf-8><title>h3</title><p>ok</p>"


class _TcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _TcpHandler(socketserver.BaseRequestHandler):
    """One TCP connection: the TLS handshake here (not in the accept loop), then HTTP/2 or HTTP/1.1 by the ALPN."""

    def handle(self):
        owner = self.server.owner
        try:
            self.request.settimeout(10)
            tls = owner._tls.wrap_socket(self.request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            tls.settimeout(60)
            alpn = tls.selected_alpn_protocol() or "http/1.1"
            owner.tcp_alpn.append(alpn)
            if alpn == "h2":
                self._h2(tls, owner)
            else:
                self._http1(tls, owner)
        except (ssl.SSLError, OSError):
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    @staticmethod
    def _h2(tls, owner):
        import h2.config
        import h2.connection
        import h2.events
        import h2.exceptions

        conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False, header_encoding="utf-8"))
        conn.initiate_connection()
        tls.sendall(conn.data_to_send())
        while True:
            data = tls.recv(65536)
            if not data:
                return
            try:
                events = conn.receive_data(data)
            except h2.exceptions.ProtocolError:
                tls.sendall(conn.data_to_send())
                return
            for event in events:
                if isinstance(event, h2.events.RequestReceived):
                    owner.tcp_requests.append(dict(event.headers).get(":path", ""))
                    conn.send_headers(event.stream_id, [
                        (":status", "200"), ("content-type", "text/html; charset=utf-8"),
                        ("content-length", str(len(PAGE))), ("alt-svc", owner.alt_svc),
                        ("access-control-allow-origin", "*")])
                    conn.send_data(event.stream_id, PAGE, end_stream=True)
                elif isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.ConnectionTerminated):
                    return
            out = conn.data_to_send()
            if out:
                tls.sendall(out)

    @staticmethod
    def _http1(tls, owner):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = tls.recv(4096)
            if not chunk:
                return
            head += chunk
        parts = head.split(b"\r\n", 1)[0].split(b" ")
        owner.tcp_requests.append(parts[1].decode("latin-1") if len(parts) > 1 else "")
        tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\n"
                    b"Alt-Svc: %s\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n%s"
                    % (len(PAGE), owner.alt_svc.encode(), PAGE))


class H3Server:
    """TCP+TLS (HTTP/2) and QUIC (HTTP/3) on one port of `address`."""

    def __init__(self, address: str):
        from aioquic.asyncio import serve
        from aioquic.asyncio.protocol import QuicConnectionProtocol
        from aioquic.h3.connection import H3_ALPN, H3Connection
        from aioquic.h3.events import HeadersReceived
        from aioquic.quic.configuration import QuicConfiguration
        from aioquic.quic.events import ProtocolNegotiated

        self.address = address
        #: The address each QUIC connection came from.
        self.quic_peers: List[str] = []
        #: The paths of the requests that were answered over HTTP/3, and over TCP.
        self.h3_requests: List[str] = []
        self.tcp_requests: List[str] = []
        #: The ALPN each TCP connection negotiated ("h2", or "http/1.1").
        self.tcp_alpn: List[str] = []
        self._dir = tempfile.TemporaryDirectory()
        cert, key = _certificate(address, Path(self._dir.name))

        server = self

        # One port for both: the TCP port first, then the same number for UDP.
        for _ in range(50):
            tcp = _TcpServer((address, 0), _TcpHandler)
            port = tcp.server_address[1]
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.bind((address, port))
                probe.close()
                break
            except OSError:
                probe.close()
                tcp.server_close()
        else:
            raise RuntimeError("no port free for both TCP and UDP")
        self.port = port
        #: What the TCP answer says about HTTP/3: the same port, for an hour.
        self.alt_svc = 'h3=":%d"; ma=3600' % port
        self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._tls.load_cert_chain(str(cert), str(key))
        self._tls.set_alpn_protocols(["h2", "http/1.1"])
        tcp.owner = self
        self._tcp = tcp
        threading.Thread(target=tcp.serve_forever, daemon=True).start()

        class Protocol(QuicConnectionProtocol):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._http = None

            def quic_event_received(self, event):
                if isinstance(event, ProtocolNegotiated):
                    self._http = H3Connection(self._quic)
                    server.quic_peers.append(self._quic._network_paths[0].addr[0])
                if self._http is not None:
                    for http_event in self._http.handle_event(event):
                        if isinstance(http_event, HeadersReceived):
                            headers = dict(http_event.headers)
                            server.h3_requests.append(headers.get(b":path", b"").decode())
                            self._http.send_headers(http_event.stream_id, [
                                (b":status", b"200"), (b"content-type", b"text/html; charset=utf-8"),
                                (b"access-control-allow-origin", b"*")])
                            self._http.send_data(http_event.stream_id, PAGE, end_stream=True)
                            self.transmit()

        configuration = QuicConfiguration(is_client=False, alpn_protocols=H3_ALPN)
        configuration.load_cert_chain(str(cert), str(key))
        self._loop = asyncio.new_event_loop()
        started = threading.Event()

        def run():
            asyncio.set_event_loop(self._loop)
            self._quic = self._loop.run_until_complete(
                serve(address, port, configuration=configuration, create_protocol=Protocol))
            started.set()
            self._loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        if not started.wait(10):
            raise RuntimeError("the QUIC server did not start")

    @property
    def url(self) -> str:
        return "https://%s:%d/" % (self.address, self.port)

    def close(self) -> None:
        self._tcp.shutdown()
        self._tcp.server_close()
        self._quic.close()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._dir.cleanup()

    def __enter__(self) -> "H3Server":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
