"""`ignore_https_errors=True` against a local https page with a self-signed certificate."""
from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

TITLE = b"secure-ok"


def _self_signed(tmp_path):
    """A certificate for 127.0.0.1 that no trust store knows."""
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=7))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return cert_path, key_path


def _serve_https(cert_path, key_path, hits):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)

    def one(conn):
        try:
            conn = ctx.wrap_socket(conn, server_side=True)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            hits.append(data.split(b"\r\n", 1)[0])
            body = b"<!doctype html><title>" + TITLE + b"</title>"
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close"
                         b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        except (ssl.SSLError, OSError):
            pass
        finally:
            conn.close()

    def loop():
        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=one, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return s


@pytest.mark.e2e
def test_ignore_https_errors_loads_a_self_signed_page(firefox_binary, tmp_path):
    """⛔ THE OPTION FAILED THE CONTEXT, NOT THE PAGE. `new_context` itself
    raised `Browser.setIgnoreHTTPSErrors: ... NS_ERROR_NOT_AVAILABLE`, so a
    caller asking to ignore certificate errors got no context at all. The
    guard is the only thing in the way: with XPCSHELL_TEST_PROFILE_DIR set
    for the browser (a bench, never the product) the same context loads.

    The control arm is in the same test: without the option the page must
    NOT load, or the server's certificate was trusted for another reason and
    the first half proves nothing.

    Up to firefox-35 this was a strict expected failure; the firefox-36
    engine lets an attached Juggler pipe flip the per-context switch.
    """
    cert_path, key_path = _self_signed(tmp_path)
    hits: list = []
    srv = _serve_https(cert_path, key_path, hits)
    url = "https://127.0.0.1:%d/" % srv.getsockname()[1]
    try:
        with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                 headless=True) as browser:
            plain = browser.new_context()
            with pytest.raises(Exception):
                plain.new_page().goto(url, timeout=15_000)
            plain.close()
            assert not hits, hits

            ctx = browser.new_context(ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(url, timeout=15_000)
            assert page.title() == TITLE.decode()
    finally:
        srv.close()
    assert hits and hits[0].startswith(b"GET / "), hits
