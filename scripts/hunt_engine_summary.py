"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of the engine hunt (where a typed key's delay is born inside the engine).

Usage: python scripts/hunt_engine_summary.py EVIDENCE_DIR OS_NAME
Reads EVIDENCE_DIR/{alone,loaded}_<arm>*.log: the HUNT-TYPING lines of scripts/hunt_typing.py (one per run) and, for the stock arm, the
HUNTKEY lines the Playwright driver copied into the same log (DEBUG=pw:browser), put into the run whose typing window holds them.
Arms: ours (the engine candidate under test, patched by scripts/hunt_patch_omni.py, core of fix/typing-on-absolute-deadlines), ours_plain
(the same engine untouched: what the page sees without the stamps, to show what they cost) and stock (stock Playwright Firefox, its Juggler
patched the same way).

The stamps of one key event (a keydown or keyup command; tags in scripts/hunt_patch_omni.py) are joined to the page's event of the same
(type, key, n-th time) and to the client's send (keydown, not Shift). Between two consecutive PRESENT tags of

    sent -> P1 -> C2 -> C3 -> C4 -> C4r -> C5 -> h        (h = performance.now() in the page's handler)

there is one step, named "A>B"; the steps add up exactly to sent -> h. In an engine WITHOUT a latency timer (the candidate under test) C3
and C4 are just before and just after tip.setNextKeyLatency(born) and there is no C4r; an engine WITH the timer (older) has C3 = timer
armed, C4 = its callback, and gets the "timer planned" / "timer excess" rows. "timestamp lag" = (h - event.timeStamp) - the latency sent:
how much more or less than the declared latency the event, born in the past, is older than the page's handler. Dwell error = (keyup -
keydown) seen by the page minus the planned dwell, on both page clocks (h handler, e event.timeStamp). The stock engine has no latency, so
its chain is sent, P1, C2, C5, h.

A page gap is the time between the handlers of two consecutive keydowns that are not Shift. For every page gap under GAP_MS (150) the
step that absorbed the delay is the one where the earlier key took longer than the later one by the most: the page gap equals the send
gap plus the later key's steps minus the earlier key's, so (earlier - later) per step is what shortened it. If the sends themselves were
closer than GAP_MS the client is named instead.
"""
import glob
import json
import os
import re
import sys

GAP_MS = 150.0
ARMS = ["ours", "stock", "ours_plain"]
STAMPED = ("ours", "stock")
MODES = ["alone", "loaded"]
ORDER = ["sent", "P1", "C2", "C3", "C4", "C4r", "C5", "h"]
LINE = re.compile(r"(\S+) (\d+) (\d+) (\S+) ([\d.]+) (\S+) (\S+)(?: (.*))?$")


def q(v, f):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * f))] if v else None


def fmt(x):
    return "-" if x is None else ("%.1f" % x if isinstance(x, float) else str(x))


def parse_stamp(text):
    i = text.find("HUNTKEY ")
    if i >= 0:
        text = text[i + 8:]
    m = LINE.match(text.strip())
    if not m:
        return None
    proc, pid, call, tag, wall, typ, key, extra = m.groups()
    from urllib.parse import unquote
    return {"proc": proc, "pid": int(pid), "call": int(call), "tag": tag, "wall": float(wall), "type": typ,
            "key": unquote(key), "extra": extra or ""}


def read_log(path):
    runs, loose = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("HUNT-TYPING "):
                try:
                    runs.append(json.loads(line[len("HUNT-TYPING "):]))
                except ValueError:
                    pass
            elif "HUNTKEY " in line:
                s = parse_stamp(line)
                if s:
                    loose.append(s)
    return runs, loose


def build_events(run, loose, sources):
    """The key events of one run: {(type, key, n): {"page": (h, e), "tags": {tag: wall}, ...}}, in page order."""
    stamps = {}
    for text in list(run.get("hk") or []):
        s = parse_stamp(text)
        if s:
            stamps[text.strip()] = (s, "pipe")
    for text in list(run.get("hs") or []):
        s = parse_stamp(text)
        if s:
            stamps.setdefault(text.strip(), (s, "page"))
    # the stamps are written once, when a process ends (and the page's copy holds every key of the process so far): only the ones of
    # THIS run's typing window are its own
    w0, w1 = run["win"][0] - 100, run["win"][1] + 1000
    stamps = {t: v for t, v in stamps.items() if w0 <= v[0]["wall"] <= w1}
    for s in loose:
        if w0 <= s["wall"] <= w1:
            stamps.setdefault("%s %s %s %s" % (s["proc"], s["pid"], s["call"], s["tag"]) + repr(s["wall"]), (s, "log"))
    # one source per distinct stamp (same proc, pid, call, tag, wall): the pipe or log copy wins over the page's
    seen = {}
    for text, (s, src) in stamps.items():
        k = (s["proc"], s["pid"], s["tag"], s["wall"], s["type"], s["key"])
        if k not in seen or src != "page":
            seen[k] = (s, src)
    by = {}
    for s, src in seen.values():
        sources[(s["proc"], src)] = sources.get((s["proc"], src), 0) + 1
        by.setdefault((s["tag"], s["type"], s["key"]), []).append(s)
    for v in by.values():
        v.sort(key=lambda s: s["wall"])
    events, count = {}, {}
    nonshift_down = 0
    for typ, key, h, e in run.get("ev") or []:
        n = count.get((typ, key), 0)
        count[(typ, key)] = n + 1
        ev = {"type": typ, "key": key, "n": n, "h": h, "e": e, "tags": {}, "delay": None, "latency": None, "sent": None}
        for tag in ORDER[1:-1]:
            lst = by.get((tag, typ, key)) or []
            if n < len(lst):
                ev["tags"][tag] = lst[n]["wall"]
                if tag == "C3":
                    m = re.search(r"delay=([\d.]+)", lst[n]["extra"])
                    ev["delay"] = float(m.group(1)) if m else None
                if tag == "C2":
                    m = re.search(r"latency=([\d.]+)", lst[n]["extra"])
                    ev["latency"] = float(m.group(1)) if m else None
        if typ == "keydown" and key != "Shift":
            ev["nonshift_index"] = nonshift_down
            nonshift_down += 1
        events[(typ, key, n)] = ev
    sent = run.get("sent_wall_ms") or []
    if len(sent) == nonshift_down:
        for ev in events.values():
            if "nonshift_index" in ev:
                ev["sent"] = sent[ev["nonshift_index"]]
    return list(events.values())


def chain(ev):
    """[(name, wall)] of the tags present, in order, ending at the page's handler."""
    pts = []
    if ev["sent"] is not None:
        pts.append(("sent", ev["sent"]))
    for tag in ORDER[1:-1]:
        if tag in ev["tags"]:
            pts.append((tag, ev["tags"][tag]))
    pts.append(("h", ev["h"]))
    return pts


def steps(ev):
    pts = chain(ev)
    out = {}
    for (a, ta), (b, tb) in zip(pts, pts[1:]):
        out["%s>%s" % (a, b)] = tb - ta
    if "C3" in ev["tags"] and "C4" in ev["tags"] and ev["delay"] is not None:
        out["timer planned"] = ev["delay"]
        out["timer excess"] = (ev["tags"]["C4"] - ev["tags"]["C3"]) - ev["delay"]
    if ev["latency"] is not None:
        out["timestamp lag"] = (ev["h"] - ev["e"]) - ev["latency"]
    if "P1" in ev["tags"]:
        out["P1>h total"] = ev["h"] - ev["tags"]["P1"]
    return out


def load(evidence, mode, arm, sources):
    """-> list of runs, each {"run": json, "events": [...]}"""
    out = []
    paths = glob.glob(os.path.join(evidence, "%s_%s.log" % (mode, arm))) + glob.glob(os.path.join(evidence, "%s_%s_[0-9]*.log" % (mode, arm)))
    for path in sorted(paths):
        runs, loose = read_log(path)
        for r in runs:
            if "win" not in r:
                continue
            out.append({"run": r, "events": build_events(r, loose, sources)})
    return out


def gaps_of(events):
    d = [e for e in events if e["type"] == "keydown" and e["key"] != "Shift"]
    return list(zip(d, d[1:]))


def step_table(data_by_arm, kind):
    names = []
    cols = {}
    for arm, runs in data_by_arm.items():
        vals = {}
        for r in runs:
            for ev in r["events"]:
                if ev["type"] != kind:
                    continue
                for k, v in steps(ev).items():
                    vals.setdefault(k, []).append(v)
        cols[arm] = vals
        for k in vals:
            if k not in names:
                names.append(k)
    pri = {"sent>P1": 0, "P1>C2": 1, "C2>C3": 2, "timer planned": 3, "C3>C4": 4, "timer excess": 5, "C4>C4r": 6, "C4r>C5": 7,
           "C4>C5": 7, "C2>C5": 8, "C5>h": 9, "timestamp lag": 10, "P1>h total": 11}
    names.sort(key=lambda k: pri.get(k, 99))
    lines = ["| step (ms) | " + " | ".join("%s p50 / p95 / max (n)" % a for a in cols) + " |",
             "|---|" + "---|" * len(cols)]
    for k in names:
        cells = []
        for arm in cols:
            v = cols[arm].get(k)
            cells.append("-" if not v else "%s / %s / %s (%d)" % (fmt(q(v, .5)), fmt(q(v, .95)), fmt(max(v)), len(v)))
        lines.append("| %s | %s |" % (k, " | ".join(cells)))
    return lines


def main():
    evidence, os_name = sys.argv[1], sys.argv[2]
    for mode in MODES:
        sources, data = {}, {}
        for arm in ARMS:
            d = load(evidence, mode, arm, sources if arm in STAMPED else {})
            if d:
                data[arm] = d
        if not data:
            continue
        print("## engine hunt, %s, %s" % (os_name, mode))
        print()
        print("runs per arm: " + ", ".join("%s %d" % (a, len(r)) for a, r in data.items()))
        print()
        print("stamps by where they came from (process, source): " + (", ".join(
            "%s/%s %d" % (p, s, n) for (p, s), n in sorted(sources.items())) or "none"))
        print()
        # coverage
        for arm in STAMPED:
            if arm in data:
                evs = [e for r in data[arm] for e in r["events"]]
                full = sum(1 for e in evs if "P1" in e["tags"] and "C2" in e["tags"] and "C5" in e["tags"])
                print("- %s: %d key events, %d with P1, C2 and C5" % (arm, len(evs), full))
        print()
        # page gaps, every arm
        print("### page keydown gaps under %d ms (handler clock)" % GAP_MS)
        print()
        print("| arm | runs | runs with a gap under %d | gaps under %d / gaps | smallest gap p50 / min | planned gap min |" % (GAP_MS, GAP_MS))
        print("|---|---|---|---|---|---|")
        for arm, runs in data.items():
            short = total = with_short = 0
            mins = []
            plan = []
            for r in runs:
                gs = [(b["h"] - a["h"]) for a, b in gaps_of(r["events"])]
                if gs:
                    mins.append(min(gs))
                    total += len(gs)
                    n = sum(1 for g in gs if g < GAP_MS)
                    short += n
                    with_short += 1 if n else 0
                if r["run"].get("plan_min_keydown_gap_ms") is not None:
                    plan.append(r["run"]["plan_min_keydown_gap_ms"])
            print("| %s | %d | %d | %d / %d | %s / %s | %s |" % (arm, len(runs), with_short, short, total, fmt(q(mins, .5)),
                                                              fmt(min(mins) if mins else None), fmt(min(plan) if plan else None)))
        print()
        stamped = {a: r for a, r in data.items() if a in STAMPED}
        print("### dwell error: (keyup - keydown) seen by the page minus the planned dwell (ms)")
        print()
        print("| arm | clock | keys | abs p50 | abs p95 | abs max | signed p50 | signed min | signed max | runs with all keys seen |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for arm, runs in data.items():
            seen = sum(1 for r in runs if r["run"].get("all_keys_seen"))
            for clock, key in (("h (handler)", "dwell_err_h_ms"), ("e (event.timeStamp)", "dwell_err_e_ms")):
                err = [x for r in runs for x in (r["run"].get(key) or [])]
                ab = [abs(x) for x in err]
                print("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %d/%d |" % (
                    arm, clock, len(err), fmt(q(ab, .5)), fmt(q(ab, .95)), fmt(max(ab) if ab else None), fmt(q(err, .5)),
                    fmt(min(err) if err else None), fmt(max(err) if err else None), seen, len(runs)))
        print()
        for kind in ("keydown", "keyup"):
            if stamped:
                print("### steps of a %s command (ms)" % kind)
                print()
                print("\n".join(step_table(stamped, kind)))
                print()
        # which step absorbed it
        for arm, runs in stamped.items():
            rows, counts = [], {}
            for ri, r in enumerate(runs):
                for a, b in gaps_of(r["events"]):
                    gap = b["h"] - a["h"]
                    if gap >= GAP_MS:
                        continue
                    sa, sb = steps(a), steps(b)
                    sendgap = (b["sent"] - a["sent"]) if a["sent"] is not None and b["sent"] is not None else None
                    names = [k for k in sa if ">" in k and k != "P1>h total" and k in sb]
                    if sendgap is not None and sendgap < GAP_MS:
                        who, amount = "client send", sendgap
                    elif names and sendgap is not None:
                        who = max(names, key=lambda k: sa[k] - sb[k])
                        amount = sa[who] - sb[who]
                    else:
                        who, amount = "unstamped", None
                    counts[who] = counts.get(who, 0) + 1
                    rows.append((ri, a["key"], b["key"], sendgap, gap, who, amount,
                                 {k: sa[k] for k in names}, {k: sb[k] for k in names}))
            print("### %s: page gaps under %d ms and the step that absorbed them" % (arm, GAP_MS))
            print()
            if not rows:
                print("none")
                print()
                continue
            print("culprit counts: " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
            print()
            print("| run | keys | send gap | page gap | absorbed by (earlier - later) | earlier key's steps | later key's steps |")
            print("|---|---|---|---|---|---|---|")
            for ri, ka, kb, sg, gap, who, amount, sa, sb in rows[:60]:
                print("| %d | %s>%s | %s | %s | %s %s | %s | %s |" % (
                    ri, ka, kb, fmt(sg), fmt(gap), who, fmt(amount),
                    " ".join("%s=%s" % (k, fmt(v)) for k, v in sa.items()),
                    " ".join("%s=%s" % (k, fmt(v)) for k, v in sb.items())))
            if len(rows) > 60:
                print("(%d more not listed)" % (len(rows) - 60))
            print()


if __name__ == "__main__":
    main()
