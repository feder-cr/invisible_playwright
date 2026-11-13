"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of windows-flakes-noreader-hunt.yml.

Usage: python scripts/hunt_noreader_summary.py <label> <evidence dir>

Per arm (old, new) and per process it reads evidence/netidle_<arm>_<round>_<proc>.log (HUNT-ROW json lines of scripts/hunt_netidle.py),
evidence/pipe_<arm>_<round>_<proc>.txt (the time, seconds, the core's pipe reader read a chunk with the /landed request) and
evidence/lc_<arm>_<round>_<proc>.jsonl (the lifecycle's events, Network.requestWillBeSent among them). For every row that has a /landed
request on the server it takes the nearest pipe stamp and then the nearest lifecycle stamp of /landed, and prints markdown:
  - per arm the misses (not on /landed when goto returned) split into "defect" (the server got /landed BEFORE goto returned) and
    "not yet asked" (the page had not asked for /landed yet), with server->pipe and pipe->lifecycle in ms for each defect miss;
  - per arm p50 / p95 / max of server->pipe and pipe->lifecycle over every row of every round.
"""
import glob
import json
import os
import sys

ARMS = ("old", "new")
NEAR = 3.0  # seconds: a stamp farther than this from the request it is matched to is not its stamp


def read_rows(log):
    rows = []
    with open(log, errors="replace") as f:
        for line in f:
            if line.startswith("HUNT-ROW "):
                try:
                    rows.append(json.loads(line[len("HUNT-ROW "):]))
                except ValueError:
                    pass
    return rows


def read_pipe(path):
    out = []
    if os.path.exists(path):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    out.append(float(line.strip()))
                except ValueError:
                    pass
    return out


def read_lc(path):
    """Times of the lifecycle's Network.requestWillBeSent events of /landed."""
    out = []
    if os.path.exists(path):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if str(r.get("ev", "")).endswith("requestWillBeSent") and "/landed" in str(r.get("url") or ""):
                    out.append(r["t"])
    return out


def nearest(stamps, at):
    best = None
    for s in stamps:
        if abs(s - at) <= NEAR and (best is None or abs(s - at) < abs(best - at)):
            best = s
    return best


def pct(vals, q):
    v = sorted(vals)
    if not v:
        return None
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def fmt(x):
    return "-" if x is None else "%.1f" % x


def collect(ev, arm):
    """Every row of the arm, with s2p (server -> pipe) and p2l (pipe -> lifecycle) in ms, None when a stamp is missing, and its miss kind."""
    out = []
    dead = 0
    logs = sorted(glob.glob(os.path.join(ev, "netidle_%s_*.log" % arm)))
    for log in logs:
        rows = read_rows(log)
        if not rows:
            dead += 1
        tag = os.path.basename(log)[len("netidle_"):-len(".log")]
        pipes = read_pipe(os.path.join(ev, "pipe_%s.txt" % tag))
        lcs = read_lc(os.path.join(ev, "lc_%s.jsonl" % tag))
        for r in rows:
            srv = r.get("landed_t")
            r["tag"] = tag
            r["s2p"] = r["p2l"] = None
            if srv is not None:
                p = nearest(pipes, srv)
                if p is not None:
                    r["s2p"] = (p - srv) * 1000
                    l = nearest(lcs, p)
                    if l is not None:
                        r["p2l"] = (l - p) * 1000
            r["miss"] = not r["landed_at_return"]
            r["kind"] = None
            if r["miss"]:
                r["kind"] = "defect" if (srv is not None and srv < r["t1"]) else "not yet asked"
            out.append(r)
    return out, dead, len(logs)


def netidle(ev):
    data = {arm: collect(ev, arm) for arm in ARMS}
    print("### network idle rows: misses (not on /landed when goto returned) out of the rows made")
    print()
    print("A miss is a **defect** when the server got /landed before goto returned, **not yet asked** when the page had not asked for it yet.")
    print()
    print("| arm | misses | defect | not yet asked | rows | goto errors | processes with no row | misses by path |")
    print("|---|---|---|---|---|---|---|---|")
    for arm in ARMS:
        rows, dead, nlogs = data[arm]
        miss = [r for r in rows if r["miss"]]
        by = {}
        for r in miss:
            by[r["path"]] = by.get(r["path"], 0) + 1
        print("| %s | %d | %d | %d | %d | %d | %d of %d | %s |" % (
            arm, len(miss), sum(r["kind"] == "defect" for r in miss), sum(r["kind"] == "not yet asked" for r in miss), len(rows),
            sum(r["error"] is not None for r in rows), dead, nlogs, ", ".join("%s %d" % kv for kv in sorted(by.items())) or "-"))
    print()
    print("Expected 1200 rows per arm (4 processes x 15 rounds x 20 rows).")

    print()
    print("### defect misses: server -> pipe and pipe -> lifecycle, ms")
    for arm in ARMS:
        defects = [r for r in data[arm][0] if r["kind"] == "defect"]
        print()
        print("**%s**: %d defect misses" % (arm, len(defects)))
        if defects:
            print()
            print("| process | path | goto ms | server -> pipe | pipe -> lifecycle |")
            print("|---|---|---|---|---|")
            for r in defects:
                print("| %s | %s #%d | %d | %s | %s |" % (r["tag"], r["path"], r["round"], r["goto_ms"], fmt(r["s2p"]), fmt(r["p2l"])))

    print()
    print("### server -> pipe and pipe -> lifecycle over every row of every round, ms")
    print()
    print("| arm | step | rows with both stamps | p50 | p95 | max |")
    print("|---|---|---|---|---|---|")
    for arm in ARMS:
        rows = data[arm][0]
        for name, key in (("server -> pipe", "s2p"), ("pipe -> lifecycle", "p2l")):
            vals = [r[key] for r in rows if r[key] is not None]
            print("| %s | %s | %d | %s | %s | %s |" % (arm, name, len(vals), fmt(pct(vals, 0.5)), fmt(pct(vals, 0.95)), fmt(max(vals) if vals else None)))
    print()
    print("Rows without a /landed request on the server, or whose pipe or lifecycle stamp is missing, are left out of the percentiles.")


if __name__ == "__main__":
    label, evidence = sys.argv[1], sys.argv[2]
    print("## %s" % label)
    print()
    netidle(evidence)
