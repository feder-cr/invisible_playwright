"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of the send-split hunt (where the delay of a typed key is born between the client's
decision and the page's handler, with the hop "sent -> P1" cut in pieces).

Usage: python scripts/hunt_engine_summary.py EVIDENCE_DIR OS_NAME
Reads EVIDENCE_DIR/{alone,loaded}_<arm>*.log: the HUNT-TYPING lines of scripts/hunt_typing.py (one per run) and, for the stock arm, the
HUNTKEY lines the Playwright driver copied into the same log (DEBUG=pw:browser), put into the run whose typing window holds them.
Arms (PIPE-BUFFER hunt): ours_bigpipe (the engine candidate under test, patched by scripts/hunt_patch_omni.py, core of fix/typing-on-absolute-deadlines,
the core's two protocol pipes made 1 MiB big by a runtime patch in scripts/hunt_typing.py), ours_normal (the same with the core's pipes as they are)
and stock (stock Playwright Firefox, its Juggler patched the same way). Windows, 4 processes at once.

What this hunt adds to the send-split summary, first in the output: per arm the quantiles of S0>S1 and S1>S2 (p50 / p95 / p99 / max, count over
LONG_MS), the page keydown gaps under GAP_MS, the dwell error, the pipe sizes in effect at launch (the "pipe" field of each run), and the BACKLOG
table: for every S0>S1 over LONG_MS in the two ours arms, the bytes written to the pipe and not yet read by the engine at that moment, estimated
from the stamps as the sum of the sizes of the Page.dispatchKeyEvent messages (S1's raw message, plus its terminating NUL) with S1 done and S2 not
yet. Only the key messages are stamped, so any other message in the pipe is not in the number (a lower bound); a message whose S2 is missing is
counted apart ("unknown") and left out of the sum. The moment is taken three ways: at S0 (the decision), at the start of the os.write call (S1pre,
the third item of the run's "s1" entries), and the peak between S0 and S1.

The points of one key event (a keydown or keyup command; tags in scripts/hunt_patch_omni.py and scripts/hunt_typing.py):

    S0   the client decided (Keyboard.down, before the send; non-Shift keydowns only)            "sent"
    S1   os.write returned in invisible_core Connection._write (ours only: stock's write is in the node driver)
    S2   the parent's JS got the pipe message (components/Juggler.js, nsIRemoteDebuggingPipeClient.receiveMessage)
    S2b  Dispatcher._dispatch entered (before its JSON.parse)
    S3   = P1, PageHandler['Page.dispatchKeyEvent'] entered
    C2 .. C5  the content process (latency declared, DOM event about to be made), h = the page's handler (performance.now())

Hops asked for: S0>S1 (core: from the decision to the write), S1>S2 (pipe, C++ reader thread, hand-over to the main thread), S2>S3 (the
parent's main thread up to the handler; S2>S2b and S2b>P1 are its two pieces), P1>C2 (parent to content process). Stock has no S1, so for it
S0>S2 is one hop (shown for both arms as "S0 to S2 (sum)" so they compare).

A page gap is the time between the handlers of two consecutive keydowns that are not Shift. For every page gap under GAP_MS (150) the hop
that absorbed the delay is the one where the earlier key took longer than the later one by the most (the page gap equals the send gap plus
the later key's hops minus the earlier key's). If the sends themselves were closer than GAP_MS the client is named instead.

Long hops: every S1>S2 (stock: S0>S2) and S2>S3 over LONG_MS (30) is listed with what the parent's main thread ran in its window, from the
ring the Dispatcher wrapper keeps for ALL inbound messages (D: method, id, the synchronous part's duration, the whole duration) and ALL
emitted events (E). An entry "occupies" the window when its synchronous part [t0, t1] overlaps it; an entry whose whole duration overlaps but
whose synchronous part does not is only in flight (awaiting, not on the thread) and is counted by method. Nothing listed means the thread
was held by something the ring does not see (content-to-parent channel messages, GC, the shell, another process's work), or the time was
spent outside the thread (the pipe, the OS scheduler).
"""
import glob
import json
import os
import re
import sys
from urllib.parse import unquote

GAP_MS = 150.0
LONG_MS = 30.0
ARMS = ["ours_bigpipe", "ours_normal", "stock"]
STAMPED = ("ours_bigpipe", "ours_normal", "stock")
OURS = ("ours_bigpipe", "ours_normal")
MODES = ["loaded"]
ORDER = ["sent", "S1", "S2", "S2b", "P1", "C2", "C3", "C4", "C4r", "C5", "h"]
LINE = re.compile(r"(\S+) (\d+) (\d+) (\S+) ([\d.]+) (\S+) (\S+)(?: (.*))?$")


def q(v, f):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * f))] if v else None


def fmt(x):
    return "-" if x is None else ("%.1f" % x if isinstance(x, float) else str(x))


def kv(extra):
    """'a=1 b=x' -> {'a': '1', 'b': 'x'}"""
    out = {}
    for part in (extra or "").split():
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def message_of(raw):
    """The (type, key) of a Page.dispatchKeyEvent message, from the JSON text; (None, None) if it is not one."""
    try:
        d = json.loads(raw)
    except ValueError:
        return None, None
    if d.get("method") != "Page.dispatchKeyEvent":
        return None, None
    p = d.get("params") or {}
    return p.get("type"), p.get("key")


def parse_stamp(text):
    i = text.find("HUNTKEY ")
    if i >= 0:
        text = text[i + 8:]
    m = LINE.match(text.strip())
    if not m:
        return None
    proc, pid, call, tag, wall, typ, key, extra = m.groups()
    s = {"proc": proc, "pid": int(pid), "call": int(call), "tag": tag, "wall": float(wall), "type": typ,
         "key": unquote(key), "extra": extra or ""}
    if tag in ("S2", "S2b") and s["extra"].startswith("msg="):
        t, k = message_of(unquote(s["extra"][4:]))
        if t is None:
            return None
        s["type"], s["key"] = t, k
    return s


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
    """-> (events, ring, anchors): the key events of one run in page order ({"page": (h, e), "tags": {tag: wall}, ...}), the parent's ring
    entries (D and E) inside the run's typing window, and the A0 anchors of the parent's modules."""
    stamps = {}
    for text in list(run.get("hk") or []):
        s = parse_stamp(text)
        if s:
            stamps[text.strip()] = (s, "pipe")
    for text in list(run.get("hs") or []):
        s = parse_stamp(text)
        if s:
            stamps.setdefault(text.strip(), (s, "page"))
    for s in loose:
        stamps.setdefault("%s %s %s %s" % (s["proc"], s["pid"], s["call"], s["tag"]) + repr(s["wall"]) + s["extra"], (s, "log"))
    # one source per distinct stamp: the pipe or log copy wins over the page's
    seen = {}
    for text, (s, src) in stamps.items():
        k = (s["proc"], s["pid"], s["tag"], s["wall"], s["type"], s["key"], s["extra"])
        if k not in seen or src != "page":
            seen[k] = (s, src)
    anchors = [s for s, _src in seen.values() if s["tag"] == "A0"]
    # the stamps are written once, when a process ends (and the page's copy holds every key of the process so far): only the ones of
    # THIS run's typing window are its own
    w0, w1 = run["win"][0] - 100, run["win"][1] + 1000
    by, ring = {}, []
    for s, src in seen.values():
        if s["tag"] == "A0" or not (w0 <= s["wall"] <= w1):
            continue
        sources[(s["proc"], src)] = sources.get((s["proc"], src), 0) + 1
        if s["tag"] in ("D", "E"):
            f = kv(s["extra"])
            ring.append({"tag": s["tag"], "t0": s["wall"], "m": f.get("m", "?"), "id": f.get("id"), "sync": float(f.get("sync", "nan")),
                         "total": float(f["total"]) if "total" in f else None, "t1": float(f.get("t1", "nan")),
                         "t2": float(f["t2"]) if "t2" in f else None, "len": f.get("len")})
            continue
        by.setdefault((s["tag"], s["type"], s["key"]), []).append(s)
    # S1: ours, stamped by the client right after os.write, the message kept raw
    for item in run.get("s1") or []:
        wall, raw = item[0], item[1]
        pre = item[2] if len(item) > 2 else None
        t, k = message_of(raw)
        if t is not None and w0 <= wall <= w1:
            # the size on the pipe: the JSON as UTF-8 and the NUL the core adds
            by.setdefault(("S1", t, k), []).append({"wall": wall, "extra": "", "pre": pre, "size": len(raw.encode("utf-8")) + 1})
    for v in by.values():
        v.sort(key=lambda s: s["wall"])
    ring.sort(key=lambda r: r["t0"])
    events, count = {}, {}
    nonshift_down = 0
    for typ, key, h, e in run.get("ev") or []:
        n = count.get((typ, key), 0)
        count[(typ, key)] = n + 1
        ev = {"type": typ, "key": key, "n": n, "h": h, "e": e, "tags": {}, "delay": None, "latency": None, "sent": None,
              "s1pre": None, "size": None}
        for tag in ORDER[1:-1]:
            lst = by.get((tag, typ, key)) or []
            if n < len(lst):
                ev["tags"][tag] = lst[n]["wall"]
                if tag == "S1":
                    ev["s1pre"], ev["size"] = lst[n].get("pre"), lst[n].get("size")
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
    return list(events.values()), ring, anchors


def chain(ev):
    """[(name, wall)] of the points present, in order, ending at the page's handler."""
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
    t = ev["tags"]
    if "C3" in t and "C4" in t and ev["delay"] is not None:
        out["timer planned"] = ev["delay"]
        out["timer excess"] = (t["C4"] - t["C3"]) - ev["delay"]
    if ev["latency"] is not None:
        out["timestamp lag"] = (ev["h"] - ev["e"]) - ev["latency"]
    if "P1" in t:
        out["P1>h total"] = ev["h"] - t["P1"]
    # sums that compare the arms (stock has no S1)
    if ev["sent"] is not None and "S2" in t:
        out["S0 to S2 (sum)"] = t["S2"] - ev["sent"]
    if ev["sent"] is not None and "P1" in t:
        out["S0 to S3 (sum)"] = t["P1"] - ev["sent"]
    if "S2" in t and "P1" in t:
        out["S2 to S3 (S2>P1)"] = t["P1"] - t["S2"]
    return out


def hops(ev):
    """The hops a page gap is attributed to: S0>S1, S1>S2 (stock: S0>S2), S2>S3, P1>C2, C2>C5, C5>h. Only the present ones."""
    t, out = ev["tags"], {}
    sent = ev["sent"]
    if sent is not None and "S1" in t:
        out["S0>S1"] = t["S1"] - sent
    elif sent is not None and "S2" in t:
        out["S0>S2"] = t["S2"] - sent
    if "S1" in t and "S2" in t:
        out["S1>S2"] = t["S2"] - t["S1"]
    if "S2" in t and "P1" in t:
        out["S2>S3"] = t["P1"] - t["S2"]
    if "P1" in t and "C2" in t:
        out["P1>C2"] = t["C2"] - t["P1"]
    if "C2" in t and "C5" in t:
        out["C2>C5"] = t["C5"] - t["C2"]
    if "C5" in t:
        out["C5>h"] = ev["h"] - t["C5"]
    return out


def load(evidence, mode, arm, sources):
    """-> list of runs, each {"run": json, "events": [...], "ring": [...], "anchors": [...]}"""
    out = []
    paths = glob.glob(os.path.join(evidence, "%s_%s.log" % (mode, arm))) + glob.glob(os.path.join(evidence, "%s_%s_[0-9]*.log" % (mode, arm)))
    for path in sorted(paths):
        runs, loose = read_log(path)
        for r in runs:
            if "win" not in r:
                continue
            events, ring, anchors = build_events(r, loose, sources)
            out.append({"run": r, "events": events, "ring": ring, "anchors": anchors})
    return out


def gaps_of(events):
    d = [e for e in events if e["type"] == "keydown" and e["key"] != "Shift"]
    return list(zip(d, d[1:]))


PRI = {"sent>S1": 0, "S1>S2": 1, "sent>S2": 1, "S2>S2b": 2, "S2b>P1": 3, "sent>P1": 3, "S2 to S3 (S2>P1)": 4, "S0 to S2 (sum)": 5,
       "S0 to S3 (sum)": 6, "P1>C2": 7, "C2>C3": 8, "timer planned": 9, "C3>C4": 10, "timer excess": 11, "C4>C4r": 12, "C4r>C5": 13,
       "C4>C5": 13, "C2>C5": 14, "C5>h": 15, "timestamp lag": 16, "P1>h total": 17}


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
    names.sort(key=lambda k: PRI.get(k, 99))
    lines = ["| step (ms) | " + " | ".join("%s p50 / p95 / max (n)" % a for a in cols) + " |",
             "|---|" + "---|" * len(cols)]
    for k in names:
        cells = []
        for arm in cols:
            v = cols[arm].get(k)
            cells.append("-" if not v else "%s / %s / %s (%d)" % (fmt(q(v, .5)), fmt(q(v, .95)), fmt(max(v)), len(v)))
        lines.append("| %s | %s |" % (k.replace("sent", "S0"), " | ".join(cells)))
    return lines


def overlaps(ring, a, b, own_from, own_to):
    """(occupying, inflight): ring entries whose synchronous part overlaps [a, b], and those only in flight over it; the key message that
    is itself being delivered (a Page.dispatchKeyEvent entered between own_from and own_to) is left out."""
    occ, fly = [], []
    for r in ring:
        if r["m"] == "Page.dispatchKeyEvent" and own_from - 0.5 <= r["t0"] <= own_to + 0.5:
            continue
        sync_end = r["t1"] if r["t1"] == r["t1"] else r["t0"]
        if r["t0"] <= b and sync_end >= a:
            occ.append(r)
        elif r["tag"] == "D" and r["t2"] is not None and r["t0"] <= b and r["t2"] >= a:
            fly.append(r)
    return occ, fly


def long_hops(runs):
    """Rows for every S1>S2 (stock S0>S2) and S2>S3 over LONG_MS, with the parent's activity in the window."""
    rows = []
    for ri, r in enumerate(runs):
        for ev in r["events"]:
            t = ev["tags"]
            wins = []
            if "S1" in t and "S2" in t:
                wins.append(("S1>S2", t["S1"], t["S2"]))
            elif ev["sent"] is not None and "S2" in t and "S1" not in t:
                wins.append(("S0>S2", ev["sent"], t["S2"]))
            if "S2" in t and "P1" in t:
                wins.append(("S2>S3", t["S2"], t["P1"]))
            for name, a, b in wins:
                if b - a > LONG_MS:
                    occ, fly = overlaps(r["ring"], a, b, t.get("S2", a), t.get("S2b", b))
                    rows.append((ri, ev, name, a, b, occ, fly))
    return rows


def describe_entry(r, a):
    if r["tag"] == "D":
        return "%s id=%s sync=%.1f total=%s at %+.1f ms" % (r["m"], r["id"], r["sync"], fmt(r["total"]), r["t0"] - a)
    return "event %s sync=%.1f at %+.1f ms" % (r["m"], r["sync"], r["t0"] - a)


def qrow(v):
    """'p50 / p95 / p99 / max / over LONG_MS (n)' of a list of ms, or '-'."""
    if not v:
        return "-"
    return "%s / %s / %s / %s / %d (%d)" % (fmt(q(v, .5)), fmt(q(v, .95)), fmt(q(v, .99)), fmt(max(v)),
                                           sum(1 for x in v if x > LONG_MS), len(v))


def hop_values(runs):
    """{name: [ms]} of the hops the pipe-buffer hunt asks for, over every run of an arm."""
    out = {}
    for r in runs:
        for ev in r["events"]:
            t = ev["tags"]
            if ev["sent"] is not None and "S1" in t:
                out.setdefault("S0>S1", []).append(t["S1"] - ev["sent"])
            if ev["sent"] is not None and "S2" in t:
                out.setdefault("S0>S2 (sum)", []).append(t["S2"] - ev["sent"])
            if "S1" in t and "S2" in t:
                out.setdefault("S1>S2 (every key command)", []).append(t["S2"] - t["S1"])
                if ev["type"] == "keydown" and ev["key"] != "Shift":
                    out.setdefault("S1>S2 (non-Shift keydown)", []).append(t["S2"] - t["S1"])
            if ev["s1pre"] is not None and "S1" in t:
                out.setdefault("os.write call (S1pre>S1)", []).append(t["S1"] - ev["s1pre"])
            if ev["sent"] is not None and ev["s1pre"] is not None:
                out.setdefault("S0>S1pre (core before the write)", []).append(ev["s1pre"] - ev["sent"])
    return out


def messages_of(events):
    """The key messages of one run that have S1: {"s1", "s2" (None if unstamped), "size", "ev"}."""
    return [{"s1": ev["tags"]["S1"], "s2": ev["tags"].get("S2"), "size": ev["size"], "ev": ev}
            for ev in events if "S1" in ev["tags"] and ev.get("size")]


def backlog_at(msgs, when, skip):
    """(bytes, messages, unknown): the messages other than `skip` with S1 done and S2 not yet at `when`; one without S2 is counted as unknown."""
    size = n = unknown = 0
    for m in msgs:
        if m is skip or m["s1"] > when:
            continue
        if m["s2"] is None:
            unknown += 1
        elif m["s2"] > when:
            size += m["size"]
            n += 1
    return size, n, unknown


def backlog_rows(runs):
    """-> (rows, all_at_write): a row per ours event with S0>S1 over LONG_MS; and, for every event with S1pre, the backlog at the start of its write."""
    rows, every = [], []
    for ri, r in enumerate(runs):
        msgs = messages_of(r["events"])
        pipe = r["run"].get("pipe") or {}
        for m in msgs:
            ev = m["ev"]
            if ev["s1pre"] is not None:
                every.append(backlog_at(msgs, ev["s1pre"], m)[0])
            if ev["sent"] is None or m["s1"] - ev["sent"] <= LONG_MS:
                continue
            at_s0 = backlog_at(msgs, ev["sent"], m)
            at_w = backlog_at(msgs, ev["s1pre"], m) if ev["s1pre"] is not None else None
            # the peak between the decision and the end of the write: sampled at the decision and at every S1 / S2 of another message inside it
            times = [ev["sent"], m["s1"]] + [x for o in msgs if o is not m for x in (o["s1"], o["s2"]) if x is not None and ev["sent"] <= x <= m["s1"]]
            peak = max(backlog_at(msgs, x, m)[0] for x in times)
            rows.append((ri, ev, m["s1"] - ev["sent"], (ev["s1pre"] - ev["sent"]) if ev["s1pre"] is not None else None,
                         (m["s1"] - ev["s1pre"]) if ev["s1pre"] is not None else None, at_s0, at_w, peak, pipe))
    return rows, every


def pipebuf_section(data):
    print("### S0>S1 and S1>S2 per arm (ms): p50 / p95 / p99 / max / over %d ms (n)" % LONG_MS)
    print()
    print("stock has no S1 (its write is made by the node driver): its hop is S0>S2, shown as the sum for every arm.")
    print()
    cols = {arm: hop_values(runs) for arm, runs in data.items() if arm in STAMPED}
    names = []
    for c in cols.values():
        for k in c:
            if k not in names:
                names.append(k)
    order = ["S0>S1", "S0>S1pre (core before the write)", "os.write call (S1pre>S1)", "S1>S2 (every key command)", "S1>S2 (non-Shift keydown)",
             "S0>S2 (sum)"]
    names.sort(key=lambda k: order.index(k) if k in order else 99)
    print("| hop | " + " | ".join(cols) + " |")
    print("|---|" + "---|" * len(cols))
    for k in names:
        print("| %s | %s |" % (k, " | ".join(qrow(c.get(k)) for c in cols.values())))
    print()
    print("### pipe sizes in effect at launch (what the runs printed: GetNamedPipeInfo on the core's two handles, and the probe of a pipe made with the same size)")
    print()
    print("| arm | runs with a pipe record | CreatePipe calls in the spawn | size asked (0 = default) | probe: bytes taken before a writer blocks | to_browser (flags / out / in / max) | from_browser (flags / out / in / max) |")
    print("|---|---|---|---|---|---|---|")
    for arm, runs in data.items():
        recs = [r["run"].get("pipe") for r in runs if r["run"].get("pipe")]
        if not recs:
            print("| %s | 0 | - | - | - | not measured: its pipes are made by the node driver | - |" % arm)
            continue
        seen = {}
        for p in recs:
            def one(d):
                return "error %s" % d["error"] if d and "error" in d else ("%s / %s / %s / %s" % tuple(d.get(x, "-") for x in ("flags", "out", "in", "max")) if d else "-")
            key = (p.get("createpipe_calls"), p.get("asked"), p.get("probe_bytes"), one(p.get("to_browser")), one(p.get("from_browser")))
            seen[key] = seen.get(key, 0) + 1
        for (calls, asked, probe, tb, fb), n in sorted(seen.items(), key=lambda kv_: -kv_[1]):
            print("| %s | %d | %s | %s | %s | %s | %s |" % (arm, n, calls, asked, probe, tb, fb))
    print()
    print("### backlog: for every S0>S1 over %d ms, the bytes written (S1 done) and not yet read by the engine (S2 not yet), from the stamps" % LONG_MS)
    print()
    print("Only Page.dispatchKeyEvent messages are stamped, so this is a lower bound of what is in the pipe. 'unknown' = other messages whose S2 is missing "
          "(left out of the sum). at S0 = the decision, at write = the start of the os.write call, peak = the largest between S0 and the end of the write.")
    print()
    for arm in OURS:
        if arm not in data:
            continue
        rows, every = backlog_rows(data[arm])
        print("#### %s" % arm)
        print()
        print("backlog at the start of every write (all key commands, bytes) p50 / p95 / max (n): %s / %s / %s (%d); S0>S1 over %d ms: %d" % (
            fmt(q(every, .5)), fmt(q(every, .95)), fmt(max(every) if every else None), len(every), LONG_MS, len(rows)))
        print()
        if not rows:
            print("no S0>S1 over %d ms" % LONG_MS)
            print()
            continue
        big = sum(1 for x in rows if x[7] >= 4096)
        print("of them with a peak of 4096 bytes or more (the default size of an anonymous pipe): %d" % big)
        print()
        print("| run | key | S0>S1 | S0>S1pre | in os.write | at S0 bytes (msgs, unknown) | at write bytes (msgs, unknown) | peak bytes | pipe: asked / probe / to_browser out,in |")
        print("|---|---|---|---|---|---|---|---|---|")
        for ri, ev, tot, before, inwrite, a0, aw, peak, pipe in rows[:60]:
            tb = pipe.get("to_browser") or {}
            print("| %d | %s %s | %.1f | %s | %s | %d (%d, %d) | %s | %d | %s / %s / %s,%s |" % (
                ri, ev["type"], ev["key"], tot, fmt(before), fmt(inwrite), a0[0], a0[1], a0[2],
                "-" if aw is None else "%d (%d, %d)" % aw, peak, pipe.get("asked", "-"), pipe.get("probe_bytes", "-"), tb.get("out", "-"), tb.get("in", "-")))
        if len(rows) > 60:
            print("(%d more not listed)" % (len(rows) - 60))
        print()


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
        print("## pipe-buffer hunt (send-split stamps), %s, %s" % (os_name, mode))
        print()
        print("runs per arm: " + ", ".join("%s %d" % (a, len(r)) for a, r in data.items()))
        print()
        print("stamps by where they came from (process, source): " + (", ".join(
            "%s/%s %d" % (p, s, n) for (p, s), n in sorted(sources.items())) or "none"))
        print()
        for arm in STAMPED:
            if arm in data:
                evs = [e for r in data[arm] for e in r["events"]]
                full = sum(1 for e in evs if "S2" in e["tags"] and "P1" in e["tags"] and "C2" in e["tags"] and "C5" in e["tags"])
                s1 = sum(1 for e in evs if "S1" in e["tags"])
                s2 = sum(1 for e in evs if "S2" in e["tags"])
                s2b = sum(1 for e in evs if "S2b" in e["tags"])
                p1 = sum(1 for e in evs if "P1" in e["tags"])
                ring = sum(len(r["ring"]) for r in data[arm])
                print("- %s: %d key events; with S1 %d, S2 %d, S2b %d, P1 %d, S2+P1+C2+C5 %d; ring entries in the typing windows %d" % (
                    arm, len(evs), s1, s2, s2b, p1, full, ring))
        print()
        pipebuf_section(data)
        print("### clocks of the parent's modules (A0 stamps: every patched module says the anchor it uses)")
        print()
        for arm in STAMPED:
            if arm not in data:
                continue
            spreads, shared, total = [], 0, 0
            for r in data[arm]:
                by_pid = {}
                for a in r["anchors"]:
                    by_pid.setdefault(a["pid"], []).append(a)
                for pid, lst in by_pid.items():
                    vals = []
                    for a in lst:
                        f = kv(a["extra"])
                        total += 1
                        shared += 1 if f.get("shared") == "true" else 0
                        try:
                            vals.append(float(f["anchor"]))
                        except (KeyError, ValueError):
                            pass
                    if len(vals) > 1:
                        spreads.append(max(vals) - min(vals))
            print("- %s: %d module stamps, %d reused another module's anchor; anchor spread inside one process p50 / max %s / %s ms (%d processes)" % (
                arm, total, shared, fmt(q(spreads, .5)), fmt(max(spreads) if spreads else None), len(spreads)))
        print()
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
                print("### hops of a %s command (ms): S0>S1 core, S1>S2 pipe + reader thread + main-thread hand-over, S2>S3 (=P1) parent main thread, P1>C2" % kind)
                print()
                print("\n".join(step_table(stamped, kind)))
                print()
        # which hop absorbed it
        for arm, runs in stamped.items():
            rows, counts = [], {}
            for ri, r in enumerate(runs):
                for a, b in gaps_of(r["events"]):
                    gap = b["h"] - a["h"]
                    if gap >= GAP_MS:
                        continue
                    sa, sb = hops(a), hops(b)
                    sendgap = (b["sent"] - a["sent"]) if a["sent"] is not None and b["sent"] is not None else None
                    names = [k for k in sa if k in sb]
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
            print("### %s: page gaps under %d ms and the hop that absorbed them" % (arm, GAP_MS))
            print()
            if not rows:
                print("none")
                print()
                continue
            print("culprit counts: " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv_: -kv_[1])))
            print()
            print("| run | keys | send gap | page gap | absorbed by (earlier - later) | earlier key's hops | later key's hops |")
            print("|---|---|---|---|---|---|---|")
            for ri, ka, kb, sg, gap, who, amount, sa, sb in rows[:60]:
                print("| %d | %s>%s | %s | %s | %s %s | %s | %s |" % (
                    ri, ka, kb, fmt(sg), fmt(gap), who, fmt(amount),
                    " ".join("%s=%s" % (k, fmt(v)) for k, v in sa.items()),
                    " ".join("%s=%s" % (k, fmt(v)) for k, v in sb.items())))
            if len(rows) > 60:
                print("(%d more not listed)" % (len(rows) - 60))
            print()
        # long hops and what the parent's main thread ran
        for arm, runs in stamped.items():
            rows = long_hops(runs)
            print("### %s: hops over %d ms (S1>S2, S2>S3) and the parent's activity in their window" % (arm, LONG_MS))
            print()
            events_n = sum(1 for r in runs for e in r["events"] if "S2" in e["tags"])
            print("key events with S2: %d; long hops: %d (S1>S2 %d, S0>S2 %d, S2>S3 %d)" % (
                events_n, len(rows), sum(1 for x in rows if x[2] == "S1>S2"), sum(1 for x in rows if x[2] == "S0>S2"),
                sum(1 for x in rows if x[2] == "S2>S3")))
            print()
            if not rows:
                continue
            print("| run | key | hop | ms | S2>S2b / S2b>S3 | occupying the thread (sync part overlaps) | in flight only |")
            print("|---|---|---|---|---|---|---|")
            for ri, ev, name, a, b, occ, fly in rows[:40]:
                t = ev["tags"]
                pieces = "%s / %s" % (fmt(t["S2b"] - t["S2"]) if "S2b" in t and "S2" in t else "-",
                                      fmt(t["P1"] - t["S2b"]) if "S2b" in t and "P1" in t else "-")
                occs = "; ".join(describe_entry(r, a) for r in occ[:8]) + (" (+%d)" % (len(occ) - 8) if len(occ) > 8 else "")
                counts = {}
                for r in fly:
                    counts[r["m"]] = counts.get(r["m"], 0) + 1
                flies = ", ".join("%s x%d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv_: -kv_[1]))
                print("| %d | %s %s | %s | %.1f | %s | %s | %s |" % (ri, ev["type"], ev["key"], name, b - a, pieces, occs or "nothing in the ring", flies or "-"))
            if len(rows) > 40:
                print("(%d more not listed)" % (len(rows) - 40))
            print()
        # the parent's main-thread work, per method, in the typing windows: ours against stock
        print("### the parent's inbound messages and emitted events in the typing windows (synchronous part, ms)")
        print()
        cols = {}
        for arm, runs in stamped.items():
            vals = {}
            for r in runs:
                for e in r["ring"]:
                    if e["sync"] == e["sync"]:
                        vals.setdefault(("event " if e["tag"] == "E" else "") + e["m"], []).append(e["sync"])
            cols[arm] = vals
        names = sorted({k for v in cols.values() for k in v}, key=lambda k: -max(max(v.get(k, [0])) for v in cols.values()))
        if names:
            print("| message / event | " + " | ".join("%s n / p50 / p95 / max" % a for a in cols) + " |")
            print("|---|" + "---|" * len(cols))
            for k in names[:14]:
                cells = []
                for arm in cols:
                    v = cols[arm].get(k)
                    cells.append("-" if not v else "%d / %s / %s / %s" % (len(v), fmt(q(v, .5)), fmt(q(v, .95)), fmt(max(v))))
                print("| %s | %s |" % (k, " | ".join(cells)))
        else:
            print("no ring entries")
        print()


if __name__ == "__main__":
    main()
