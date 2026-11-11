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


def test_the_blocked_condition_is_proved_before_it_is_measured_and_the_rule_is_removed_on_failure():
    text = WORKFLOW.read_text(encoding="utf-8")
    cond = (SCRIPTS / "run_condition.ps1").read_text(encoding="utf-8")
    assert cond.index("verify_udp_block.py") < cond.index("webrtc_gather.py") < cond.index("h3_fallback.py")
    assert text.index("-Action enable") < text.index("-Condition blocked")
    tail = text[text.index("-Action disable") - 200:]
    assert "if: always()" in tail.split("-Action disable")[0]


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
