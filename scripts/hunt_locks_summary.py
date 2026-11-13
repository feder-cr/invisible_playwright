"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of windows-flakes-locks-hunt.yml.

Usage: python scripts/hunt_locks_summary.py <label> <evidence dir> [threshold ms, default 100]

Per process of the arm `new` it reads
  evidence/netidle_new_<round>_<proc>.log    HUNT-ROW lines (the server's time of /landed, landed_t),
  evidence/pipe_new_<round>_<proc>.txt       when the core's pipe reader read the chunk that carries /landed,
  evidence/lc_new_<round>_<proc>.jsonl       when the lifecycle applied Network.requestWillBeSent of /landed,
  evidence/locks_new_<round>_<proc>.jsonl    the rows of scripts/hunt_locks_runtime.py (listeners, locks, gate, messages, totals).
For every /landed whose pipe -> lifecycle is over the threshold it cuts the interval [pipe, lifecycle] into the items below and prints markdown:
  (a) the listener calls of Connection.dispatch_event (the part of a listener that is not a lock or gate wait is its "own" time),
  (b) the wait of gated_dispatch on the events gate (with the holder it saw when it began to wait),
  (c) the waits of the reader on every lock / condition that was timed (rows of 5 ms or more),
  (d) per message, wall against CPU: wall - cpu - lock waits - gate = off the CPU (the GIL, or the OS not running the thread),
  and the rest: time outside any _deliver (buffer, split) and inside a message but outside a listener (decode).
Then the totals per item over every process, and the longest stalls of the reader whether or not they sit in a delayed interval.
The stamps and rows are lists in memory written once at process exit, so a process that did not exit normally has no stamp file.

FIRST of all, the CONTROL: evidence/control_<arm>_<round>_<proc>.log (arms old and new, run with no patcher at all) gives, per arm, the
misses of networkidle (not on /landed when goto returned) split into "defect" (the server got /landed BEFORE goto returned) and
"not yet asked" (the page had not asked for it yet), from the server times in the HUNT-ROW lines only.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt_noreader_summary as base  # noqa: E402

ARM = "new"


def read_locks(path):
    rows = []
    if os.path.exists(path):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def ov(row, a, b):
    """Seconds of the row's span [t - ms, t] inside [a, b]."""
    end = row["t"]
    start = end - row["ms"] / 1000.0
    return max(0.0, min(end, b) - max(start, a))


def cpu_of(row):
    c = row.get("cpu_cyc")
    return c if c is not None else (row.get("cpu_ts") or 0.0)


def breakdown(rows, p, l):
    """The interval [p, l] cut into items. Everything in ms."""
    iv = (l - p) * 1000.0
    out = {"interval": iv, "gate": 0.0, "lock": 0.0, "cpu": 0.0, "off": 0.0, "outside": 0.0, "msg_other": 0.0, "stamp_io": 0.0, "lc_io": 0.0,
           "locks": {}, "listeners": {}, "gates": [], "io": {}}
    in_msgs = 0.0
    for r in rows:
        k = r["k"]
        if k == "totals" or "t" not in r or r.get("ms") is None and k != "chunk":
            continue
        if k == "msg":
            o = ov(r, p, l)
            if o <= 0:
                continue
            frac = o / (r["ms"] / 1000.0)
            wall = r["ms"]
            cpu = cpu_of(r)
            off = max(0.0, wall - cpu - r["lock"] - r["gate"])
            in_msgs += o * 1000.0
            out["gate"] += frac * r["gate"]
            out["lock"] += frac * r["lock"]
            out["cpu"] += frac * min(cpu, wall)
            out["off"] += frac * off
            out["msg_other"] += frac * max(0.0, wall - r["lst"])
        elif k == "lock":
            o = ov(r, p, l) * 1000.0
            if o > 0:
                d = out["locks"].setdefault((r["op"], r["name"]), {"ms": 0.0, "n": 0, "holders": {}})
                d["ms"] += o
                d["n"] += 1
                h = "%s at %s" % (r.get("holder"), r.get("held_at"))
                d["holders"][h] = d["holders"].get(h, 0) + 1
        elif k == "gate":
            o = ov(r, p, l) * 1000.0
            if o > 0:
                out["gates"].append((o, r))
        elif k == "io":
            o = ov(r, p, l) * 1000.0
            if o > 0:
                out["io"][r["name"]] = out["io"].get(r["name"], 0.0) + o
        elif k == "chunk":
            # the stamp's own file write, which sits between the pipe read and the first message
            out["stamp_io"] += r.get("stamp_ms", 0.0)
    # listeners, with what is nested in them (a lock or gate wait or the stamp write inside the call) taken out
    nested = [r for r in rows if r["k"] in ("lock", "gate", "io") and "t" in r and r.get("ms") is not None]
    for r in rows:
        if r["k"] != "lst":
            continue
        o = ov(r, p, l)
        if o <= 0:
            continue
        a = max(r["t"] - r["ms"] / 1000.0, p)
        b = min(r["t"], l)
        inner = sum(ov(n, a, b) for n in nested if n["t"] >= a and n["t"] - n["ms"] / 1000.0 <= b) * 1000.0
        own = max(0.0, o * 1000.0 - inner)
        frac = o / (r["ms"] / 1000.0)
        d = out["listeners"].setdefault((r["name"]), {"own": 0.0, "total": 0.0, "n": 0, "cpu": 0.0, "events": {}})
        d["own"] += own
        d["total"] += o * 1000.0
        d["n"] += 1
        d["cpu"] += frac * cpu_of(r)
        d["events"][r["ev"]] = d["events"].get(r["ev"], 0) + 1
    out["outside"] = max(0.0, iv - in_msgs)
    out["lc_io"] = out["io"].get("hunt_lc_write", 0.0)
    return out


def dominant(b):
    cand = {}
    if b["gate"] > 0:
        cand["gate"] = b["gate"]
    for (op, name), d in b["locks"].items():
        cand["%s %s" % (op, name)] = d["ms"]
    for name, d in b["listeners"].items():
        cand["listener %s (own)" % name] = d["own"]
    cand["inside a message, outside a listener (decode, ends_waits)"] = max(0.0, b["msg_other"] - sum(d["total"] for d in b["listeners"].values()))
    cand["outside any message (buffer, split, stamp write)"] = b["outside"]
    name = max(cand, key=lambda k: cand[k])
    return name, cand[name]


def collect():
    out = []
    logs = sorted(glob.glob(os.path.join(EV, "netidle_%s_*.log" % ARM)))
    for log in logs:
        tag = os.path.basename(log)[len("netidle_"):-len(".log")]
        pipes = base.read_pipe(os.path.join(EV, "pipe_%s.txt" % tag))
        lcs = base.read_lc(os.path.join(EV, "lc_%s.jsonl" % tag))
        locks = read_locks(os.path.join(EV, "locks_%s.jsonl" % tag))
        out.append({"tag": tag, "rows": base.read_rows(log), "pipes": pipes, "lcs": lcs, "locks": locks})
    return out


def miss_counts(prefix, arm):
    """(rows, misses, defect, not yet asked, goto errors, processes with no row, processes, misses by path) of control_<arm>_*.log."""
    rows = []
    dead = 0
    logs = sorted(glob.glob(os.path.join(EV, "%s_%s_*.log" % (prefix, arm))))
    for log in logs:
        got = base.read_rows(log)
        if not got:
            dead += 1
        rows.extend(got)
    miss = [r for r in rows if not r["landed_at_return"]]
    defect = [r for r in miss if r.get("landed_t") is not None and r["landed_t"] < r["t1"]]
    by = {}
    for r in miss:
        by[r["path"]] = by.get(r["path"], 0) + 1
    return len(rows), len(miss), len(defect), len(miss) - len(defect), sum(r["error"] is not None for r in rows), dead, len(logs), by


def control():
    print("### CONTROL, no patcher at all: networkidle misses, old against new")
    print()
    print("Fifteen rounds x four processes per arm, the order of the arms alternating, from the HUNT-ROW lines of `hunt_netidle.py` only. A miss is "
          "a row that was not on /landed when goto returned: a **defect** when the server got /landed before goto returned, **not yet asked** when "
          "the page had not asked for it yet.")
    print()
    print("| arm | misses | defect | not yet asked | rows | goto errors | processes with no row | misses by path |")
    print("|---|---|---|---|---|---|---|---|")
    for arm in ("old", "new"):
        n, m, d, ny, err, dead, nlogs, by = miss_counts("control", arm)
        print("| %s | %d | %d | %d | %d | %d | %d of %d | %s |" % (
            arm, m, d, ny, n, err, dead, nlogs, ", ".join("%s %d" % kv for kv in sorted(by.items())) or "-"))
    n, m, d, ny, err, dead, nlogs, by = miss_counts("netidle", ARM)
    print("| %s, stamped (the patched pass, for comparison only) | %d | %d | %d | %d | %d | %d of %d | %s |" % (
        ARM, m, d, ny, n, err, dead, nlogs, ", ".join("%s %d" % kv for kv in sorted(by.items())) or "-"))
    print()
    print("Expected 1200 rows per arm (4 processes x 15 rounds x 20 rows).")
    print()


def main(label, threshold):
    procs = collect()
    print("## %s" % label)
    print()
    control()
    print("## The stamped pass: where the time goes between the pipe and the lifecycle")
    print()
    n_rows = n_landed = n_both = 0
    delayed = []
    p2ls = []
    for pr in procs:
        for r in pr["rows"]:
            n_rows += 1
            srv = r.get("landed_t")
            if srv is None:
                continue
            n_landed += 1
            p = base.nearest(pr["pipes"], srv)
            l = base.nearest(pr["lcs"], p) if p is not None else None
            if p is None or l is None:
                continue
            n_both += 1
            p2l = (l - p) * 1000.0
            p2ls.append(p2l)
            if p2l > threshold:
                delayed.append((pr, r, p, l, p2l))
    with_totals = sum(any(x["k"] == "totals" for x in pr["locks"]) for pr in procs)
    print("Arm `%s`: %d processes (%d with a totals row, %d with lock rows), %d rows, %d with /landed on the server, %d with the pipe and the lifecycle stamp, "
          "**%d with pipe -> lifecycle over %g ms**." % (
              ARM, len(procs), with_totals, sum(bool(pr["locks"]) for pr in procs), n_rows, n_landed, n_both, len(delayed), threshold))
    print()
    print("pipe -> lifecycle over every row: p50 %s ms, p95 %s ms, max %s ms." % (
        base.fmt(base.pct(p2ls, 0.5)), base.fmt(base.pct(p2ls, 0.95)), base.fmt(max(p2ls) if p2ls else None)))

    # ── the delayed events ────────────────────────────────────────────────────────────────────────────────────────────────────────
    print()
    print("### every /landed whose pipe -> lifecycle is over %g ms, the interval cut by item (ms)" % threshold)
    print()
    print("Resource view, a partition of the messages the reader handled inside the interval: gate (b) + locks (c) + on CPU + off CPU (d) = time inside "
          "messages; `outside` is the rest of the interval (no `_deliver` running). `off CPU` is wall - cpu - lock waits - gate, i.e. the GIL or the OS "
          "not running the thread.")
    print()
    hist = {}
    details = []
    if not delayed:
        print("None.")
    else:
        print("| process | path #round | goto ms | pipe -> lifecycle | gate | locks | on CPU | off CPU | outside | dominant item (ms) |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for pr, r, p, l, p2l in delayed:
            b = breakdown(pr["locks"], p, l)
            name, ms = dominant(b)
            hist[name] = hist.get(name, 0) + 1
            details.append((pr, r, b))
            print("| %s | %s #%d | %d | %.0f | %.0f | %.0f | %.0f | %.0f | %.0f | %s (%.0f) |" % (
                pr["tag"], r["path"], r["round"], r["goto_ms"], p2l, b["gate"], b["lock"], b["cpu"], b["off"], b["outside"], name, ms))
        print()
        print("### dominant item over the delayed events")
        print()
        print("| item | delayed events |")
        print("|---|---|")
        for k, v in sorted(hist.items(), key=lambda kv: -kv[1]):
            print("| %s | %d |" % (k, v))
        print()
        print("### the delayed events one by one (the first 25): what ran on the reader in the interval, items of 1 ms or more")
        for pr, r, b in details[:25]:
            print()
            print("**%s, %s #%d**: interval %.0f ms; gate %.0f, locks %.0f, on CPU %.0f, off CPU %.0f, outside any message %.0f, "
                  "decode/ends_waits %.0f, lifecycle stamp write %.1f, pipe stamp write %.1f" % (
                      pr["tag"], r["path"], r["round"], b["interval"], b["gate"], b["lock"], b["cpu"], b["off"], b["outside"],
                      max(0.0, b["msg_other"] - sum(d["total"] for d in b["listeners"].values())), b["lc_io"], b["stamp_io"]))
            items = []
            for (op, name), d in b["locks"].items():
                holders = ", ".join("%s x%d" % (h, c) for h, c in sorted(d["holders"].items(), key=lambda kv: -kv[1])[:2])
                items.append((d["ms"], "- %s `%s`: %.1f ms in %d wait(s); held by %s" % (op, name, d["ms"], d["n"], holders)))
            for name, d in b["listeners"].items():
                ev = ", ".join("%s x%d" % kv for kv in sorted(d["events"].items(), key=lambda kv: -kv[1])[:3])
                items.append((d["total"], "- listener `%s`: %.1f ms in %d call(s) (own %.1f, cpu %.1f); events: %s" % (
                    name, d["total"], d["n"], d["own"], d["cpu"], ev)))
            for o, g in b["gates"]:
                items.append((o, "- gate: %.1f ms, got=%s, holder when it began to wait: %s at %s (held %s ms so far); event %s" % (
                    o, g.get("got"), g.get("holder"), g.get("held_at"), g.get("held_so_far_ms"), g.get("ev"))))
            for nme, o in b["io"].items():
                items.append((o, "- instrumentation `%s`: %.1f ms" % (nme, o)))
            for _, line in sorted(items, key=lambda it: -it[0]):
                print(line)
            if not items:
                print("- nothing of 1 ms or more is on the reader in this interval")
        if len(details) > 25:
            print()
            print("(%d more delayed events not listed)" % (len(details) - 25))

    # ── totals per item ───────────────────────────────────────────────────────────────────────────────────────────────────────────
    items = {}
    msgs = {"n": 0, "wall_ms": 0.0, "cpu_ts_ms": 0.0, "cpu_cyc_ms": 0.0, "chunks": 0, "bytes": 0}
    for pr in procs:
        for r in pr["locks"]:
            if r["k"] != "totals":
                continue
            for k, v in r["items"].items():
                d = items.setdefault(k, [0, 0.0, 0.0])
                d[0] += v[0]
                d[1] += v[1]
                d[2] = max(d[2], v[2])
            for k in msgs:
                msgs[k] += r["msgs"].get(k, 0)
    rowcount = {}
    for pr in procs:
        for r in pr["locks"]:
            if r["k"] in ("lock", "gate", "io") and r.get("ms") is not None:
                key = {"lock": "%s:%s" % ("wait" if r.get("op") == "wait" else "lock", r.get("name")), "gate": "gate", "io": r.get("name")}[r["k"]]
                rowcount[key] = rowcount.get(key, 0) + 1
    print()
    print("### totals per item, every process of the arm, every message the reader handled (ms)")
    print()
    print("`lock:` = a wait to take it, `wait:` = a Condition.wait on the reader (a pause the code asked for), `lst:` = a listener call (it holds the lock "
          "waits and the gate wait inside it), `gate` = gated_dispatch's wait, `hunt_pipe_stamp` = the pipe stamp's append to a list in memory.")
    print()
    print("| item | n | sum | mean | max | rows of the wait kind (5 ms+; gate, io 1 ms+) |")
    print("|---|---|---|---|---|---|")
    for k, (n, s, mx) in sorted(items.items(), key=lambda kv: -kv[1][1])[:45]:
        print("| %s | %d | %.1f | %.3f | %.1f | %s |" % (k, n, s, s / max(n, 1), mx, rowcount.get(k, "-")))
    print()
    print("### off the CPU, per message (d)")
    print()
    cpu_total = msgs["cpu_cyc_ms"] if msgs["cpu_cyc_ms"] else msgs["cpu_ts_ms"]
    print("Messages handled: %d (%d chunks, %d bytes). Wall in `_deliver`: %.1f ms; CPU of the reader thread: %.1f ms by the cycle counter (%.1f ms by "
          "time.thread_time, ticks of about 15.6 ms on Windows). CPU / wall = %.3f." % (
              msgs["n"], msgs["chunks"], msgs["bytes"], msgs["wall_ms"], msgs["cpu_cyc_ms"], msgs["cpu_ts_ms"],
              (cpu_total / msgs["wall_ms"]) if msgs["wall_ms"] else 0.0))
    stalls = []
    off_big = 0
    for pr in procs:
        for r in pr["locks"]:
            if r["k"] == "msg":
                off = max(0.0, r["ms"] - cpu_of(r) - r["lock"] - r["gate"])
                if off >= 5.0:
                    off_big += 1
                stalls.append((r["ms"], off, pr["tag"], r))
    print()
    print("Messages of 1 ms or more written as rows: %d; of them %d spent 5 ms or more off the CPU outside lock and gate waits." % (len(stalls), off_big))
    print()
    print("### the longest messages of the reader (any time, delayed or not)")
    print()
    print("| process | message ms | event | on CPU | locks | gate | listeners | off CPU | landed |")
    print("|---|---|---|---|---|---|---|---|---|")
    for ms, off, tag, r in sorted(stalls, key=lambda x: -x[0])[:20]:
        print("| %s | %.1f | %s | %.1f | %.1f | %.1f | %.1f | %.1f | %s |" % (tag, ms, r.get("ev"), min(cpu_of(r), ms), r["lock"], r["gate"], r["lst"], off, r["landed"]))
    print()
    print("### every lock wait of 5 ms or more, by lock (any time)")
    print()
    by = {}
    for pr in procs:
        for r in pr["locks"]:
            if r["k"] == "lock":
                d = by.setdefault((r["op"], r["name"]), {"n": 0, "ms": 0.0, "max": 0.0, "holders": {}})
                d["n"] += 1
                d["ms"] += r["ms"]
                d["max"] = max(d["max"], r["ms"])
                h = "%s at %s" % (r.get("holder"), r.get("held_at"))
                d["holders"][h] = d["holders"].get(h, 0) + 1
    if not by:
        print("None: the reader never waited 5 ms or more for a lock.")
    else:
        print("| op | lock | waits | sum ms | max ms | holder when the wait began |")
        print("|---|---|---|---|---|---|")
        for (op, name), d in sorted(by.items(), key=lambda kv: -kv[1]["ms"]):
            print("| %s | %s | %d | %.0f | %.0f | %s |" % (op, name, d["n"], d["ms"], d["max"],
                                                          ", ".join("%s x%d" % (h, c) for h, c in sorted(d["holders"].items(), key=lambda kv: -kv[1])[:3])))
    print()
    print("The instrumentation runs on the reader too, but it only appends to lists in memory (nothing is opened or written until the process exits); "
          "compare the stamped pass's misses with the control's above to see whether it changes the result.")


if __name__ == "__main__":
    EV = sys.argv[2]
    main(sys.argv[1], float(sys.argv[3]) if len(sys.argv) > 3 else 100.0)
