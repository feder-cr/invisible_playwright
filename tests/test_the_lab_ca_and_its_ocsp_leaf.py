"""The lab CA of the OCSP case of the egress gate, held to what it says with no browser.

``_lab_pki.py`` makes a CA, a leaf whose Authority Information Access names an OCSP URL, the TLS server and the responder behind them, and puts the CA in
a profile with the engine's xpcshell. Here each part is shown on a known input: the leaf carries the URL it was given (by a name and by an IP), a client
that trusts the CA verifies the page and a client that does not refuses it (the negative control the e2e relies on), the responder records what it was
asked and answers 503, and the xpcshell step passes only when the script says the root is trusted (a script that returned and left the root untrusted
fails, whatever its exit code).
"""
from __future__ import annotations

import http.client
import socket
import ssl
import sys

import pytest

import _lab_pki as pki

pytestmark = pytest.mark.unit


def _client_context(ca: "pki.LabCa") -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(ca.ca_pem))
    context.check_hostname = True
    return context


def test_a_leaf_carries_the_ocsp_url_it_was_given_by_a_name_and_by_an_ip(tmp_path):
    ca = pki.LabCa(tmp_path)
    by_name, _ = ca.leaf("name", ["tls-name-3fa91c.lab.test"], ["127.0.0.1"], "http://ocsp-3fa91c.lab.test:4000/")
    by_ip, _ = ca.leaf("ip", ["tls-ip-3fa91c.lab.test"], ["127.0.0.1"], "http://192.0.2.77:4001/")
    bare, _ = ca.leaf("bare", [], ["127.0.0.1"], None)
    assert pki.aia_ocsp_urls(by_name) == ["http://ocsp-3fa91c.lab.test:4000/"]
    assert pki.aia_ocsp_urls(by_ip) == ["http://192.0.2.77:4001/"]
    assert pki.aia_ocsp_urls(bare) == []


def test_a_client_that_trusts_the_lab_ca_verifies_the_page_and_one_that_does_not_refuses_it(tmp_path):
    trusted, stranger = pki.LabCa(tmp_path, "lab trusted CA"), pki.LabCa(tmp_path, "lab stranger CA")
    leaf, key = trusted.leaf("page", ["tls-name-3fa91c.lab.test"], ["127.0.0.1"], None)
    server = pki.TlsPage(leaf, key, "egress-ocsp-ok")
    try:
        # verified by the IP in the SAN, as the control page of the e2e is loaded
        connection = http.client.HTTPSConnection("127.0.0.1", server.port, context=_client_context(trusted), timeout=10)
        connection.request("GET", "/")
        answer = connection.getresponse()
        assert answer.status == 200 and b"egress-ocsp-ok" in answer.read()
        connection.close()
        # the issuer the profile does not hold: the chain fails, the known-bad input of the negative control
        with pytest.raises(ssl.SSLCertVerificationError):
            refused = http.client.HTTPSConnection("127.0.0.1", server.port, context=_client_context(stranger), timeout=10)
            refused.request("GET", "/")
        assert server.handshakes, "the server never saw a handshake: the negative control would prove nothing"
    finally:
        server.close()


def test_the_leaf_of_a_name_is_verified_by_that_name_only_with_the_matching_ca(tmp_path):
    ca = pki.LabCa(tmp_path)
    leaf, key = ca.leaf("page", ["tls-name-3fa91c.lab.test"], [], None)
    server = pki.TlsPage(leaf, key, "x")
    try:
        context = _client_context(ca)
        with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname="tls-name-3fa91c.lab.test") as tls:
                assert tls.getpeercert()["subjectAltName"] == (("DNS", "tls-name-3fa91c.lab.test"),)
        with pytest.raises(ssl.SSLCertVerificationError):
            with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
                with context.wrap_socket(raw, server_hostname="another-name.lab.test"):
                    pass
    finally:
        server.close()


def test_the_responder_records_what_it_was_asked_and_answers_503():
    responder = pki.OcspResponder("127.0.0.1")
    try:
        connection = http.client.HTTPConnection("127.0.0.1", responder.port, timeout=10)
        connection.request("POST", "/", body=b"x" * 87, headers={"Content-Type": "application/ocsp-request"})
        answer = connection.getresponse()
        answer.read()
        assert answer.status == 503
        connection.request("POST", "/", body=b"y", headers={"Content-Type": "text/plain"})
        connection.getresponse().read()
        connection.close()
        assert [(r.method, r.content_type, r.length) for r in responder.requests] == [
            ("POST", "application/ocsp-request", 87), ("POST", "text/plain", 1)]
        assert [r.length for r in responder.asked] == [87]          # a request that is not OCSP is recorded but is not an OCSP request
        assert responder.url_by_address() == "http://127.0.0.1:%d/" % responder.port
    finally:
        responder.close()


def test_a_silent_responder_has_no_requests():
    responder = pki.OcspResponder("127.0.0.1")
    try:
        assert responder.requests == [] and responder.asked == []
    finally:
        responder.close()


def test_the_plain_xpcshell_is_tried_first():
    commands = pki.xpcshell_commands(sys.executable)
    assert commands[0][0] == "plain" and commands[0][1][1:] == ["-xpcshell"]
    assert len(commands) == 2 and "-g" in commands[1][1]


@pytest.mark.parametrize("result,expected", [
    ({"ok": True, "trusted_ssl": True}, True),
    ({"ok": True, "trusted_ssl": False}, False),           # the call returned and the root is not trusted: not a pass
    ({"ok": True, "trusted_error": "NS_ERROR_FAILURE"}, False),
    ({"ok": False, "error": "x", "trusted_ssl": True}, False),
    ({}, False),
    (None, False),
])
def test_only_a_root_the_database_says_is_trusted_is_a_pass(result, expected):
    assert pki.trusted_by_the_script(result) is expected


_FAKE_SHELL = """
import json, os, sys
body = json.loads(os.environ["FAKE_BODY"])
body["seen"] = {"profile": os.environ["LAB_PROFILE"], "cert": os.environ["LAB_CERT_B64"], "script": sys.argv[1]}
open(os.environ["LAB_RESULT"], "w", encoding="utf-8").write(json.dumps(body))
print("JavaScript error: add_ca.js, line 25: uncaught exception: 2147500037")
sys.exit(0)
"""


def _fake_shell(monkeypatch, tmp_path, body):
    script = tmp_path / "fake_shell.py"
    script.write_text(_FAKE_SHELL, encoding="utf-8")
    monkeypatch.setattr(pki, "xpcshell_commands", lambda binary: [("fake", [sys.executable, str(script)])])
    monkeypatch.setenv("FAKE_BODY", __import__("json").dumps(body))


def test_the_profile_gets_the_ca_through_the_shell_and_its_noise_is_not_a_failure(monkeypatch, tmp_path):
    _fake_shell(monkeypatch, tmp_path, {"ok": True, "trusted_ssl": True})
    profile = tmp_path / "profile"
    evidence = pki.add_ca_to_profile(sys.executable, profile, "QUJD", tmp_path / "work")
    assert profile.is_dir()
    shown = " ".join(evidence)
    assert "xpcshell [fake] exit=0" in shown and '"profile": ' in shown and '"cert": "QUJD"' in shown
    assert "uncaught exception: 2147500037" in shown        # the harmless line is kept in the evidence, never a verdict
    assert (tmp_path / "work" / "add_ca.js").read_text(encoding="utf-8") == pki.XPCSHELL_JS


def test_a_shell_that_returned_and_left_the_root_untrusted_fails_with_its_evidence(monkeypatch, tmp_path):
    _fake_shell(monkeypatch, tmp_path, {"ok": True, "trusted_ssl": False})
    with pytest.raises(RuntimeError, match="could not trust the lab CA") as failed:
        pki.add_ca_to_profile(sys.executable, tmp_path / "profile", "QUJD", tmp_path / "work")
    assert "xpcshell [fake] exit=0" in str(failed.value)


def test_the_script_asks_the_database_whether_the_root_is_trusted():
    for needle in ('addCertFromBase64(Services.env.get("LAB_CERT_B64"), "C,,")', "isCertTrusted(", "TRUSTED_SSL", 'Services.env.get("LAB_PROFILE")'):
        assert needle in pki.XPCSHELL_JS, needle


def test_the_ocsp_prefs_ask_and_soft_fail():
    assert pki.OCSP_PREFS == {"security.OCSP.enabled": 1, "security.OCSP.require": False, "security.pki.crlite_mode": 0}
