"""Hunt (never merged): the two first-run questions of the egress gate (studio-gate-di-classe.md, 3.2-3.3).

(A) Is the engine's own log written under the Windows sandbox, and does it hold the positive control of the native-lookup sink: a context
    with NO proxy that fetches ``http://native-<nonce>.lab.test/`` must leave ``Calling getaddrinfo for host [native-<nonce>`` in the
    ``moz-<pid>.log*`` files (MOZ_LOG=sync,nsHostResolver:5, MOZ_LOG_FILE=<dir>/moz-%PID.log). Reported: which process files carry it.
(B) Can the engine's own ``-xpcshell`` run ``x509certdb.addCertFromBase64(pem, "C,,")`` on a fresh profile directory; and then does a page
    served with a leaf signed by that lab CA (AIA OCSP URL http://127.0.0.1:<port>/) load WITHOUT ``ignore_https_errors``, with the local
    OCSP listener receiving a request (``security.OCSP.enabled=1``; the crlite pref used is printed).

Prints one ``HUNT A PASS|FAIL`` line and one ``HUNT B PASS|FAIL`` line, then the evidence. Always exits 0 when both questions were asked:
the answer is in the lines, not in the exit code. No pytest in here.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))

import _egress_sink as sink  # noqa: E402  (the repository's own sink: the same names, the same regex)
from _servers import LocalServer  # noqa: E402

#: The crlite pref of (B). 0 = CRLite off: the leaf's issuer is not in any filter anyway, and this takes the question out of the answer.
CRLITE_PREF = "security.pki.crlite_mode"
CRLITE_VALUE = 0
SETTLE_S = 4.0


def log(message: str) -> None:
    print(message, flush=True)


# ------------------------------------------------------------------------------------------------ launching
def _hermetic_session() -> None:
    """The launch asks nothing of the internet: the same declarations as ``_hermetic_session`` of the DoH e2e."""
    from invisible_core import SessionGeo, decide_session_locale
    import invisible_playwright.launcher as launcher

    geo = SessionGeo("America/New_York", "203.0.113.7", latitude=40.7128, longitude=-74.0060, locale=decide_session_locale("en-US"))
    launcher.prepare_session_geo = lambda tz, proxy, locale, **_kw: geo
    launcher.InvisiblePlaywright._assert_uscita_invariata = lambda self: None
    import invisible_core._geo as core_geo

    core_geo.discover_egress_ip = lambda *_a, **_k: geo.egress_ip


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        import psutil

        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except Exception:
        proc.kill()


def _run(argv, env, timeout, cwd=None):
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        return None, "TIMEOUT after %ss\n%s" % (timeout, out.decode("utf-8", "replace"))


# ------------------------------------------------------------------------------------------------ (A)
def question_a(binary: str, work: Path) -> tuple:
    from invisible_playwright import InvisiblePlaywright

    logs = work / "moz"
    logs.mkdir(parents=True)
    nonce = sink.new_nonce()
    name = "native-%s.lab.test" % nonce
    os.environ["MOZ_LOG"] = sink.MOZ_LOG
    os.environ["MOZ_LOG_FILE"] = str(logs / sink.LOG_FILE_NAME)
    navigation = "not tried"
    try:
        with InvisiblePlaywright(seed=42, binary_path=binary, headless=True) as browser:
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto("http://%s/" % name, timeout=20000)
                navigation = "loaded (unexpected: the name is on no zone)"
            except Exception as exc:  # the name resolves nowhere: the error is the expected one
                navigation = "error (expected): %s" % str(exc).splitlines()[0][:120]
            time.sleep(SETTLE_S)
            context.close()
    finally:
        os.environ.pop("MOZ_LOG", None)
        os.environ.pop("MOZ_LOG_FILE", None)

    files = sink.log_files(logs)
    rows, carrying = [], []
    needle = "Calling getaddrinfo for host [native-%s" % nonce
    for path in files:
        text = Path(path).read_bytes().decode("utf-8", "replace")
        started = text.count(sink._STARTED)
        hits = text.count(needle)
        resolving = len(re.findall(r"Resolving host \[%s\]" % re.escape(name), text))
        rows.append("%s: bytes=%d started=%d getaddrinfo_hits=%d resolving_hits=%d" % (os.path.basename(path), len(text), started, hits, resolving))
        if hits:
            carrying.append(os.path.basename(path))
    native = sink.engine_log_native_lookups(logs, nonce)
    written = bool(files)
    alive = sink.engine_log_is_alive(logs)
    ok = written and alive and bool(native)
    summary = ("log written=%s (%d file(s)); resolver started in some process=%s; getaddrinfo line for %s=%s; carried by: %s"
               % (written, len(files), alive, name, bool(native), ", ".join(carrying) or "none"))
    evidence = ["navigation: " + navigation] + rows
    return ok, summary, evidence


# ------------------------------------------------------------------------------------------------ (B)
XPCSHELL_JS = r"""
"use strict";
const result = Services.env.get("HUNT_RESULT");
const out = { steps: [] };
let done = false;
function finish(ok) {
  out.ok = ok;
  IOUtils.writeUTF8(result, JSON.stringify(out)).then(() => { done = true; }, e => { dump("write failed " + e + "\n"); done = true; });
}
try {
  const profd = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  profd.initWithPath(Services.env.get("HUNT_PROFILE"));
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
  const cert = certdb.addCertFromBase64(Services.env.get("HUNT_CERT_B64"), "C,,");
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
Services.tm.spinEventLoopUntil("hunt-xpcshell", () => done);
quit(0);
"""


def make_lab_pki(work: Path, ocsp_port: int) -> dict:
    """A lab CA and a leaf for 127.0.0.1 / localhost whose AIA names the OCSP listener, like ``make_certificate`` of ``_doh_servers``."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, NameOID

    def usage(**kw):
        base = dict(digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
                    key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False)
        base.update(kw)
        return x509.KeyUsage(**base)

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "lab egress hunt CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=90))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(usage(digital_signature=True, key_cert_sign=True, crl_sign=True), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    ocsp_url = x509.UniformResourceIdentifier("http://127.0.0.1:%d/" % ocsp_port)
    leaf = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(ca_name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=60))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(usage(digital_signature=True), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(AuthorityInformationAccessOID.OCSP, ocsp_url)]),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    paths = {"ca": work / "lab-ca.pem", "leaf": work / "lab-leaf.pem", "key": work / "lab-leaf-key.pem"}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["leaf"].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
    paths["ca_b64"] = base64.b64encode(ca.public_bytes(serialization.Encoding.DER)).decode("ascii")
    return paths


class OcspListener:
    """What the OCSP URL of the leaf points at: it records every request and answers 503 (soft-fail), so the page still loads."""

    def __init__(self):
        import http.server

        outer = self
        self.requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append((self.command, self.path[:80], self.headers.get("Content-Type", ""), len(body)))
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _answer  # noqa: N815

            def log_message(self, *args):
                pass

        self._server = LocalServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


class TlsPage:
    """A page over TLS at 127.0.0.1 with the lab leaf; the handshake is made in the thread of the connection, not in the accept loop."""

    def __init__(self, leaf: Path, key: Path):
        import http.server

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(leaf), str(key))
        self.handshakes = []
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
                body = b"<!doctype html><title>lab</title><body>egress-firstrun-ok</body>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Server(LocalServer):
            def handle_error(self, request, client_address):
                pass

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def _xpcshell_variants(binary: str):
    exe = str(Path(binary).resolve())
    gre = str(Path(exe).parent)
    return [
        ("plain", [exe, "-xpcshell"]),
        ("with -g and -a", [exe, "-xpcshell", "-g", gre, "-a", gre]),
    ]


def add_ca_with_xpcshell(binary: str, profile: Path, ca_b64: str, work: Path) -> tuple:
    script = work / "add_ca.js"
    script.write_text(XPCSHELL_JS, encoding="utf-8")
    evidence, outcome = [], None
    for label, head in _xpcshell_variants(binary):
        result = work / ("xpcshell-result-%s.json" % label.replace(" ", "_"))
        env = dict(os.environ, HUNT_PROFILE=str(profile), HUNT_CERT_B64=ca_b64, HUNT_RESULT=str(result), MOZ_LAUNCHER_PROCESS="0",
                   MOZ_CRASHREPORTER_DISABLE="1")
        code, output = _run(head + [str(script)], env, timeout=120, cwd=str(Path(binary).resolve().parent))
        body = None
        if result.exists():
            try:
                body = json.loads(result.read_text(encoding="utf-8"))
            except ValueError:
                body = {"unreadable": result.read_text(encoding="utf-8", errors="replace")[:300]}
        evidence.append("xpcshell [%s] exit=%s result=%s" % (label, code, json.dumps(body) if body is not None else "(no result file)"))
        evidence.append("xpcshell [%s] output: %s" % (label, output.strip()[:600].replace("\n", " | ") or "(empty)"))
        if body and body.get("ok"):
            outcome = label
            break
    db = sorted(p.name for p in profile.glob("cert*.db")) + sorted(p.name for p in profile.glob("key*.db"))
    evidence.append("profile NSS files after xpcshell: %s" % (db or "none"))
    return outcome, evidence


def question_b(binary: str, work: Path) -> tuple:
    from invisible_playwright import InvisiblePlaywright

    evidence = []
    ocsp = OcspListener()
    pki = make_lab_pki(work, ocsp.port)
    page_server = TlsPage(pki["leaf"], pki["key"])
    profile = work / "profile"
    profile.mkdir()
    try:
        variant, more = add_ca_with_xpcshell(binary, profile, pki["ca_b64"], work)
        evidence += more
        if variant is None:
            return False, "xpcshell could not add the lab CA (see evidence)", evidence
        prefs = {"security.OCSP.enabled": 1, "security.OCSP.require": False, CRLITE_PREF: CRLITE_VALUE}
        loaded, detail = False, "not tried"
        url = "https://127.0.0.1:%d/" % page_server.port
        with InvisiblePlaywright(seed=42, binary_path=binary, headless=True, profile_dir=profile, extra_prefs=prefs) as context:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                response = page.goto(url, timeout=30000)
                text = page.evaluate("() => document.body.innerText")
                loaded = bool(response) and response.status == 200 and "egress-firstrun-ok" in text
                detail = "status=%s text=%r" % (response.status if response else None, text[:40])
            except Exception as exc:
                detail = "goto error: %s" % str(exc).splitlines()[0][:200]
            deadline = time.monotonic() + 8
            while not ocsp.requests and time.monotonic() < deadline:
                time.sleep(0.1)
        evidence.append("page %s without ignore_https_errors: %s; server handshakes: %s" % (url, detail, page_server.handshakes[:5]))
        evidence.append("OCSP listener 127.0.0.1:%d requests: %s" % (ocsp.port, ocsp.requests[:5] or "none"))
        evidence.append("prefs used: %s" % json.dumps(prefs))
        ok = loaded and bool(ocsp.requests)
        summary = ("xpcshell addCertFromBase64 ok (%s); page loaded trusted=%s; OCSP requests=%d; %s=%s"
                   % (variant, loaded, len(ocsp.requests), CRLITE_PREF, CRLITE_VALUE))
        return ok, summary, evidence
    finally:
        page_server.close()
        ocsp.close()


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", nargs="?", default=os.environ.get("INVPW_BINARY_PATH") or os.environ.get("FF"))
    parser.add_argument("--work", default="evidence/egress-firstrun")
    args = parser.parse_args()
    if not args.binary or not Path(args.binary).exists():
        log("HUNT: no engine binary (%r)" % args.binary)
        return 2
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    log("platform=%s python=%s binary=%s" % (sys.platform, sys.version.split()[0], args.binary))
    _hermetic_session()
    summary = {}
    for label, question in (("A", question_a), ("B", question_b)):
        sub = work / label.lower()
        sub.mkdir(exist_ok=True)
        try:
            ok, text, evidence = question(args.binary, sub)
        except BaseException:
            ok, text, evidence = False, "raised: " + traceback.format_exc().strip().splitlines()[-1], [traceback.format_exc()]
        log("HUNT %s %s: %s" % (label, "PASS" if ok else "FAIL", text))
        for line in evidence:
            log("  " + line)
        summary[label] = {"pass": ok, "summary": text, "evidence": evidence}
    (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as handle:
            handle.write("### egress first run (%s)\n" % sys.platform)
            for label in "AB":
                handle.write("- **%s %s**: %s\n" % (label, "PASS" if summary[label]["pass"] else "FAIL", summary[label]["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
