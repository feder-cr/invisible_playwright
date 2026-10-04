"""The Accept-Language a server reads, byte for byte, on every kind of request.

The header is read where a server reads it - the raw header line of an
HTTP/1.1 request on a local socket - not through the protocol, which reports
what the engine meant to send.
"""
from __future__ import annotations

import socket
import threading

import pytest

from invisible_playwright import InvisiblePlaywright

#: Measured on a signed retail Firefox 151.0 whose only pref is the
#: intl.accept_languages invisible_core writes for it-IT ("it-IT, it, en-US,
#: en"): navigation, image, fetch, XHR, dedicated, shared and service worker
#: requests all carry exactly this, over HTTP/1.1 and HTTP/2.
RETAIL_IT_IT = "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"

PAGE = b"""<!doctype html><title>al</title><script>
(async () => {
  await fetch('/fetch').then(r => r.text());
  await new Promise(ok => { const x = new XMLHttpRequest(); x.open('GET', '/xhr');
                            x.onloadend = ok; x.send(); });
  await new Promise(ok => { const w = new Worker('/worker.js'); w.onmessage = ok; });
  document.title = 'done';
})();
</script>"""
WORKER = b"fetch('/worker-fetch').then(r => r.text()).then(() => postMessage(1));"


def _serve(seen):
    """A server that keeps each request's path and raw Accept-Language lines."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)

    def one(conn):
        buf = b""
        try:
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                head, buf = buf.split(b"\r\n\r\n", 1)
                lines = head.split(b"\r\n")
                path = lines[0].split(b" ")[1].decode("latin-1")
                seen.append((path, [ln.decode("latin-1") for ln in lines[1:]
                                    if ln.lower().startswith(b"accept-language:")]))
                body, ctype = (PAGE, b"text/html") if path == "/" else (
                    (WORKER, b"text/javascript") if path == "/worker.js" else (b"ok", b"text/plain"))
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + ctype
                             + b"\r\nCache-Control: no-store\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\n\r\n" + body)
        except OSError:
            return
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
def test_every_request_of_a_session_carries_the_retail_accept_language(firefox_binary):
    """⛔ ONE SESSION SENT TWO DIFFERENT HEADERS. The engine seeds the
    language override with a LIST, so navigator.languages can be split from
    it, and copied that list onto the wire as it was: spaces, no q-values. A
    request without a BrowsingContext - a fetch from a worker - never saw the
    override and sent what Firefox prepares from intl.accept_languages. A
    server sees both forms from one client, and only the second is one any
    Firefox sends.

    Up to firefox-35 this was a strict expected failure; the firefox-36
    engine prepares the override list like intl.accept_languages.
    """
    seen: list = []
    srv = _serve(seen)
    url = "http://127.0.0.1:%d/" % srv.getsockname()[1]
    try:
        with InvisiblePlaywright(seed=42, locale="it-IT", binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_function("document.title === 'done'", timeout=15_000)
            languages = page.evaluate("[...navigator.languages]")
    finally:
        srv.close()
    assert languages == ["it-IT", "it", "en-US", "en"]
    by_path = {path: lines for path, lines in seen if path != "/favicon.ico"}
    assert set(by_path) >= {"/", "/fetch", "/xhr", "/worker.js", "/worker-fetch"}, by_path
    wrong = {path: lines for path, lines in by_path.items()
             if lines != ["Accept-Language: " + RETAIL_IT_IT]}
    assert not wrong, wrong
