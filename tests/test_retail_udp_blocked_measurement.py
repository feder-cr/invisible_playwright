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
import h3_fallback as h3  # noqa: E402
import verify_udp_block as vub  # noqa: E402
import webrtc_gather as wg  # noqa: E402

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
