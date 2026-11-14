"""The retail-Firefox-on-Windows UDP measurement: its workflow is a measurement, not a gate, and its helpers are exact.

The workflow itself runs on GitHub (windows-latest), so here only what can be checked without a browser or a network:
the shape of the workflow (pinned actions, read-only token, the triggers, a proof of condition before the numbers), the
proof's verdict logic, and the arithmetic of the summary the job prints.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "retail_udp_blocked"
WORKFLOW = ROOT / ".github" / "workflows" / "retail-udp-blocked.yml"
sys.path.insert(0, str(SCRIPTS))

import report  # noqa: E402
import storage_partition as sp  # noqa: E402
import h3_fallback as h3  # noqa: E402
import verify_udp_block as vub  # noqa: E402
import webrtc_gather as wg  # noqa: E402
import turn_server as ts  # noqa: E402

pytestmark = pytest.mark.unit


def test_every_action_is_pinned_by_commit_sha():
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", WORKFLOW.read_text(encoding="utf-8"), re.MULTILINE)
    assert uses, "the workflow uses no action at all"
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_the_workflow_runs_on_a_pr_that_touches_it_and_can_be_reused():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^on:\n  pull_request:\n(?:    .*\n)*?    paths:\n      - '\.github/workflows/retail-udp-blocked\.yml'\n"
                     r"      - 'scripts/retail_udp_blocked/\*\*'\n  workflow_dispatch:", text, re.MULTILINE)
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE)
    assert "cancel-in-progress: true" in text and "runs-on: windows-latest" in text
    assert re.search(r"timeout-minutes: \d+", text)


def test_the_four_conditions_run_in_order_and_the_rule_is_on_only_for_the_blocked_two():
    text = WORKFLOW.read_text(encoding="utf-8")
    order = [text.index(k) for k in ("-Name control", "-Name doh-free", "-Action enable", "-Name blocked", "-Name doh-blocked")]
    assert order == sorted(order), order
    assert text.index("-Action disable") > order[-1]
    tail = text[text.index("-Action disable") - 200:]
    assert "if: always()" in tail.split("-Action disable")[0]
    assert re.search(r"GECKODRIVER_WIN64_ZIP_SHA256: [0-9a-f]{64}$", text, re.MULTILINE), "the geckodriver zip must be pinned"


def test_each_condition_is_proved_before_it_is_measured():
    cond = (SCRIPTS / "run_condition.ps1").read_text(encoding="utf-8")
    first = cond.index("Invoke-Logged 'verify_udp_block.py'")
    assert first < cond.index("Invoke-Logged 'webrtc_gather.py'") < cond.index("Invoke-Logged 'h3_fallback.py'")
    assert cond.rindex("Invoke-Logged 'verify_udp_block.py'") > cond.index("Invoke-Logged 'h3_fallback.py'")


def test_the_doh_conditions_force_mode_3_with_the_products_provider_and_require_the_proof():
    cond = (SCRIPTS / "run_condition.ps1").read_text(encoding="utf-8")
    for need in ("network.trr.mode=3", "network.trr.uri=https://mozilla.cloudflare-dns.com/dns-query",
                 "network.trr.bootstrapAddr=162.159.61.4", "--require-doh"):
        assert need in cond, need
    assert "if (-not $doh)" in cond  # the WebRTC gather is not part of the DoH conditions


def test_the_measurement_never_fails_on_a_measured_value():
    src = (SCRIPTS / "report.py").read_text(encoding="utf-8")
    assert "sys.exit(main())" in src and "raise SystemExit" not in src


def test_the_stun_datagram_is_a_real_binding_request():
    req, txid = vub.stun_binding_request()
    assert len(req) == 20 and req[:2] == b"\x00\x01" and req[2:4] == b"\x00\x00"
    assert req[4:8] == bytes.fromhex("2112a442") and req[8:] == txid and len(txid) == 12


def _ok_probes(**over):
    out = {"udp_dns_8.8.8.8_53": "reply", "udp_stun_stun.l.google.com_19302": "reply",
           "udp_stun_stun.cloudflare.com_3478": "reply", "tcp_8.8.8.8_53": "connected in 10 ms",
           "tcp_cloudflare-quic.com_443": "connected in 10 ms", "getaddrinfo_cloudflare-quic.com": ["192.0.2.1"]}
    out.update(over)
    return out


def test_control_needs_a_stun_answer_and_blocked_needs_none():
    assert vub.judge(_ok_probes(), "control") == []
    dead = _ok_probes(**{"udp_stun_stun.l.google.com_19302": "no reply in 3 s (silently dropped)",
                         "udp_stun_stun.cloudflare.com_3478": "error: [WinError 10013]"})
    assert vub.judge(dead, "control")
    assert vub.judge(dead, "blocked") == []
    assert vub.block_mode(dead) == "send error"
    assert vub.judge(_ok_probes(), "blocked")


def test_the_condition_fails_when_tcp_or_names_do_not_work():
    assert vub.judge(_ok_probes(**{"tcp_8.8.8.8_53": "error: timed out"}), "control")
    assert vub.judge(_ok_probes(**{"getaddrinfo_cloudflare-quic.com": "error: x"}), "control")


def test_the_gather_table_keeps_the_linux_fields():
    events = [
        {"ev": "pc_created", "t": 50.0},
        {"ev": "icecandidate", "t": 60.0, "type": "host", "protocol": "udp", "address": "a.local", "port": 1,
         "line": "candidate:0 1 UDP 2122252543 a.local 1 typ host", "foundation": "candidate:0"},
        {"ev": "icecandidateerror", "t": 11150.0, "url": "stun:x:1", "code": 701, "text": "t"},
        {"ev": "gathering_end", "t": 11160.0},
    ]
    rows, errors, _, end = wg.candidate_table(events)
    assert rows[0]["address_kind"] == "mDNS" and rows[0]["since_pc_created_ms"] == 10.0 and rows[0]["component"] == "1"
    assert errors[0]["code"] == 701 and errors[0]["since_pc_created_ms"] == 11100.0 and end == 11160.0


def test_the_report_counts_what_was_measured(tmp_path):
    for cond, protos, err in (("control", ["h2", "h3", "h3"], None), ("blocked", ["h2", "h2", "h2"], 701)):
        for n, t in ((1, 11100.0), (2, 11200.0), (3, 11300.0)):
            d = tmp_path / cond / "webrtc" / "local-stun" / f"run{n}"
            d.mkdir(parents=True)
            tl = {"candidates": [{"type": "host", "protocol": "udp"}, {"type": "host", "protocol": "tcp"}],
                  "icecandidateerrors": [{"code": err, "url": "stun:x", "since_pc_created_ms": t}] if err else [],
                  "gathering_end_since_pc_created_ms": t}
            (d / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
        h = tmp_path / cond / "h3" / "google"
        h.mkdir(parents=True)
        (h / "run1.json").write_text(json.dumps({"visits": [
            {"visit": i + 1, "wall_ms": 100 * (i + 1), "nav": {"nextHopProtocol": p}} for i, p in enumerate(protos)]}),
            encoding="utf-8")
        (tmp_path / cond / "udp_block_check.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    w = report.webrtc_summary(tmp_path / "blocked")
    assert w["sessions"] == 3 and w["candidates_by_type"] == {"host": 6}
    assert w["errors_by_code"]["701"]["count"] == 3
    assert w["errors_by_code"]["701"]["since_pc_created_ms"] == {"n": 3, "min": 11100.0, "median": 11200.0, "max": 11300.0}
    assert report.h3_summary(tmp_path / "control")["sequences_all_sites"] == {"h2,h3,h3": 1}
    assert report.h3_summary(tmp_path / "blocked")["sequences_all_sites"] == {"h2,h2,h2": 1}
    report.main(["--out", str(tmp_path)])
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["proof_of_condition"] == {"control": True, "blocked": True}
    assert (tmp_path / "summary.json").exists()


def test_doh_is_proven_only_by_the_browsers_own_state():
    ev = {"prefs": {"network.trr.mode": 3}, "dns_service": {"currentTrrMode": 3},
          "lookup": {"isTRR": False}, "cache_entries": [{"hostname": "www.example.org", "trr": True}]}
    assert h3.doh_proven(ev, "www.example.org")[0]
    assert h3.doh_proven({**ev, "cache_entries": []}, "www.example.org")[0] is False
    assert h3.doh_proven({**ev, "cache_entries": [], "lookup": {"isTRR": True}}, "www.example.org")[0]
    assert h3.doh_proven({**ev, "prefs": {"network.trr.mode": 0}}, "www.example.org")[0] is False
    assert h3.doh_proven({**ev, "dns_service": {"currentTrrMode": 5}}, "www.example.org")[0] is False
    assert h3.doh_proven({"error": "NoSuchContextError"}, "www.example.org")[0] is False
    assert h3.doh_proven(None, "www.example.org")[0] is False


def test_an_extra_pref_is_typed_like_json_and_falls_back_to_a_string():
    assert h3.parse_pref("network.trr.mode=3") == ("network.trr.mode", 3)
    assert h3.parse_pref("a.b=true") == ("a.b", True)
    assert h3.parse_pref("network.trr.uri=https://x.example/dns-query") == ("network.trr.uri", "https://x.example/dns-query")
    assert h3.parse_pref("network.trr.bootstrapAddr=203.0.113.5") == ("network.trr.bootstrapAddr", "203.0.113.5")
    with pytest.raises(SystemExit):
        h3.parse_pref("no-equals")


def test_the_summary_shows_the_load_time_of_visit_1_per_condition_and_site(tmp_path):
    for cond, loads in (("control", (400, 500, 900)), ("doh-blocked", (1400, 1500, 1900))):
        h = tmp_path / cond / "h3" / "google"
        h.mkdir(parents=True)
        for n, le in enumerate(loads, 1):
            (h / f"run{n}.json").write_text(json.dumps({"doh_proven": cond.startswith("doh"), "dns_evidence": {
                "prefs": {"network.trr.mode": 3 if cond.startswith("doh") else 0}}, "visits": [
                {"visit": 1, "wall_ms": le + 10, "nav": {"nextHopProtocol": "h3", "loadEventEnd": le}},
                {"visit": 2, "wall_ms": 50, "nav": {"nextHopProtocol": "h3", "loadEventEnd": 40}}]}), encoding="utf-8")
    s = report.h3_summary(tmp_path / "doh-blocked")["sites"]["google"]
    assert s["load_event_end_ms_by_visit"]["1"] == {"n": 3, "min": 1400.0, "median": 1500.0, "max": 1900.0}
    assert s["wall_ms_by_visit"]["1"]["max"] == 1910.0 and s["doh_proven_sessions"] == 3 and s["trr_mode_seen"] == {"3": 3}
    assert report.h3_summary(tmp_path / "control")["sites"]["google"]["doh_proven_sessions"] == 0
    report.main(["--out", str(tmp_path)])
    md = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert list(md["conditions"]) == ["control", "doh-blocked"]


def test_the_cookie_string_is_parsed_like_document_cookie():
    assert sp.parse_cookie_string("a=1; b=2;c=") == {"a": "1", "b": "2", "c": ""}
    assert sp.parse_cookie_string("") == {} and sp.parse_cookie_string(None) == {}


def test_a_report_is_read_for_what_each_writer_stored():
    report_ = {"cookie": "hdr_frame=1; js_frame=1", "localStorage": {"ls_frame": "1"}, "sessionStorage": {}}
    assert sp.sees(report_, "frame") == {"cookie_header": True, "cookie_js": True, "localStorage": True, "sessionStorage": False}
    assert not any(sp.sees(report_, "top").values())
    assert sp.sees(None, "frame") == {"cookie_header": False, "cookie_js": False, "localStorage": False, "sessionStorage": False}


def test_the_yes_no_leaves_session_storage_out():
    assert sp.answer({"cookie_header": True, "cookie_js": True, "localStorage": True, "sessionStorage": False}) == "yes"
    assert sp.answer({"cookie_header": False, "cookie_js": False, "localStorage": False, "sessionStorage": True}) == "no"
    assert sp.answer({"cookie_header": True, "cookie_js": False, "localStorage": True, "sessionStorage": False}) == "partly: cookie_header, localStorage"


def test_the_repetitions_agree_only_when_everything_measured_is_equal():
    def rep(top, frame, mode=5):
        return {"top_sees_frame": top, "frame_sees_top": frame, "frame_sees_own": {"cookie_header": True},
                "prefs_values": {"network.cookie.cookieBehavior": mode}}
    same = sp.agreement([rep({"a": 1}, {"a": 0}) for _ in range(5)])
    assert same["all_agree"] and same["distinct_outcomes"] == 1 and same["outcome_counts"][0]["count"] == 5
    mixed = sp.agreement([rep({"a": 1}, {"a": 0})] * 4 + [rep({"a": 1}, {"a": 0}, mode=4)])
    assert not mixed["all_agree"] and mixed["distinct_outcomes"] == 2
    broken = sp.agreement([rep({"a": 1}, {"a": 0}), {"error": "x"}])
    assert not broken["all_agree"] and broken["ok"] == 1


def test_the_two_sites_write_by_header_and_by_script_and_only_in_write_mode():
    status, headers, body = sp.route("/frame.html?mode=write", 1234)
    assert status == 200 and ("Set-Cookie", "hdr_frame=1; Path=/") in headers
    assert "document.cookie = 'js_' + role" in body and "localStorage.setItem('ls_' + role" in body
    assert ("Set-Cookie", "hdr_top=1; Path=/") in sp.route("/top.html?mode=write", 1234)[1]
    assert not [h for h in sp.route("/frame.html?mode=read", 1234)[1] if h[0] == "Set-Cookie"]
    a_body = sp.route("/a.html?mode=read", 1234)[2]
    assert 'src="http://localhost:1234/frame.html?mode=read"' in a_body and "addEventListener('message'" in a_body
    assert sp.route("/nope", 1234)[0] == 404 and sp.route("/a.html?mode=x", 1234)[0] == 400


def test_the_local_server_answers_on_loopback_with_the_header_cookie():
    import urllib.request
    servers, port = sp.serve()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/top.html?mode=write" % port, timeout=10) as r:
            assert r.headers["Set-Cookie"] == "hdr_top=1; Path=/" and b"B top write" in r.read()
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


def test_the_analysis_answers_both_questions_from_the_reports():
    frame_cookie = "hdr_frame=1; js_frame=1"
    rec = {"frame_write": {"data": {"cookie": frame_cookie, "localStorage": {"ls_frame": "1"}, "sessionStorage": {"ss_frame": "1"}}},
           "top_read": {"cookie": "", "localStorage": {}, "sessionStorage": {}},
           "frame_read_after_top": {"data": {"cookie": frame_cookie, "localStorage": {"ls_frame": "1"}, "sessionStorage": {}}},
           "baseline_frame": {"data": {"cookie": "", "localStorage": {}, "sessionStorage": {}}},
           "prefs": {"named": {"network.cookie.cookieBehavior": {"present": True, "value": 5},
                               "network.cookie.cookieBehavior.optInPartitioning": {"present": False}}}}
    a = sp.analyse(rec)
    assert a["top_sees_frame_answer"] == "no" and a["frame_sees_top_answer"] == "no"
    assert a["frame_sees_own"]["localStorage"] and not a["baseline_frame_sees_anything"]
    assert a["prefs_values"] == {"network.cookie.cookieBehavior": 5, "network.cookie.cookieBehavior.optInPartitioning": None}
    rec["top_read"] = {"cookie": frame_cookie, "localStorage": {"ls_frame": "1"}, "sessionStorage": {}}
    assert sp.analyse(rec)["top_sees_frame_answer"] == "yes"


def test_the_partition_step_runs_before_the_conditions_and_does_not_judge():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index("storage_partition.py") < text.index("-Name control")
    src = (SCRIPTS / "storage_partition.py").read_text(encoding="utf-8")
    assert "if broke:" in src and "sys.exit(1)" in src and src.count("sys.exit") == 1


def _udp_roundtrip(server, packet):
    import socket
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c.settimeout(5)
    try:
        c.sendto(packet, server.address)
        return c.recvfrom(65536)[0]
    finally:
        c.close()


def test_the_turn_server_asks_for_credentials_and_signs_its_success():
    import hashlib
    import hmac
    import struct
    server = ts.TurnServer("127.0.0.1", 0)
    try:
        tid = b"\x01" * 12
        bare = struct.pack("!HHI", 0x0003, 8, ts.MAGIC) + tid + struct.pack("!HH", 0x0019, 4) + b"\x11\x00\x00\x00"
        first = _udp_roundtrip(server, bare)
        assert struct.unpack("!H", first[:2])[0] == 0x0113
        at = ts.attrs_of(first)
        assert at[0x0014] == ts.REALM.encode() and at[0x0015] == ts.NONCE.encode()
        key = hashlib.md5(("%s:%s:%s" % (ts.USERNAME, ts.REALM, ts.PASSWORD)).encode()).digest()
        body = (ts.attr(0x0019, b"\x11\x00\x00\x00") + ts.attr(0x0006, ts.USERNAME.encode()) + ts.attr(0x0014, ts.REALM.encode()) +
                ts.attr(0x0015, ts.NONCE.encode()))
        second = _udp_roundtrip(server, ts.message(0x0003, b"\x02" * 12, body, key))
        assert struct.unpack("!H", second[:2])[0] == 0x0103
        mi_at = second.index(struct.pack("!HH", 0x0008, 20))
        head = struct.pack("!H", 0x0103) + struct.pack("!H", mi_at - 20 + 24) + second[4:20]
        assert hmac.new(key, head + second[20:mi_at], hashlib.sha1).digest() == second[mi_at + 4:mi_at + 24]
        assert server.allocations and server.allocations[0][2] == ts.USERNAME
    finally:
        server.close()


def test_only_the_control_condition_runs_the_turn_target_and_the_page_has_a_turn_over_tcp():
    ps1 = (SCRIPTS / "run_condition.ps1").read_text(encoding="utf-8")
    assert re.search(r"if \(\$Name -eq 'control'\) \{[^}]*'--target', 'turn'", ps1), "the TURN target belongs to the control condition only"
    page = (SCRIPTS / "local_turn_page.html").read_text(encoding="utf-8")
    assert "transport=udp" in page and "transport=tcp" in page and "stun:" not in page.replace("// ", "").split("<script>")[1]


def test_the_report_summarises_the_turn_target_by_form_of_the_address(tmp_path):
    d = tmp_path / "control" / "webrtc-turn"
    d.mkdir(parents=True)
    rows = []
    for form, tcp in (("ip", [[300.0], [320.0]]), ("name", [[310.0, 3000.0], [330.0, 3100.0]])):
        for n, ms in enumerate(tcp, 1):
            rows.append({"target": "local-turn-" + form, "run": n, "turn_form": form, "host": 2, "srflx": 1, "relay": 1,
                         "host_names": 1, "tcp_errors": len(ms), "tcp_error_ms_after_pc": ms, "udp_errors": 0,
                         "udp_error_ms_after_pc": [], "gathering_end_since_pc_created_ms": 3200.0 if len(ms) > 1 else None})
    (d / "summary_runs.json").write_text(json.dumps(rows), encoding="utf-8")
    t = report.turn_summary(tmp_path / "control")
    assert t["ip"]["tcp_errors"] == [1, 1] and t["ip"]["second_tcp_error_ms"] is None and t["ip"]["gathering_never_ended"] == 2
    assert t["name"]["tcp_errors"] == [2, 2] and t["name"]["second_tcp_error_ms"]["median"] == 3050.0
    assert t["name"]["gathering_never_ended"] == 0
    assert report.turn_summary(tmp_path / "blocked") is None


def test_the_turn_server_goes_on_after_the_connection_reset_windows_reports_on_a_receive():
    """The first run of the TURN target answered one session of ten: after the first client closed its socket, Windows reported the ICMP
    'port unreachable' of the last reply as ConnectionResetError on the next recvfrom, and the thread of the server ended. The server must
    go on (red on the first version: the thread returned and the next Allocate got no answer)."""
    import socket
    import struct
    server = ts.TurnServer("127.0.0.1", 0)
    try:
        real = server.sock
        seen = {"raised": False}

        class Flaky:
            def __init__(self, inner):
                self.inner = inner

            def recvfrom(self, n):
                if not seen["raised"]:
                    seen["raised"] = True
                    raise ConnectionResetError(10054, "reset by the ICMP of an earlier send")
                return self.inner.recvfrom(n)

            def sendto(self, *a):
                return self.inner.sendto(*a)

            def close(self):
                return self.inner.close()

            def getsockname(self):
                return self.inner.getsockname()

        # the thread is already blocked in the real recvfrom: wake it with a datagram, then swap
        server.sock = Flaky(real)
        poke = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        poke.sendto(b"x", server.address)      # not a STUN message: ignored, and the next recvfrom is the flaky one
        poke.close()
        bare = struct.pack("!HHI", 0x0003, 8, ts.MAGIC) + b"\x03" * 12 + struct.pack("!HH", 0x0019, 4) + b"\x11\x00\x00\x00"
        answered = None
        for _ in range(20):
            try:
                answered = _udp_roundtrip(server, bare)
                break
            except OSError:
                continue
        assert answered is not None and struct.unpack("!H", answered[:2])[0] == 0x0113, "the server did not answer after the reset"
        assert seen["raised"], "the reset was never delivered"
    finally:
        server.sock = real
        server.close()
