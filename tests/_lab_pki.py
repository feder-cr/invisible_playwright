"""A lab certificate authority the ENGINE trusts, leaves that name an OCSP responder, and the servers that stand behind them (no pytest in here).

Why. A revocation check is asked only for a chain that builds to a trusted root: with an issuer the engine does not trust the handshake fails
first and no OCSP request is ever made, so a page served with ``ignore_https_errors`` (which switches every check off) says nothing about the
road of an OCSP request. The lab therefore makes a CA, puts it in the NSS database of the profile BEFORE the launch, and serves a leaf signed
by it whose Authority Information Access names an OCSP URL (by a NAME, or by an IP).

How the CA gets into the profile. The engine's own ``-xpcshell`` (the same code on every system, no OS store, no root) runs
``x509certdb.addCertFromBase64(pem, "C,,")`` on the profile directory the session is then launched with (``profile_dir=``); first shown on a
runner of each system (run 43574494194): the page loads trusted with no ``ignore_https_errors`` and a listener at the OCSP URL gets
``POST application/ocsp-request``. The prefs the session needs for it are ``OCSP_PREFS``.

The script prints ``JavaScript error: ... uncaught exception: 2147500037`` for its own lines 25 and 26 (and, on Linux, for a line of
``nsAsyncShutdown.sys.mjs``): an observer of ``profile-do-change`` / ``profile-after-change`` throws NS_ERROR_FAILURE in a shell with no application.
It changes nothing: the verdict is the result file the script writes after ``addCertFromBase64`` returned, and the trust read back with
``isCertTrusted``, never the exit code or the text.
"""
from __future__ import annotations

import base64
import datetime
import ipaddress
import json
import os
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from _servers import LocalServer

#: ``security.pki.crlite_mode`` 0 takes CRLite out of the answer (the lab's issuer is in no filter anyway); ``security.OCSP.enabled`` 1 asks;
#: ``require`` false makes a missing answer a soft failure, so the page still loads and the test reads the road, not the page.
OCSP_PREFS = {"security.OCSP.enabled": 1, "security.OCSP.require": False, "security.pki.crlite_mode": 0}

XPCSHELL_JS = r"""
"use strict";
const result = Services.env.get("LAB_RESULT");
const out = { steps: [] };
let done = false;
function finish(ok) {
  out.ok = ok;
  IOUtils.writeUTF8(result, JSON.stringify(out)).then(() => { done = true; }, e => { dump("write failed " + e + "\n"); done = true; });
}
try {
  const profd = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  profd.initWithPath(Services.env.get("LAB_PROFILE"));
  const provider = {
    getFile(prop, persistent) {
      persistent.value = true;
      if (prop == "ProfD" || prop == "ProfLD" || prop == "ProfDS" || prop == "UpdRootD") {
        return profd.clone();
      }
      throw Cr.NS_ERROR_FAILURE;
    },
    QueryInterface: ChromeUtils.generateQI(["nsIDirectoryServiceProvider"]),
  };
  Services.dirsvc.QueryInterface(Ci.nsIDirectoryService).registerProvider(provider);
  out.steps.push("profile provider registered");
  Services.obs.notifyObservers(null, "profile-do-change", "xpcshell-do-get-profile");
  Services.obs.notifyObservers(null, "profile-after-change", "xpcshell-do-get-profile");
  out.steps.push("profile notified");
  const certdb = Cc["@mozilla.org/security/x509certdb;1"].getService(Ci.nsIX509CertDB);
  out.steps.push("x509certdb service obtained");
  const cert = certdb.addCertFromBase64(Services.env.get("LAB_CERT_B64"), "C,,");
  out.steps.push("addCertFromBase64 returned");
  try { out.subject = cert.subjectName; } catch (e) { out.subject_error = String(e); }
  try {
    out.trusted_ssl = certdb.isCertTrusted(cert, Ci.nsIX509Cert.CA_CERT, Ci.nsIX509CertDB.TRUSTED_SSL);
  } catch (e) { out.trusted_error = String(e); }
  finish(true);
} catch (e) {
  out.error = String(e);
  out.stack = e && e.stack ? String(e.stack) : "";
  finish(false);
}
Services.tm.spinEventLoopUntil("lab-ca-xpcshell", () => done);
quit(0);
"""


class LabCa:
    """A CA made for one test: the key stays here, the certificate goes to the profile (``ca_b64``) or nowhere (an untrusted issuer)."""

    def __init__(self, directory: Path, label: str = "lab egress CA"):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self.directory = Path(directory)
        self.label = label
        now = datetime.datetime.now(datetime.timezone.utc)
        self._key = ec.generate_private_key(ec.SECP256R1())
        self._name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
        self._cert = (x509.CertificateBuilder().subject_name(self._name).issuer_name(self._name).public_key(self._key.public_key())
                      .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                      .not_valid_after(now + datetime.timedelta(days=90))
                      .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                      .add_extension(_usage(digital_signature=True, key_cert_sign=True, crl_sign=True), critical=True)
                      .add_extension(x509.SubjectKeyIdentifier.from_public_key(self._key.public_key()), critical=False)
                      .sign(self._key, hashes.SHA256()))
        self.ca_pem = self.directory / ("%s.pem" % label.replace(" ", "-"))
        self.ca_pem.write_bytes(self._cert.public_bytes(serialization.Encoding.PEM))
        #: What ``addCertFromBase64`` takes: the DER, base64.
        self.ca_b64 = base64.b64encode(self._cert.public_bytes(serialization.Encoding.DER)).decode("ascii")

    def leaf(self, tag: str, dns_names: Sequence[str], ip_addresses: Sequence[str], ocsp_url: Optional[str]) -> Tuple[Path, Path]:
        """A leaf for ``dns_names`` and ``ip_addresses`` whose AIA names ``ocsp_url`` (none: no AIA). Returns (certificate, key), both PEM."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, NameOID

        now = datetime.datetime.now(datetime.timezone.utc)
        key = ec.generate_private_key(ec.SECP256R1())
        san = [x509.DNSName(n) for n in dns_names] + [x509.IPAddress(ipaddress.ip_address(a)) for a in ip_addresses]
        builder = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (list(dns_names) + list(ip_addresses))[0])]))
                   .issuer_name(self._name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=60))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(_usage(digital_signature=True), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .add_extension(x509.SubjectAlternativeName(san), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._key.public_key()), critical=False))
        if ocsp_url:
            builder = builder.add_extension(
                x509.AuthorityInformationAccess([x509.AccessDescription(AuthorityInformationAccessOID.OCSP,
                                                                        x509.UniformResourceIdentifier(ocsp_url))]), critical=False)
        leaf = builder.sign(self._key, hashes.SHA256())
        cert_path, key_path = self.directory / ("leaf-%s.pem" % tag), self.directory / ("leaf-%s-key.pem" % tag)
        cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return cert_path, key_path


def _usage(**given):
    from cryptography import x509

    base = dict(digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False)
    base.update(given)
    return x509.KeyUsage(**base)


def aia_ocsp_urls(leaf_pem: Path) -> List[str]:
    """The OCSP URLs a leaf names (for the unit test of the lab and for the message of a failed case)."""
    from cryptography import x509
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    cert = x509.load_pem_x509_certificate(Path(leaf_pem).read_bytes())
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.OCSP]


class OcspRequest(NamedTuple):
    method: str
    path: str
    content_type: str
    length: int


class OcspResponder:
    """What the OCSP URL of a leaf points at: it records every request and answers 503 (a soft failure), so the page still loads.

    Bound at ``address`` (an address of this host that no proxy of the tests forwards to): a request that arrives here came from the browser, direct."""

    def __init__(self, address: str = "127.0.0.1"):
        import http.server

        outer = self
        self.address = address
        self.requests: List[OcspRequest] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                outer.requests.append(OcspRequest(self.command, self.path[:80], self.headers.get("Content-Type", ""), length))
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _answer  # noqa: N815

            def log_message(self, *args):
                pass

        self._server = LocalServer((address, 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def asked(self) -> List[OcspRequest]:
        """The requests that are OCSP ones (``POST application/ocsp-request``, or a GET of the base64 form)."""
        return [r for r in self.requests if r.content_type == "application/ocsp-request" or r.method == "GET"]

    def url_by_address(self) -> str:
        return "http://%s:%d/" % (self.address, self.port)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class TlsPage:
    """A page over TLS at ``address`` with the given leaf. The handshake is made in the thread of the connection, not in the accept loop."""

    def __init__(self, leaf: Path, key: Path, body: str, address: str = "127.0.0.1"):
        import http.server

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(leaf), str(key))
        self.handshakes: List[str] = []
        self.body = body
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self):
                try:
                    self.request = context.wrap_socket(self.request, server_side=True)
                    outer.handshakes.append("ok")
                except (ssl.SSLError, OSError) as exc:
                    outer.handshakes.append("failed: %s" % exc)
                    raise
                super().setup()

            def do_GET(self):  # noqa: N802
                payload = ("<!doctype html><title>lab</title><body>%s</body>" % outer.body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        class Server(LocalServer):
            def handle_error(self, request, client_address):
                pass

        self._server = Server((address, 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ------------------------------------------------------------------------------------------------------- the profile
def xpcshell_commands(binary: str) -> List[Tuple[str, List[str]]]:
    """The ways to start the engine's xpcshell, the plain one first (the one that worked on both systems)."""
    exe = str(Path(binary).resolve())
    gre = str(Path(exe).parent)
    return [("plain", [exe, "-xpcshell"]), ("with -g and -a", [exe, "-xpcshell", "-g", gre, "-a", gre])]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        import psutil

        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except Exception:  # noqa: BLE001 - the process may be gone already
        proc.kill()


def _run(argv: List[str], env: Dict[str, str], timeout: float, cwd: str) -> Tuple[Optional[int], str]:
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        return None, "TIMEOUT after %ss\n%s" % (timeout, out.decode("utf-8", "replace"))


def trusted_by_the_script(result: Optional[dict]) -> bool:
    """The verdict of one run of ``XPCSHELL_JS`` from the file it wrote: the call returned AND the NSS database says the root is trusted for TLS."""
    return bool(result) and result.get("ok") is True and result.get("trusted_ssl") is True


def add_ca_to_profile(binary: str, profile: Path, ca_b64: str, work: Path, timeout: float = 120) -> List[str]:
    """Trust ``ca_b64`` in the NSS database of ``profile`` with the engine's own xpcshell. Returns the evidence lines; raises ``RuntimeError`` with
    them when no way to start the shell ended with the root trusted. The profile directory is the one the session is launched with afterwards."""
    profile, work = Path(profile), Path(work)
    profile.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    script = work / "add_ca.js"
    script.write_text(XPCSHELL_JS, encoding="utf-8")
    evidence: List[str] = []
    for label, head in xpcshell_commands(binary):
        result_file = work / ("xpcshell-result-%s.json" % label.replace(" ", "_"))
        env = dict(os.environ, LAB_PROFILE=str(profile), LAB_CERT_B64=ca_b64, LAB_RESULT=str(result_file), MOZ_LAUNCHER_PROCESS="0",
                   MOZ_CRASHREPORTER_DISABLE="1")
        code, output = _run(head + [str(script)], env, timeout, str(Path(binary).resolve().parent))
        body = None
        if result_file.exists():
            try:
                body = json.loads(result_file.read_text(encoding="utf-8"))
            except ValueError:
                body = {"unreadable": result_file.read_text(encoding="utf-8", errors="replace")[:300]}
        evidence.append("xpcshell [%s] exit=%s result=%s" % (label, code, json.dumps(body) if body is not None else "(no result file)"))
        evidence.append("xpcshell [%s] output: %s" % (label, output.strip()[:600].replace("\n", " | ") or "(empty)"))
        if trusted_by_the_script(body):
            return evidence
    raise RuntimeError("the engine's xpcshell could not trust the lab CA in %s:\n  %s" % (profile, "\n  ".join(evidence)))
