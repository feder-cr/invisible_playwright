"""The sequence with times of one repetition of the cold-pool hunt (scripts/cold_pool_hunt.py).

    python scripts/cold_pool_report.py hunt/rep-007

Merges, on the wall clock, the engine's log (MOZ_LOG with `timestamp`, modules SOCKS, SOCKS-UDP, signaling) and the gateway's events
(tests/_hunt_plugin.py): the first SOCKS5 connection, every UDP ASSOCIATE the gateway sees and every one the engine says is OK, the
PeerConnection, every UDP layer pushed onto a socket, and for each push how many associations were ready (OK before it, minus the pushes
before it, per engine process): 0 is a cold pool, the push had to open a new association. "Warm received" is not logged by the engine
(nsSOCKSUDPIOLayerWarm has no log line): the associations that follow the first connection are its trace.
"""
import datetime
import json
import re
import sys
from pathlib import Path

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) UTC - \[(\w+) (\d+):[^\]]*\]: (\w)/(\S+) (.*)$")


def epoch(stamp):
    return datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=datetime.timezone.utc).timestamp()


def engine_events(path):
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        stamp, process, pid, level, module, text = m.groups()
        out.append({"t": epoch(stamp), "process": process, "pid": int(pid), "level": level, "module": module, "text": text})
    return out


def gateway_events(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def classify(e):
    t = e["text"]
    if e["module"] == "SOCKS-UDP":
        if "UDP ASSOCIATE OK" in t:
            return "associate_ok"
        if "layer pushed onto fd" in t:
            return "pushed"
        if e["level"] == "E" or "failed" in t or "rejected" in t:
            return "udp_error"
    if e["module"] == "SOCKS" and "trying proxy server" in t:
        return "socks_tcp_first_try"
    if e["module"] == "SOCKS" and e["level"] == "E":
        return "socks_error"
    if e["module"] == "signaling" and "PeerConnectionImpl constructor" in t:
        return "peerconnection"
    return None


def report(folder):
    folder = Path(folder)
    log = engine_events(folder / "moz.log")
    log += [e for p in sorted(folder.glob("moz.log.*")) for e in engine_events(p)]   # child-process files, when the engine writes them
    gw = gateway_events(folder / "gateway.jsonl")
    rows = []   # (t, who, what, pid)
    for e in log:
        kind = classify(e)
        if kind:
            rows.append((e["t"], "engine %s %d" % (e["process"], e["pid"]), kind + ("  " + e["text"][:70] if kind.endswith("error") else ""), e["pid"], kind))
    for e in gw:
        rows.append((e["t"], "gateway", e["kind"] + ("  %s:%s" % (e.get("host"), e.get("port")) if e["kind"] == "connect" else ""), None, e["kind"]))
    rows.sort()
    result = {}
    try:
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    lines = ["=== %s: rc %s, %s s" % (folder.name, result.get("rc"), result.get("seconds"))]
    pytest_log = (folder / "pytest.log")
    if pytest_log.exists():
        for l in pytest_log.read_text(encoding="utf-8", errors="replace").splitlines():
            if l.startswith("[page first]") or l.startswith("E   ") and "srflx" in l:
                lines.append("    " + l[:260])
    if not rows:
        lines.append("    (no event: the engine wrote no log line and the gateway saw nothing)")
        return "\n".join(lines)
    t0 = next((r[0] for r in rows if r[4] in ("connect", "socks_tcp_first_try")), rows[0][0])
    ready = {}      # pid -> associations OK minus pushes so far
    cold = []
    for t, who, what, pid, kind in rows:
        note = ""
        if kind == "associate_ok":
            ready[pid] = ready.get(pid, 0) + 1
        elif kind == "pushed":
            had = ready.get(pid, 0)
            note = "   <- %d ready association(s) at this push%s" % (had, "  COLD" if had < 1 else "")
            if had < 1:
                cold.append(t - t0)
            ready[pid] = max(0, had - 1)
        lines.append("  %+8.3f s  %-24s %s%s" % (t - t0, who, what, note))
    first = lambda k: next((r[0] - t0 for r in rows if r[4] == k), None)
    fmt = lambda v: "-" if v is None else "%.3f s" % v
    lines.append("  summary: first SOCKS5 connection %s (gateway) | first association OK %s | PeerConnection %s | first push %s | cold pushes: %s"
                 % (fmt(first("connect")), fmt(first("associate_ok")), fmt(first("peerconnection")), fmt(first("pushed")),
                    ", ".join("%.3f s" % c for c in cold) or "none"))
    return "\n".join(lines)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(report(arg))
