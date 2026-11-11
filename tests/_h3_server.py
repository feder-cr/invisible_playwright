"""A local HTTPS server that speaks HTTP/3 as well, reached by its address.

The same port answers TCP (TLS, HTTP/1.1, with an `Alt-Svc: h3` header) and UDP (QUIC, HTTP/3, aioquic), with a self-signed
certificate for the address it is given. It records, for every QUIC connection, the address the packets came from: a
connection that comes from anywhere but the relay of a gateway on this host (127.0.0.1) did not go through the proxy.

Needs `aioquic` (pip install aioquic); a test that wants it imports it with `pytest.importorskip`.
"""
from __future__ import annotations

import asyncio
import datetime
import http.server
import ipaddress
import socket
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


class H3Server:
    """TCP+TLS and QUIC on one port of `address`."""

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
        self._dir = tempfile.TemporaryDirectory()
        cert, key = _certificate(address, Path(self._dir.name))

        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = 5

            def do_GET(self):
                server.tcp_requests.append(self.path)
                body = b"tcp ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Alt-Svc", 'h3=":%d"; ma=3600' % server.port)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        # One port for both: the TCP port first, then the same number for UDP.
        for _ in range(50):
            tcp = http.server.ThreadingHTTPServer((address, 0), Handler)
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
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        context.set_alpn_protocols(["http/1.1"])
        tcp.socket = context.wrap_socket(tcp.socket, server_side=True)
        tcp.daemon_threads = True
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
                                (b":status", b"200"), (b"content-type", b"text/plain"),
                                (b"access-control-allow-origin", b"*")])
                            self._http.send_data(http_event.stream_id, b"h3 ok", end_stream=True)
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
