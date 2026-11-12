r"""Fold the raw per-session files of one run into summary.json, meta.json and a short table for the job summary.

New for the Windows reference (the Linux captures were summarised by hand). Reads, under --out:
  firefox_info.json, geckodriver_info.json            written by the install scripts
  <condition>/udp_block_check.json                    proof of the condition (verify_udp_block.py)
  <condition>/udp_block_check_after.json              the same proof after the measurements
  <condition>/firewall_rule.txt                       the rule text (blocked only)
  commands.txt                                        the command lines run_condition.ps1 ran
  partition/result.json                               storage partitioning (storage_partition.py); only its answers go to meta.json
  <condition>/webrtc/local-stun/run<N>/timeline.json  WebRTC gather (webrtc_gather.py)
  <condition>/h3/<site>/run<N>.json                   HTTP/3 visits (h3_fallback.py), with the DNS evidence of the session
with <condition> in control, doh-free, blocked, doh-blocked (the two doh-* have no WebRTC gather). Writes --out/summary.json and --out/meta.json, prints the markdown to stdout and
appends it to $GITHUB_STEP_SUMMARY when set. It reports what was measured; it never judges it, and its exit code is 0.
"""
import argparse, collections, datetime, json, os, platform, statistics, sys
from pathlib import Path

CONDITIONS = ["control", "doh-free", "blocked", "doh-blocked"]


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def stats(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return {"n": len(vals), "min": round(vals[0], 1), "median": round(statistics.median(vals), 1), "max": round(vals[-1], 1)}


def fmt_stats(s):
    return "-" if not s else "%s / %s / %s" % (s["min"], s["median"], s["max"])


def webrtc_summary(cond_dir):
    runs = sorted((cond_dir / "webrtc" / "local-stun").glob("run*/timeline.json"), key=lambda p: int(p.parent.name[3:]))
    sessions = [load(p) for p in runs]
    sessions = [s for s in sessions if s]
    by_type = collections.Counter()
    by_type_proto = collections.Counter()
    per_session = []
    err_times = collections.defaultdict(list)
    err_urls = collections.defaultdict(collections.Counter)
    ends = []
    for s in sessions:
        c = collections.Counter(x["type"] for x in s["candidates"])
        by_type.update(c)
        by_type_proto.update("%s/%s" % (x["type"], x["protocol"]) for x in s["candidates"])
        per_session.append({"host": c.get("host", 0), "srflx": c.get("srflx", 0), "relay": c.get("relay", 0),
                            "errors": [{"code": e["code"], "url": e["url"], "since_pc_created_ms": e["since_pc_created_ms"]}
                                       for e in s["icecandidateerrors"]],
                            "gathering_end_since_pc_created_ms": s.get("gathering_end_since_pc_created_ms")})
        for e in s["icecandidateerrors"]:
            err_times[e["code"]].append(e["since_pc_created_ms"])
            err_urls[e["code"]][e["url"]] += 1
        ends.append(s.get("gathering_end_since_pc_created_ms"))
    return {
        "sessions": len(sessions),
        "candidates_by_type": dict(by_type),
        "candidates_by_type_protocol": dict(by_type_proto),
        "errors_by_code": {str(code): {"count": len(t), "since_pc_created_ms": stats(t), "urls": dict(err_urls[code])}
                           for code, t in sorted(err_times.items(), key=lambda kv: str(kv[0]))},
        "gathering_end_since_pc_created_ms": stats(ends),
        "gathering_never_ended": sum(1 for e in ends if e is None),
        "per_session": per_session,
    }


def h3_summary(cond_dir):
    sites = {}
    for site_dir in sorted(p for p in (cond_dir / "h3").glob("*") if p.is_dir()):
        runs = [load(p) for p in sorted(site_dir.glob("run*.json"), key=lambda p: int(p.stem[3:]))]
        runs = [r for r in runs if r]
        seqs = collections.Counter()
        per_visit = collections.defaultdict(collections.Counter)
        walls = collections.defaultdict(list)
        loads = collections.defaultdict(list)
        errors = 0
        doh_ok = 0
        modes = collections.Counter()
        for r in runs:
            doh_ok += 1 if r.get("doh_proven") else 0
            modes[str(((r.get("dns_evidence") or {}).get("prefs") or {}).get("network.trr.mode"))] += 1
            proto = []
            for v in r["visits"]:
                p = (v.get("nav") or {}).get("nextHopProtocol") or ("error" if v.get("error") else "none")
                proto.append(p)
                per_visit[v["visit"]][p] += 1
                walls[v["visit"]].append(v.get("wall_ms"))
                le = (v.get("nav") or {}).get("loadEventEnd")
                loads[v["visit"]].append(le if le else None)
                errors += 1 if v.get("error") else 0
            seqs[",".join(proto)] += 1
        sites[site_dir.name] = {
            "sessions": len(runs), "sequences": dict(seqs),
            "protocol_by_visit": {str(k): dict(v) for k, v in sorted(per_visit.items())},
            "wall_ms_by_visit": {str(k): stats(v) for k, v in sorted(walls.items())},
            "load_event_end_ms_by_visit": {str(k): stats(v) for k, v in sorted(loads.items())},
            "doh_proven_sessions": doh_ok, "trr_mode_seen": dict(modes),
            "visit_errors": errors,
        }
    total_seq = collections.Counter()
    for s in sites.values():
        total_seq.update(s["sequences"])
    return {"sites": sites, "sequences_all_sites": dict(total_seq)}


def turn_summary(cond_dir):
    """The TURN target (webrtc_gather.py --target turn): by form of the TURN address, how many host candidates, how many TURN-over-TCP
    errors and when, whether the gathering ended. None when the condition did not run it."""
    rows = load(cond_dir / "webrtc-turn" / "summary_runs.json")
    if not rows:
        return None
    out = {}
    for form in sorted({r.get("turn_form") for r in rows}):
        mine = [r for r in rows if r.get("turn_form") == form]
        first_tcp = [r["tcp_error_ms_after_pc"][0] for r in mine if r.get("tcp_error_ms_after_pc")]
        second_tcp = [r["tcp_error_ms_after_pc"][1] for r in mine if len(r.get("tcp_error_ms_after_pc") or []) > 1]
        ends = [r.get("gathering_end_since_pc_created_ms") for r in mine]
        out[form] = {
            "sessions": len(mine),
            "host_candidates": sorted(r["host"] for r in mine),
            "host_names": sorted(r.get("host_names", 0) for r in mine),
            "tcp_errors": sorted(r.get("tcp_errors", 0) for r in mine),
            "udp_errors": sorted(r.get("udp_errors", 0) for r in mine),
            "first_tcp_error_ms": stats(first_tcp), "second_tcp_error_ms": stats(second_tcp),
            "srflx": sorted(r["srflx"] for r in mine), "relay": sorted(r["relay"] for r in mine),
            "gathering_end_since_pc_created_ms": stats([e for e in ends if e is not None]),
            "gathering_never_ended": sum(1 for e in ends if e is None),
        }
    return out


def markdown(summary):
    L = ["## Retail Firefox on Windows, outbound UDP free vs blocked, native DNS vs DNS over HTTPS", ""]
    for cond in CONDITIONS:
        c = summary["conditions"].get(cond)
        if not c:
            continue
        proof = c.get("proof") or {}
        L.append("### %s" % cond)
        L.append("")
        L.append("- condition proof: %s%s" % ("ok" if proof.get("ok") else "MISSING OR FAILED",
                                              ", block mode: %s" % proof["block_mode"] if proof.get("block_mode") else ""))
        w = c["webrtc"]
        if w["sessions"]:
            L.append("")
            L.append("WebRTC gather, %d sessions" % w["sessions"])
            L.append("")
            L.append("| candidates by type/protocol (all sessions) | gather end after pc (ms) min / median / max |")
            L.append("|---|---|")
            L.append("| %s | %s |" % (", ".join("%s %d" % kv for kv in sorted(w["candidates_by_type_protocol"].items())) or "none",
                                      fmt_stats(w["gathering_end_since_pc_created_ms"])))
            L.append("")
            L.append("| icecandidateerror code | count | time after pc (ms) min / median / max |")
            L.append("|---|---|---|")
            for code, e in w["errors_by_code"].items():
                L.append("| %s | %d | %s |" % (code, e["count"], fmt_stats(e["since_pc_created_ms"])))
            if not w["errors_by_code"]:
                L.append("| none | 0 | - |")
        if c.get("turn"):
            L.append("")
            L.append("TURN over UDP + TURN over TCP on one server of ours (UDP answers, the TCP port refuses), by form of its address")
            L.append("")
            L.append("| form | sessions | host candidates | host names | TCP errors | UDP errors | first TCP error (ms) | second TCP error (ms) | gather end (ms) | never ended |")
            L.append("|---|---|---|---|---|---|---|---|---|---|")
            for form, t in c["turn"].items():
                L.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %d |" % (
                    form, t["sessions"], t["host_candidates"], t["host_names"], t["tcp_errors"], t["udp_errors"],
                    fmt_stats(t["first_tcp_error_ms"]), fmt_stats(t["second_tcp_error_ms"]),
                    fmt_stats(t["gathering_end_since_pc_created_ms"]), t["gathering_never_ended"]))
        h = c["h3"]
        L.append("")
        L.append("HTTP/3 visits (nextHopProtocol of visit 1, 2, 3 in one session; times of visit 1 in ms, min / median / max)")
        L.append("")
        L.append("| site | sessions | sequence: count | visit 1 load event end | visit 1 wall (driver.get) | DoH shown in use |")
        L.append("|---|---|---|---|---|---|")
        for site, s in h["sites"].items():
            L.append("| %s | %d | %s | %s | %s | %d of %d (trr.mode seen: %s) |" % (
                site, s["sessions"], "; ".join("%s: %d" % kv for kv in sorted(s["sequences"].items())),
                fmt_stats((s["load_event_end_ms_by_visit"] or {}).get("1")), fmt_stats((s["wall_ms_by_visit"] or {}).get("1")),
                s["doh_proven_sessions"], s["sessions"], ", ".join("%s: %d" % kv for kv in sorted(s["trr_mode_seen"].items())) or "-"))
        L.append("| all | %s | %s | | | |" % (sum(s["sessions"] for s in h["sites"].values()),
                                              "; ".join("%s: %d" % kv for kv in sorted(h["sequences_all_sites"].items()))))
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    summary = {"conditions": {}}
    commands = {}
    firewall = None
    for cond in CONDITIONS:
        d = out / cond
        if not d.exists():
            continue
        summary["conditions"][cond] = {"proof": load(d / "udp_block_check.json"), "webrtc": webrtc_summary(d), "h3": h3_summary(d),
                                       "turn": turn_summary(d)}
        for kind in ("webrtc", "webrtc-turn", "h3"):
            m = load(d / kind / "meta.json")
            if m:
                commands["%s/%s" % (cond, kind)] = m.get("command")
        if (d / "firewall_rule.txt").exists():
            firewall = (d / "firewall_rule.txt").read_text(encoding="utf-8-sig")
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    fx = load(out / "firefox_info.json") or {}
    gd = load(out / "geckodriver_info.json") or {}
    meta = {
        "what": "retail (Mozilla-built) Firefox on Windows, outbound UDP free (control) and blocked by a Windows Firewall rule, each with native DNS and with DNS over HTTPS forced (doh-free, doh-blocked: HTTP/3 visits only)",
        "doh": {"network.trr.mode": 3, "network.trr.uri": "https://mozilla.cloudflare-dns.com/dns-query",
                "network.trr.bootstrapAddr": "162.159.61.4",
                "provider_declared_in": "invisible_core src/invisible_core/prefs.py (network.trr.uri, network.trr.bootstrapAddr)",
                "evidence": "dns_evidence in every h3/<site>/run<N>.json of every condition: trr prefs, nsIDNSService current TRR mode, "
                            "a fresh lookup of the site host with its record's TRR flag, about:networking#dns cache entries with trr"},
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "firefox": fx,
        "geckodriver": gd,
        "runner": {"image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
                   "runner_os": os.environ.get("RUNNER_OS"), "runner_arch": os.environ.get("RUNNER_ARCH"),
                   "machine": platform.platform()},
        "python": {"version": platform.python_version(), "pip_freeze_file": "pip_freeze.txt"},
        "workflow": {"run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                     "run_url": "%s/%s/actions/runs/%s" % (os.environ.get("GITHUB_SERVER_URL", ""), os.environ.get("GITHUB_REPOSITORY", ""),
                                                           os.environ.get("GITHUB_RUN_ID", "")) if os.environ.get("GITHUB_RUN_ID") else None,
                     "sha": os.environ.get("GITHUB_SHA"), "ref": os.environ.get("GITHUB_REF"), "event": os.environ.get("GITHUB_EVENT_NAME")},
        "firewall_rule": firewall,
        "command_lines": [ln for ln in (out / "commands.txt").read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
        if (out / "commands.txt").exists() else [],
        "command_lines_as_run_by_the_scripts": commands,
        "proof_of_condition_after": {c: (load(out / c / "udp_block_check_after.json") or {}).get("ok") for c in summary["conditions"]},
        "storage_partition": (load(out / "partition" / "result.json") or {}).get("answers"),
        "proof_of_condition": {c: (summary["conditions"][c]["proof"] or {}).get("ok") for c in summary["conditions"]},
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    md = markdown(summary)
    print(md)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    sys.exit(main())
