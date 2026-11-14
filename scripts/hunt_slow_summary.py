"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of the slow-listener hunt (markdown on stdout).

Usage: python scripts/hunt_slow_summary.py <label> <evidence dir> [call_ms=50] [decode_ms=20]
Reads <dir>/slow_*.jsonl (rows of scripts/hunt_slow_runtime.py, one file per process) and prints:
  1. what was measured (processes, listener calls, how many over 2 / 20 / 50 / 100 / 200 ms, the sampler's own work);
  2. every listener call over call_ms: event, listener, wall, CPU, off-CPU (wall - CPU: the GIL, a lock, preemption), the number of samples
     and the most frequent innermost frames (file:line function) of its samples, and the first frame in our own code;
  3. the calls over call_ms grouped by innermost frame, and by first frame in our own code (invisible_core / invisible_playwright);
  4. the same calls grouped by listener and event;
  5. every decode over decode_ms with the size of the message.
A frame is "file:line:function"; a sample is taken only when the sampler gets the GIL, so a sample after a long gap is where the call was
when the GIL came free (field dt), not where the gap began.
"""
import collections
import glob
import json
import os
import statistics
import sys

LABEL = sys.argv[1] if len(sys.argv) > 1 else "run"
DIR = sys.argv[2] if len(sys.argv) > 2 else "evidence"
CALL_MS = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
DEC_MS = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
OWN = ("invisible_core/", "invisible_playwright/")


def load():
    procs = []
    for path in sorted(glob.glob(os.path.join(DIR, "slow_*.jsonl"))):
        p = {"file": os.path.basename(path)[len("slow_"):-len(".jsonl")], "lst": {}, "smp": collections.defaultdict(list), "dec": [],
             "totals": None}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    k = r.get("k")
                    if k == "lst":
                        p["lst"][r["id"]] = r
                    elif k == "smp":
                        p["smp"][r["id"]].append(r)
                    elif k == "dec":
                        p["dec"].append(r)
                    elif k == "totals":
                        p["totals"] = r
        except OSError:
            continue
        procs.append(p)
    return procs


def f1(x):
    return "-" if x is None else "%.1f" % x


def md(s):
    return str(s).replace("|", "\\|")


def innermost(smp):
    return smp["st"][0] if smp.get("st") else None


def own_frame(smp):
    for fr in smp.get("st", []):
        if fr.startswith(OWN):
            return fr
    return None


def top(counter, n):
    return "<br>".join("%d x `%s`" % (c, md(k)) for k, c in counter.most_common(n))


def main():
    procs = load()
    print("## Slow listener calls on the reader - %s" % LABEL)
    print()
    if not procs:
        print("No slow_*.jsonl in %s: the instrumentation wrote nothing (the run did not start or the patch did not load)." % DIR)
        return
    with_totals = [p for p in procs if p["totals"]]
    calls = sum(p["totals"]["calls"] for p in with_totals)
    ticks = sum(p["totals"].get("sampler_ticks", 0) for p in with_totals)
    samples = sum(p["totals"].get("samples", 0) for p in with_totals)
    errs = [p["totals"].get("sampler_error") for p in with_totals if p["totals"].get("sampler_error")]
    cyc = sorted({round(p["totals"]["cyc_per_s"] / 1e9, 2) for p in with_totals if p["totals"].get("cyc_per_s")})
    all_calls = [(p, r) for p in procs for r in p["lst"].values()]
    print("%d processes with a trace (%d wrote their totals), %d listener calls on the reader, %d sampler ticks, %d samples, "
          "cycle counter rates (GHz) %s, sampler errors: %s" % (len(procs), len(with_totals), calls, ticks, samples, cyc or "none",
                                                                 errs[:3] or "none"))
    print()
    print("Listener calls (rows are written from 2 ms): " + ", ".join(
        "over %d ms: **%d**" % (t, sum(1 for _, r in all_calls if r["ms"] > t)) for t in (2, 20, 50, 100, 200)))
    print()

    slow = sorted(((p, r) for p, r in all_calls if r["ms"] > CALL_MS), key=lambda pr: -pr[1]["ms"])
    print("### Calls over %g ms: %d" % (CALL_MS, len(slow)))
    print()
    if slow:
        print("| proc | event | listener | wall ms | cpu ms | off-CPU ms | samples (median dt ms) | innermost frames of the samples | first frame in our code |")
        print("|---|---|---|---:|---:|---:|---|---|---|")
        for p, r in slow[:70]:
            sm = p["smp"].get(r["id"], [])
            off = None if r["cpu"] is None else r["ms"] - r["cpu"]
            inner = collections.Counter(innermost(s) for s in sm if innermost(s))
            own = collections.Counter(own_frame(s) for s in sm if own_frame(s))
            dts = [s["dt"] for s in sm]
            print("| %s | %s | %s | %.1f | %s | %s | %d (%s) | %s | %s |" % (
                md(p["file"]), md(r["ev"]), md(r["name"]), r["ms"], f1(r["cpu"]), f1(off), len(sm),
                f1(statistics.median(dts)) if dts else "-", top(inner, 3) or "none", top(own, 2) or "none"))
        if len(slow) > 70:
            print()
            print("(%d more calls not listed)" % (len(slow) - 70))
        print()

        no_smp = sum(1 for p, r in slow if not p["smp"].get(r["id"]))
        cpu_bound = sum(1 for _, r in slow if r["cpu"] is not None and r["cpu"] >= 0.7 * r["ms"])
        print("Of these: %d with no sample at all (the sampler never got the GIL during the call), %d CPU-bound (cpu >= 70%% of wall), "
              "%d mostly off-CPU." % (no_smp, cpu_bound, sum(1 for _, r in slow if r["cpu"] is not None and r["cpu"] < 0.7 * r["ms"])))
        print()

        for title, fn in (("innermost frame", innermost), ("first frame in our code (invisible_core / invisible_playwright)", own_frame)):
            groups = collections.defaultdict(lambda: {"calls": set(), "n": 0, "names": collections.Counter(), "wall": 0.0})
            total = 0
            for pi, (p, r) in enumerate(slow):
                for s in p["smp"].get(r["id"], []):
                    fr = fn(s)
                    if fr is None:
                        continue
                    g = groups[fr]
                    g["n"] += 1
                    total += 1
                    if (pi, r["id"]) not in g["calls"]:
                        g["calls"].add((pi, r["id"]))
                        g["wall"] += r["ms"]
                    g["names"][r["name"] + " / " + r["ev"]] += 1
            print("### Calls over %g ms grouped by %s" % (CALL_MS, title))
            print()
            if not groups:
                print("No samples.")
                print()
                continue
            print("| frame | calls | samples | share of samples | wall ms of those calls | listener / event |")
            print("|---|---:|---:|---:|---:|---|")
            for fr, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"])[:25]:
                print("| `%s` | %d | %d | %.0f%% | %.0f | %s |" % (md(fr), len(g["calls"]), g["n"], 100.0 * g["n"] / total, g["wall"],
                                                                    top(g["names"], 3)))
            print()

        by = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
        for p, r in slow:
            b = by[(r["name"], r["ev"])]
            b[0] += 1
            b[1] += r["ms"]
            b[2] += r["cpu"] or 0.0
            b[3] = max(b[3], r["ms"])
        print("### Calls over %g ms by listener and event" % CALL_MS)
        print()
        print("| listener | event | calls | max ms | wall sum ms | cpu sum ms |")
        print("|---|---|---:|---:|---:|---:|")
        for (name, ev), b in sorted(by.items(), key=lambda kv: -kv[1][3]):
            print("| %s | %s | %d | %.1f | %.0f | %.0f |" % (md(name), md(ev), b[0], b[3], b[1], b[2]))
        print()

    decs = sorted(((p, r) for p in procs for r in p["dec"] if r["ms"] > DEC_MS), key=lambda pr: -pr[1]["ms"])
    print("### Decodes (json.loads) over %g ms: %d" % (DEC_MS, len(decs)))
    print()
    if decs:
        print("| proc | event | size bytes | wall ms | cpu ms | off-CPU ms | samples |")
        print("|---|---|---:|---:|---:|---:|---:|")
        for p, r in decs[:40]:
            off = None if r["cpu"] is None else r["ms"] - r["cpu"]
            print("| %s | %s | %d | %.1f | %s | %s | %d |" % (md(p["file"]), md(r.get("ev") or "?"), r["n"], r["ms"], f1(r["cpu"]), f1(off),
                                                              len(p["smp"].get(r["id"], []))))
        print()


main()
