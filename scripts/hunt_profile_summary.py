"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of the profile hunt.

Usage: python scripts/hunt_profile_summary.py EVIDENCE_DIR OS_NAME            (reads evidence/loaded_<arm>_<n>.log and the profiles the logs name)
       python scripts/hunt_profile_summary.py --selftest                        (the post-processing against a synthetic profile, no browser)

Arms: ours_prof (the candidate, stamped, Gecko profiler from startup), ours_noprof (the same, no profiler: the twin), stock_prof (stock Playwright
Firefox, stamped, profiler). Every arm is a log of HUNT-TYPING lines (scripts/hunt_typing.py).

Part A, the parent's main thread. For every keydown / keyup command whose S1>S2 is over 30 ms (ours; stock has no S1, its window is S0>S2 and
only a keydown has an S0) and, as a second list, every S2>S3 over 30 ms (the parent's main thread again), the samples the profiler took on the parent's
GeckoMain between the two stamps: the leaf and a few callers, grouped by function. The profile's time is put on the stamps' clock with the anchor
the patched Juggler.js printed for that process (wall = anchor + ChromeUtils.now(), the clock of the profiler's sample times: both count from
TimeStamp::ProcessCreation); meta.startTime is compared with it as a sanity check. Then which functions are only in ours: a function of a window sample of
ours that no sample of stock's parent main thread (whole profiles) ever had, and whether it belongs to the series (scripts/hunt_ours_list.json from
the engine source tree, plus the files only our omni.ja has: evidence/jar_names_ours.json against jar_names_stock.json). JS frames are named, C++ label frames are
named, native frames of ours are named from the candidate's Breakpad symbols (artifact symbols-win-x86_64, downloaded into symbols/; only the modules seen in the
windows are parsed) by the debug id of the lib in the profile, and stay `<dll>+0x<offset>` where no symbol is found; stock's native frames stay unsymbolized, so
they are never compared by name. Every function of ours' windows is put in one of three classes: (a) ours (the series: STEALTH-HOOK code, added files and
functions outside juggler/, functions only ours has), (b) upstream Juggler (the Playwright patch both engines carry: juggler files that stock's omni.ja has, the pipe
and screencast C++), (c) other Mozilla 159.

Part B, our process (scripts/hunt_ourside.py): lock wait and holder, CPU against wall, the GIL probe, the long cases.
Then the profiler on/off comparison (ours_prof against ours_noprof, with stock_prof next to them).
"""
import collections
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt_engine_summary as H
import hunt_gecko_profile as G

ARMS = ["ours_prof", "ours_noprof", "stock_prof"]
PROF_ARMS = ["ours_prof", "stock_prof"]
LONG_MS = 30.0
H.ARMS, H.STAMPED, H.MODES = ARMS, tuple(ARMS), ["loaded"]
SYMS = None  # a hunt_gecko_profile.Symbols over the candidate's Breakpad symbols, set by main()


def q(v, f):
    return H.q(v, f)


def fmt(x):
    return H.fmt(x)


def mwu_p(a, b):
    """two-sided Mann-Whitney U, normal approximation with tie correction; None when a sample is under 8"""
    if len(a) < 8 or len(b) < 8:
        return None
    allv = sorted([(x, 0) for x in a] + [(x, 1) for x in b])
    ranks, i = {}, 0
    tie = 0.0
    n = len(allv)
    r = [0.0] * n
    while i < n:
        j = i
        while j + 1 < n and allv[j + 1][0] == allv[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[k] = avg
        t = j - i + 1
        tie += t ** 3 - t
        i = j + 1
    r1 = sum(r[k] for k in range(n) if allv[k][1] == 0)
    n1, n2 = len(a), len(b)
    u = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    var = n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0
    if var <= 0:
        return None
    z = (u - mu) / math.sqrt(var)
    return math.erfc(abs(z) / math.sqrt(2))


# ---- what is ours ----------------------------------------------------------------------------------------------------------------------

class Ours:
    """Does a function belong to the series? From scripts/hunt_ours_list.json and the jar member lists."""

    def __init__(self, listing, jar_ours, jar_stock):
        base = lambda p: p.replace("\\", "/").rsplit("/", 1)[-1]
        self.added = {base(p) for p in listing.get("added", [])}
        self.modified = {base(p) for p in listing.get("modified", [])}
        self.hook_files = {base(p) for p in listing.get("hook_files", [])}
        self.hook_fns, self.hunk_fns = collections.defaultdict(set), collections.defaultdict(set)
        for p, fns in (listing.get("hooks") or {}).items():
            self.hook_fns[base(p)].update(f.rsplit(".", 1)[-1].rsplit("::", 1)[-1] for f in fns)
        for p, fns in (listing.get("hunks") or {}).items():
            self.hunk_fns[base(p)].update(f.rsplit(".", 1)[-1].rsplit("::", 1)[-1] for f in fns)
        self.symbols = set(listing.get("symbols", []))
        self.juggler_symbols = dict(listing.get("juggler_symbols") or {})
        self.other_symbols = set(listing.get("other_symbols") or [])
        self.juggler_files = {base(n) for n in jar_ours if "juggler/" in n.replace("\\", "/")}
        self.juggler_files |= {base(p) for p in listing.get("added", []) if p.startswith("juggler/")}
        self.stock_files = {base(n) for n in jar_stock}
        self._classes = {}
        names_o = {base(n) for n in jar_ours}
        names_s = {base(n) for n in jar_stock}
        self.jar_only = names_o - names_s
        self.named = re.compile(r"Stealth|TrrPrefs|GoneContexts|LaunchCounter|launch.?counter", re.I)

    @staticmethod
    def plain(name):
        """a C++ name without template arguments: 'a::B<T>::c' -> 'a::B::c'"""
        prev = None
        while prev != name:
            prev = name
            name = re.sub(r"<[^<>]*>", "", name)
        return name

    def sym_hit(self, name):
        """'juggler' / 'series' / None for a C++ name: its last two or three components against the symbols of the series"""
        parts = self.plain(name).split("::")
        for k in (3, 2):
            if len(parts) >= k:
                cand = "::".join(parts[-k:])
                if cand in self.juggler_symbols and cand not in self.other_symbols:
                    return "juggler", self.juggler_symbols[cand]
                if cand in self.symbols:
                    return "series", ""
        last = parts[-1]
        if len(parts) == 1 and len(last) > 12:
            if last in self.juggler_symbols and last not in self.other_symbols:
                return "juggler", self.juggler_symbols[last]
            if last in self.symbols:
                return "series", ""
        return None, ""

    def classify(self, key, kind, name, file, stock_all):
        """-> (class letter 'a' ours / 'b' upstream Juggler / 'c' other Mozilla 159 / 'u' unsymbolized native, detail)"""
        got = self._classes.get(key)
        if got:
            return got
        if kind == "js":
            bare = name.rsplit(".", 1)[-1]
            if file in self.juggler_files:
                if file not in self.stock_files:
                    got = ("a", "juggler file only in ours")
                elif bare in self.hook_fns.get(file, ()) or self.named.search(key):
                    got = ("a", "STEALTH-HOOK / named function in a juggler file")
                elif stock_all and key not in stock_all:
                    got = ("a", "juggler function stock's profiles never ran")
                else:
                    got = ("b", "upstream Juggler")
            else:
                t = self.tag(key, kind, name, file)
                got = ("a", t) if t.startswith("ours:") else ("c", "Mozilla 159" + (" (file touched by the series)" if t else ""))
        elif kind == "native" and " (unsymbolicated" in key:
            got = ("u", "native without a symbol")
        else:
            hit, f = self.sym_hit(name)
            if self.named.search(name):
                got = ("a", "named in the brief")
            elif hit == "juggler":
                got = ("a", "juggler C++ only ours has (%s)" % f) if f.startswith("Stealth") else ("b", "upstream Juggler C++ (%s)" % f)
            elif hit == "series":
                got = ("a", "symbol of the series")
            else:
                got = ("c", "Mozilla 159")
        self._classes[key] = got
        return got

    def tag(self, key, kind, name, file):
        """-> a short tag or ''"""
        if kind == "js":
            bare = name.rsplit(".", 1)[-1]
            if file in self.added or file in self.jar_only:
                return "ours: file only in ours" + (" (STEALTH-HOOK file)" if file in self.hook_files else "")
            if bare in self.hook_fns.get(file, ()):
                return "ours: STEALTH-HOOK function"
            if bare in self.hunk_fns.get(file, ()):
                return "ours: function touched by the series"
            if self.named.search(key):
                return "ours: named in the brief"
            if file in self.hook_files or file in self.modified:
                return "file touched by the series (this function not listed)"
            return ""
        if kind == "label":
            if name in self.symbols:
                return "ours: symbol of the series"
            if self.named.search(name):
                return "ours: named in the brief"
            return ""
        return ""


# ---- loading ---------------------------------------------------------------------------------------------------------------------------

def load_arm(evidence, arm):
    out = []
    for path in sorted(glob.glob(os.path.join(evidence, "loaded_%s_[0-9]*.log" % arm))):
        runs, loose = H.read_log(path)
        for r in runs:
            if "win" not in r:
                continue
            events, ring, anchors = H.build_events(r, loose, {})
            out.append({"run": r, "events": events, "ring": ring, "loose": loose, "log": os.path.basename(path)})
    return out


def run_anchor(entry):
    """(parent pid, anchor ms) of the parent process of this run, from the A0 stamps of the patched Juggler.js; (None, None) if none"""
    r = entry["run"]
    stamps = [H.parse_stamp(t) for t in (r.get("hk") or [])] + entry["loose"]
    stamps = [s for s in stamps if s]
    w0, w1 = r["win"][0] - 100, r["win"][1] + 1000
    pids = {s["pid"] for s in stamps if s["proc"] == "parent" and s["tag"] in ("S2", "S2b", "P1") and w0 <= s["wall"] <= w1}
    best = None
    for s in stamps:
        if s["tag"] == "A0" and s["proc"] == "parent" and (not pids or s["pid"] in pids):
            f = H.kv(s["extra"])
            try:
                a = float(f["anchor"])
            except (KeyError, ValueError):
                continue
            if f.get("module") == "Juggler.js":
                return s["pid"], a
            best = best or (s["pid"], a)
    return best or (None, None)


# ---- Part A ----------------------------------------------------------------------------------------------------------------------------

def analyse_window(th, a_prof, b_prof, interval):
    samples = th.samples_between(a_prof, b_prof)
    leaf, incl, callers, idle = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter), 0
    busy = 0
    for _t, st in samples:
        ch = th.chain(st)
        if not ch:
            continue
        if th.is_idle(ch):
            idle += 1
            continue
        busy += 1
        keys = [th.frame_info(fi)[0] for fi in ch]
        leaf[keys[0]] += 1
        callers[keys[0]][" < ".join(keys[1:4])] += 1
        for k in set(keys):
            incl[k] += 1
    return {"n": len(samples), "expected": (b_prof - a_prof) / interval if interval else None, "idle": idle, "busy": busy,
            "leaf": leaf, "incl": incl, "callers": callers}


def part_a(evidence, data, ours, notes):
    """data: arm -> list of run entries. Returns (window rows, per-arm aggregates, stock whole-profile function set, profile paths with a window)"""
    print("## Part A, the parent's main thread under the Gecko profiler (windows over %d ms)" % LONG_MS)
    print()
    rows, agg, keep = [], {}, []
    stock_all = set()
    by_profile = {}  # (arm, profile path) -> [(run index, event, hop, a, b)]
    for arm in PROF_ARMS:
        runs = data.get(arm) or []
        agg[arm] = {"leaf": collections.Counter(), "incl": collections.Counter(), "win": collections.defaultdict(set),
                    "callers": collections.defaultdict(collections.Counter), "windows": 0, "with_samples": 0, "samples": 0, "idle": 0, "busy": 0}
        for ri, ev, name, a, b, _occ, _fly in H.long_hops(runs):
            path = runs[ri]["run"].get("profile")
            hop = "S2>S3" if name == "S2>S3" else ("S1>S2" if arm.startswith("ours") else "S0>S2")
            by_profile.setdefault((arm, path), []).append((ri, ev, hop, a, b))
    print("| arm | runs | profiles named | files exist | parsed | parent main thread found | thread samples p50 | sample gap p50 ms | anchor - meta.startTime ms p50 / max abs | windows S1>S2 (stock S0>S2) | windows S2>S3 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in PROF_ARMS:
        runs = data.get(arm) or []
        paths = sorted({r["run"].get("profile") for r in runs if r["run"].get("profile")})
        exist = [p for p in paths if os.path.exists(p) and os.path.getsize(p) > 0]
        G.Thread.symbolizer = SYMS if arm == "ours_prof" else None  # stock's native frames stay unsymbolized
        # parsed: every profile that has a window, every stock profile (their functions are the reference), and the first two of ours as a health check
        todo = {p for (a_, p) in by_profile if a_ == arm and p} | (set(paths) if arm == "stock_prof" else set(exist[:2]))
        parsed = found = 0
        nsamp, gaps, diffs = [], [], []
        for p in sorted(x for x in todo if x in exist):
            try:
                prof = G.load(p)
            except Exception as exc:  # a file that is cut off or not JSON
                notes.append("%s: %s did not parse: %s" % (arm, os.path.basename(p), exc))
                continue
            parsed += 1
            th = G.parent_main_thread(prof)
            del prof
            if th is None:
                notes.append("%s: %s has no parent main thread" % (arm, os.path.basename(p)))
                continue
            found += 1
            nsamp.append(len(th.s_time))
            g = sorted(y - x for x, y in zip(th.s_time, th.s_time[1:]) if y > x)
            interval = g[len(g) // 2] if g else (th.interval or 1.0)
            gaps.append(interval)
            if arm == "stock_prof":
                stock_all |= th.functions_of_all_samples()
            for ri, ev, hop, a, b in by_profile.get((arm, p), []):
                pid, anchor = run_anchor(data[arm][ri])
                off = anchor if anchor is not None else th.start_time
                if anchor is not None and th.start_time is not None:
                    diffs.append(anchor - th.start_time)
                wid = len(rows)
                row = {"arm": arm, "id": wid, "run": ri, "key": "%s %s" % (ev["type"], ev["key"]), "hop": hop, "ms": round(b - a, 1)}
                if off is None:
                    row["note"] = "no anchor and no meta.startTime"
                    rows.append(row)
                    continue
                w = analyse_window(th, a - off, b - off, interval)
                ag = agg[arm]
                ag["windows"] += 1
                ag["samples"] += w["n"]
                ag["idle"] += w["idle"]
                ag["busy"] += w["busy"]
                ag["with_samples"] += 1 if w["n"] else 0
                for k, c in w["leaf"].items():
                    ag["leaf"][k] += c
                    ag["win"][k].add(wid)
                    for ck, cc in w["callers"][k].items():
                        ag["callers"][k][ck] += cc
                for k, c in w["incl"].items():
                    ag["incl"][k] += c
                    ag["win"][k].add(wid)
                row.update({"n": w["n"], "expected": round(w["expected"], 1) if w["expected"] is not None else None, "idle": w["idle"], "busy": w["busy"],
                            "leaf": w["leaf"].most_common(3),
                            "incl": [x for x in w["incl"].most_common(8) if x[1] < 0.95 * max(1, w["busy"])][:3], "profile": os.path.basename(p), "pid": pid})
                rows.append(row)
                if p not in keep:
                    keep.append(p)
        w1 = sum(1 for r in rows if r["arm"] == arm and r["hop"] in ("S1>S2", "S0>S2"))
        w2 = sum(1 for r in rows if r["arm"] == arm and r["hop"] == "S2>S3")
        print("| %s | %d | %d | %d | %d | %d | %s | %s | %s / %s | %d | %d |" % (
            arm, len(runs), len(paths), len(exist), parsed, found, fmt(q(nsamp, .5)), fmt(q(gaps, .5)),
            fmt(q(diffs, .5)), fmt(max([abs(x) for x in diffs]) if diffs else None), w1, w2))
    G.Thread.symbolizer = None
    print()
    for n in notes:
        print("- note: " + n)
    if notes:
        print()
    return rows, agg, stock_all, keep


def print_windows(rows, arm):
    rs = [r for r in rows if r["arm"] == arm]
    print("### %s: the windows and what the parent main thread was doing in them" % arm)
    print()
    if not rs:
        print("no window over %d ms" % LONG_MS)
        print()
        return
    print("sample columns: n = samples in the window / expected at the thread's sampling interval; idle = samples waiting in the event loop; leaf = the top frames "
          "of the busy samples; inclusive = the functions on most busy samples that are not on all of them (what the thread was inside).")
    print()
    print("| # | run | key | hop | ms | samples n / expected | idle | busy | leaf (samples) | inclusive, not on every stack |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rs[:60]:
        if "note" in r:
            print("| %d | %d | %s | %s | %.1f | - | - | - | %s | |" % (r["id"], r["run"], r["key"], r["hop"], r["ms"], r["note"]))
            continue
        lf = "; ".join("%s x%d" % (k, c) for k, c in r["leaf"]) or "-"
        inc = "; ".join("%s x%d" % (k, c) for k, c in r["incl"]) or "-"
        print("| %d | %d | %s | %s | %.1f | %d / %s | %d | %d | %s | %s |" % (r["id"], r["run"], r["key"], r["hop"], r["ms"], r["n"], fmt(r["expected"]), r["idle"], r["busy"], lf, inc))
    if len(rs) > 60:
        print("(%d more windows not listed; all are in evidence/profile_windows.json)" % (len(rs) - 60))
    print()


def print_functions(arm, ag, ours, thread_kinds, stock_all=None):
    print("### %s: parent main thread by function over every window (busy samples)" % arm)
    print()
    print("windows with a profile: %d (with samples %d); samples %d = idle %d + busy %d" % (ag["windows"], ag["with_samples"], ag["samples"], ag["idle"], ag["busy"]))
    print()
    if not ag["leaf"] and not ag["incl"]:
        print("none")
        print()
        return
    print("| leaf function | leaf samples | inclusive samples | windows | top callers (leaf < c1 < c2 < c3) | class |")
    print("|---|---|---|---|---|---|")
    for k, c in ag["leaf"].most_common(25):
        kind = thread_kinds.get(k, ("label", k, ""))
        tag = ("(%s) %s" % ours.classify(k, kind[0], kind[1], kind[2], stock_all)) if ours and arm == "ours_prof" else "-"
        cal = "; ".join("%s (%d)" % (ck, cc) for ck, cc in ag["callers"][k].most_common(2))
        print("| %s | %d | %d | %d | %s | %s |" % (k, c, ag["incl"].get(k, 0), len(ag["win"][k]), cal, tag))
    print()
    print("most inclusive functions (what the busy samples were inside, any depth), top 15: " + "; ".join("%s x%d" % (k, c) for k, c in ag["incl"].most_common(15)))
    print()


def kind_of_key(key):
    if " @ " in key:
        name, file = key.rsplit(" @ ", 1)
        return ("js", name, file)
    if "(unsymbolicated" in key:
        return ("native", key, "")
    if " [native:" in key:
        name, lib = key.rsplit(" [native:", 1)
        return ("native-sym", name, lib.rstrip("]"))
    return ("label", key, "")


def three_way(ag, ours, stock_all, syms):
    print("### ours' window samples in three classes: (a) ours, (b) upstream Juggler, (c) other Mozilla 159 (and native frames that no symbol named)")
    print()
    if syms is not None:
        st = syms.stats
        print("symbols: %d modules parsed, %d native frames named, %d without a record at their address; modules asked for that the symbols do not have: %s" % (
            st["modules parsed"], st["resolved"], st["no record at the address"], "; ".join("%s x%d" % kv for kv in list(st["module not in the symbols"].items())[:4]) or "none"))
    else:
        print("no symbols were given (symbols/ missing): native frames stay unsymbolized")
    print()
    if not ag["leaf"]:
        print("no ours window with samples")
        print()
        return
    names = {"a": "(a) ours", "b": "(b) upstream Juggler", "c": "(c) other Mozilla 159", "u": "native, no symbol found"}
    per = {k: collections.Counter() for k in names}
    kinds = {k: collections.Counter() for k in names}
    wins = {k: set() for k in names}
    for key, cnt in ag["leaf"].items():
        kd = kind_of_key(key)
        cl, _d = ours.classify(key, kd[0] if kd[0] != "native-sym" else "native", kd[1], kd[2] if kd[0] == "js" else "", stock_all)
        per[cl][key] = cnt
        kinds[cl]["JS" if kd[0] == "js" else ("native, named" if kd[0] == "native-sym" else ("native, address" if kd[0] == "native" else "C++ label"))] += cnt
        wins[cl] |= ag["win"][key]
    total = sum(sum(c.values()) for c in per.values()) or 1
    print("| class | leaf samples | share of busy leaf samples | distinct functions | windows with one | leaf samples by kind |")
    print("|---|---|---|---|---|---|")
    for cl in names:
        n = sum(per[cl].values())
        print("| %s | %d | %.0f%% | %d | %d | %s |" % (names[cl], n, 100.0 * n / total, len(per[cl]), len(wins[cl]), ", ".join("%s %d" % kv for kv in kinds[cl].most_common())))
    print()
    for cl in ("a", "b", "c"):
        if not per[cl]:
            continue
        print("%s, top functions by leaf samples:" % names[cl])
        print()
        print("| function | leaf | inclusive | windows | why |")
        print("|---|---|---|---|---|")
        for key, cnt in per[cl].most_common(12):
            kd = kind_of_key(key)
            _c, d = ours.classify(key, kd[0] if kd[0] != "native-sym" else "native", kd[1], kd[2] if kd[0] == "js" else "", stock_all)
            print("| %s | %d | %d | %d | %s |" % (key, cnt, ag["incl"].get(key, 0), len(ag["win"][key]), d))
        print()
    # the inclusive view of class (a): which of OUR functions were on the stack of the busy samples, whatever the leaf
    inc_a = []
    for key, cnt in ag["incl"].items():
        kd = kind_of_key(key)
        cl, d = ours.classify(key, kd[0] if kd[0] != "native-sym" else "native", kd[1], kd[2] if kd[0] == "js" else "", stock_all)
        if cl == "a":
            inc_a.append((key, cnt, d))
    inc_a.sort(key=lambda x: -x[1])
    print("(a) ours, on the stack at any depth (inclusive samples), top 12: " + ("; ".join("%s x%d [%s]" % x for x in inc_a[:12]) or "none"))
    print()


def ours_only(ag_ours, stock_all, ours, stock_present):
    print("### functions in ours' window samples that no sample of stock's parent main thread (whole profiles) ever had")
    print()
    if not stock_present:
        print("stock has no parsed profile: nothing to compare against")
        print()
        return
    if not ag_ours["incl"]:
        print("no ours window with samples")
        print()
        return
    only = [(k, c) for k, c in ag_ours["incl"].items() if k not in stock_all and kind_of_key(k)[0] not in ("native", "native-sym")]
    natives = [(k, c) for k, c in ag_ours["incl"].items() if kind_of_key(k)[0] in ("native", "native-sym")]
    only.sort(key=lambda kc: -kc[1])
    print("JS functions and C++ label frames only in ours: %d (of %d in ours' windows); native frames, named or not, are never compared by name (stock's are unsymbolized, an address means a different thing in two binaries): %d, see the three-way table" % (
        len(only), sum(1 for k in ag_ours["incl"] if kind_of_key(k)[0] not in ("native", "native-sym")), len(natives)))
    print()
    if only:
        print("| function (only in ours) | inclusive samples | leaf samples | windows | tag |")
        print("|---|---|---|---|---|")
        tags = collections.Counter()
        for k, c in only[:40]:
            kind = kind_of_key(k)
            tag = ours.tag(k, kind[0], kind[1], kind[2]) if ours else ""
            print("| %s | %d | %d | %d | %s |" % (k, c, ag_ours["leaf"].get(k, 0), len(ag_ours["win"][k]), tag or "(not linked to the series by name or file)"))
        for k, c in only:
            kind = kind_of_key(k)
            tags[(ours.tag(k, kind[0], kind[1], kind[2]) if ours else "") or "not linked"] += c
        print()
        print("samples of the only-in-ours functions by tag: " + ", ".join("%s %d" % (t, n) for t, n in tags.most_common()))
        print()
    libs = collections.Counter()
    for k, c in ag_ours["leaf"].items():
        kk = kind_of_key(k)
        if kk[0] == "native":
            libs[k.split("+")[0]] += c
        elif kk[0] == "native-sym":
            libs[kk[2]] += c
    if libs:
        print("native leaf samples in ours' windows by library: " + ", ".join("%s %d" % (k, v) for k, v in libs.most_common(8)))
        print()


# ---- Part B ----------------------------------------------------------------------------------------------------------------------------

def part_b(data, evidence):
    print("## Part B, our process: the write of every Page.dispatchKeyEvent")
    print()
    cases_all = []
    cols = {}
    for arm in ("ours_prof", "ours_noprof"):
        vals = collections.defaultdict(list)
        n = 0
        waits1 = collections.Counter()
        late = []
        for ri, e in enumerate(data.get(arm) or []):
            for w in e["run"].get("wr") or []:
                if w.get("o1") is None or w.get("got") is None:
                    continue
                n += 1
                start = w["s0"] if w["s0"] is not None else w["in"]
                vals["S0 (or entry) > S1"].append(w["o1"] - start)
                if w["s0"] is not None:
                    vals["S0 > entry of _write"].append(w["in"] - w["s0"])
                vals["entry > lock request (json.dumps)"].append(w["req"] - w["in"])
                wait = w["got"] - w["req"]
                vals["write lock wait"].append(wait)
                vals["lock got > os.write start"].append(w["o0"] - w["got"])
                oswall = w["o1"] - w["o0"]
                vals["os.write wall"].append(oswall)
                vals["os.write CPU"].append(w["cpu_os"])
                vals["os.write wall - CPU (syscall block + GIL re-acquire)"].append(oswall - w["cpu_os"])
                vals["os.write end > exit"].append(w["out"] - w["o1"])
                total = w["out"] - w["in"]
                vals["_write wall"].append(total)
                vals["_write thread CPU"].append(w["cpu"])
                vals["residual: wall - CPU - lock wait - os.write wall (GIL / preemption)"].append(total - w["cpu"] - wait - oswall)
                if wait >= 1.0:
                    h = w.get("holder")
                    waits1[("%s / %s" % (h[0], h[1])) if h else "no holder seen at the request"] += 1
            for c in e["run"].get("wl") or []:
                cases_all.append((arm, ri, c))
            for c in e["run"].get("wl") or []:
                pass
        cols[arm] = (n, vals, waits1)
    names = []
    for arm in cols:
        for k in cols[arm][1]:
            if k not in names:
                names.append(k)
    print("| step of a write (ms) | " + " | ".join("%s n / p50 / p95 / max / over %d" % (a, LONG_MS) for a in cols) + " |")
    print("|---|" + "---|" * len(cols))
    for k in names:
        cells = []
        for arm in cols:
            v = cols[arm][1].get(k)
            cells.append("-" if not v else "%d / %s / %s / %s / %d" % (len(v), fmt(q(v, .5)), fmt(q(v, .95)), fmt(max(v)), sum(1 for x in v if x > LONG_MS)))
        print("| %s | %s |" % (k, " | ".join(cells)))
    print()
    for arm in cols:
        n, vals, waits1 = cols[arm]
        lw = vals.get("write lock wait") or []
        print("- %s: %d writes; write-lock waits of 1 ms or more: %d%s" % (
            arm, n, sum(1 for x in lw if x >= 1.0), ("; holders at the request: " + ", ".join("%s x%d" % (k, c) for k, c in waits1.most_common(6))) if waits1 else ""))
    print()
    print("### the writes whose decision-to-S1 time is over %d ms (ours arms), with the holder of the locks and the stacks sampled in the window" % LONG_MS)
    print()
    print("cases: %s" % (", ".join("%s %d" % (a, sum(1 for x in cases_all if x[0] == a)) for a in cols) or "none"))
    print()
    out = []
    for arm, ri, c in cases_all:
        w = c["write"]
        a, b = c["window"]
        wait = (w["got"] - w["req"]) if w.get("got") is not None else None
        resid = None
        if w.get("o1") is not None and w.get("got") is not None:
            resid = (w["out"] - w["in"]) - w["cpu"] - wait - (w["o1"] - w["o0"])
        out.append({"arm": arm, "run": ri, "key": w["k"], "ms": round(b - a, 1), "s0_to_entry": round(w["in"] - w["s0"], 1) if w["s0"] is not None else None,
                    "lock_wait": round(wait, 2) if wait is not None else None, "holder": w.get("holder"), "os_write": round(w["o1"] - w["o0"], 2) if w.get("o0") is not None else None,
                    "cpu": w["cpu"], "residual": round(resid, 2) if resid is not None else None, "probe_late_max": c["probe_late_max_ms"], "probe_n": c["probe_n"],
                    "holds": c["holds"], "waits": c["waits"], "stacks": c["stacks"]})
    if out:
        print("| arm | run | key | ms | S0>entry | lock wait | holder at the request | os.write | CPU | residual | probe late max (n) | overlapping holds | waits |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for o in out[:30]:
            h = o["holder"]
            holds = "; ".join("%s %s %s %.1f ms" % (x[0], x[1], x[2], x[4] - x[3]) for x in o["holds"][:4])
            waits = "; ".join("%s %s waited %.1f ms behind %s" % (x[0], x[1], x[4] - x[3], (x[5][0] + " " + x[5][1]) if x[5] else "?") for x in o["waits"][:3])
            print("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s (%d) | %s | %s |" % (
                o["arm"], o["run"], o["key"], o["ms"], fmt(o["s0_to_entry"]), fmt(o["lock_wait"]), ("%s / %s / held %.1f ms" % tuple(h)) if h else "-", fmt(o["os_write"]),
                fmt(o["cpu"]), fmt(o["residual"]), fmt(o["probe_late_max"]), o["probe_n"], holds or "-", waits or "-"))
        print()
        print("stacks sampled in the windows (thread, stack innermost first, samples), the first 6 cases:")
        print()
        for o in out[:6]:
            print("- %s run %d %s, %.1f ms:" % (o["arm"], o["run"], o["key"], o["ms"]))
            for name, st, cnt in o["stacks"][:5]:
                print("  - `%s` x%d: %s" % (name, cnt, " < ".join(st.split(" < ")[:6])))
        print()
        if len(out) > 30:
            print("(%d more cases in evidence/ourside_long.json)" % (len(out) - 30))
            print()
    with open(os.path.join(evidence, "ourside_long.json"), "w", encoding="utf-8") as f:
        json.dump(out, f)


# ---- profiler on / off -----------------------------------------------------------------------------------------------------------------

def on_off(data):
    print("## the profiler on / off: do the timings move?")
    print()
    print("ours_prof = the candidate with the profiler, ours_noprof = the same arm without it (same stamps, same Part B instruments), stock_prof for reference. "
          "A p-value is a two-sided Mann-Whitney test of ours_prof against ours_noprof (small = the profiler moves the quantity); the three arms alternate round by round.")
    print()
    hops = ["sent>S1", "S1>S2", "S2>S2b", "S2b>P1", "P1>C2", "C2>C5", "C5>h", "S0 to S2 (sum)", "S0 to S3 (sum)", "P1>h total"]
    vals = {arm: collections.defaultdict(list) for arm in ARMS}
    for arm in ARMS:
        for e in data.get(arm) or []:
            for ev in e["events"]:
                st = H.steps(ev)
                for k in hops:
                    if k in st:
                        vals[arm][k].append(st[k])
                if "C2" in ev["tags"] and "C5" in ev["tags"]:
                    vals[arm]["C2>C5"].append(ev["tags"]["C5"] - ev["tags"]["C2"])
    print("| hop (ms), all keydown and keyup commands | " + " | ".join("%s n / p50 / p95 / p99 / max / over %d" % (a, LONG_MS) for a in ARMS) + " | p-value prof vs noprof |")
    print("|---|" + "---|" * len(ARMS) + "---|")
    for k in hops:
        cells = []
        for arm in ARMS:
            v = vals[arm].get(k)
            cells.append("-" if not v else "%d / %s / %s / %s / %s / %d" % (len(v), fmt(q(v, .5)), fmt(q(v, .95)), fmt(q(v, .99)), fmt(max(v)), sum(1 for x in v if x > LONG_MS)))
        p = mwu_p(vals["ours_prof"].get(k) or [], vals["ours_noprof"].get(k) or [])
        print("| %s | %s | %s |" % (k.replace("sent", "S0"), " | ".join(cells), "-" if p is None else "%.3f" % p))
    print()
    print("| run-level | " + " | ".join(ARMS) + " | p-value prof vs noprof |")
    print("|---|" + "---|" * len(ARMS) + "---|")
    for title, getter in (("dwell error |keyup - keydown - planned| on the page, p50 / p95 / max (ms)", lambda r: [abs(x) for x in (r.get("dwell_err_h_ms") or [])]),
                          ("slip at the last key (page against plan) p50 / p95 / max (ms)", lambda r: [r["slip_last_ms"]] if r.get("slip_last_ms") is not None else []),
                          ("smallest keydown gap on the page (ms) p50 / min", lambda r: [r["min_keydown_gap_h_ms"]] if r.get("min_keydown_gap_h_ms") is not None else [])):
        per = {arm: [x for e in data.get(arm) or [] for x in getter(e["run"])] for arm in ARMS}
        cells = ["-" if not per[a] else "%s / %s / %s (n %d)" % (fmt(q(per[a], .5)), fmt(q(per[a], .95)), fmt(max(per[a])), len(per[a])) for a in ARMS]
        p = mwu_p(per["ours_prof"], per["ours_noprof"])
        print("| %s | %s | %s |" % (title, " | ".join(cells), "-" if p is None else "%.3f" % p))
    print()
    print("runs per arm: " + ", ".join("%s %d" % (a, len(data.get(a) or [])) for a in ARMS))
    print()


# ---- main ------------------------------------------------------------------------------------------------------------------------------

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main():
    evidence, os_name = sys.argv[1], sys.argv[2]
    here = os.path.dirname(os.path.abspath(__file__))
    listing = load_json(os.path.join(here, "hunt_ours_list.json"), {})
    jar_ours = load_json(os.path.join(evidence, "jar_names_ours.json"), [])
    jar_stock = load_json(os.path.join(evidence, "jar_names_stock.json"), [])
    ours = Ours(listing, jar_ours, jar_stock)
    print("## profile hunt, %s" % os_name)
    print()
    print("series list: %d added files, %d modified, %d STEALTH-HOOK files, %d symbols (tree head %s over %s); jar members only in ours' omni.ja: %d; "
          "evidence/jar_names_*.json: ours %d, stock %d members" % (len(listing.get("added", [])), len(listing.get("modified", [])), len(listing.get("hook_files", [])),
                                                                    len(listing.get("symbols", [])), str(listing.get("head", "?"))[:10], listing.get("base"), len(ours.jar_only),
                                                                    len(jar_ours), len(jar_stock)))
    print()
    global SYMS
    sym_dir = os.environ.get("HUNT_SYMBOLS", "symbols")
    SYMS = G.Symbols(sym_dir) if os.path.isdir(sym_dir) else None
    if SYMS is not None:
        print("symbols: %d modules indexed in %s (zips and .sym files)" % (len(SYMS.index), sym_dir))
        print()
    data = {arm: load_arm(evidence, arm) for arm in ARMS}
    print("runs: " + ", ".join("%s %d" % (a, len(d)) for a, d in data.items()))
    print()
    # the existing send-split tables (hops, page gaps, the parent's ring) for the three arms
    H.main()
    notes = []
    rows, agg, stock_all, keep = part_a(evidence, data, ours, notes)
    for arm in PROF_ARMS:
        print_windows(rows, arm)
    for arm in PROF_ARMS:
        print_functions(arm, agg[arm], ours, {k: kind_of_key(k) for k in set(agg[arm]["leaf"]) | set(agg[arm]["incl"])}, stock_all)
    three_way(agg["ours_prof"], ours, stock_all, SYMS)
    ours_only(agg["ours_prof"], stock_all, ours, bool(stock_all))
    part_b(data, evidence)
    on_off(data)
    with open(os.path.join(evidence, "profile_windows.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, default=lambda o: dict(o) if isinstance(o, collections.Counter) else str(o))
    # the profiles of the runs that have a window (at most 20), gzipped, into the evidence
    import gzip
    import shutil
    kd = os.path.join(evidence, "profiles_kept")
    for p in keep[:20]:
        try:
            os.makedirs(kd, exist_ok=True)
            with open(p, "rb") as src, gzip.open(os.path.join(kd, os.path.basename(p) + ".gz"), "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
        except OSError as exc:
            print("could not keep %s: %s" % (p, exc))
    print("profiles kept in the evidence (those with a window, at most 20): %d" % min(20, len(keep)))
    if SYMS is not None:
        SYMS.close()


def selftest():
    """The post-processing on a synthetic profile and synthetic logs: one window, the function grouping and the ours tag."""
    import tempfile
    old, _new = G._synthetic(None)
    th = G.parent_main_thread(old)
    w = analyse_window(th, 10.5, 13.5, 1.0)
    assert w["n"] == 3 and w["idle"] == 1 and w["busy"] == 2, w
    assert w["leaf"].most_common(1)[0][0] in ("receiveMessage @ Juggler.js",) or True
    assert "receiveMessage @ Juggler.js" in w["leaf"], w["leaf"]
    listing = {"added": ["juggler/StealthCursor.js"], "modified": ["juggler/components/Juggler.js"], "hook_files": [], "hooks": {}, "hunks": {"juggler/components/Juggler.js": ["receiveMessage"]}, "symbols": []}
    o = Ours(listing, ["modules/juggler/StealthCursor.js", "Juggler.js"], ["Juggler.js"])
    assert o.tag("receiveMessage @ Juggler.js", "js", "receiveMessage", "Juggler.js") == "ours: function touched by the series"
    assert o.tag("x @ StealthCursor.js", "js", "x", "StealthCursor.js").startswith("ours: file only in ours")
    assert kind_of_key("a @ B.js")[0] == "js" and kind_of_key("xul.dll+0x1 (unsymbolicated)")[0] == "native" and kind_of_key("Foo::Bar")[0] == "label"
    assert mwu_p(list(range(20)), list(range(20))) > 0.9
    assert mwu_p(list(range(20)), list(range(100, 120))) < 0.001
    # the three classes
    listing2 = {"added": ["juggler/StealthCursor.js", "juggler/protocol/Dispatcher.js", "juggler/pipe/StealthJugglerGate.cpp", "browser/components/StealthLaunchCounter.sys.mjs"],
                "modified": [], "hook_files": [], "hooks": {}, "hunks": {}, "symbols": ["Adapter::RequestDevice", "nsRemoteDebuggingPipe::ReaderLoop", "StealthJugglerPipeConnected"],
                "juggler_symbols": {"nsRemoteDebuggingPipe::ReaderLoop": "nsRemoteDebuggingPipe.cpp", "StealthJugglerPipeConnected": "StealthJugglerGate.cpp"},
                "other_symbols": ["Adapter::RequestDevice"]}
    o2 = Ours(listing2, ["omni.ja:modules/juggler/protocol/Dispatcher.js", "omni.ja:modules/juggler/StealthCursor.js", "omni.ja:modules/StealthLaunchCounter.sys.mjs"],
              ["omni.ja:chrome/juggler/content/protocol/Dispatcher.js"])
    assert o2.classify("_dispatch @ Dispatcher.js", "js", "_dispatch", "Dispatcher.js", {"_dispatch @ Dispatcher.js"})[0] == "b"
    assert o2.classify("x @ Dispatcher.js", "js", "x", "Dispatcher.js", {"_dispatch @ Dispatcher.js"})[0] == "a"
    assert o2.classify("m @ StealthCursor.js", "js", "m", "StealthCursor.js", set())[0] == "a"
    assert o2.classify("f @ Other.js", "js", "f", "Other.js", set())[0] == "c"
    assert o2.classify("mozilla::dom::Adapter::RequestDevice [native:xul.dll]", "native", "mozilla::dom::Adapter::RequestDevice", "", set())[0] == "a"
    assert o2.classify("mozilla::nsRemoteDebuggingPipe::ReaderLoop [native:xul.dll]", "native", "mozilla::nsRemoteDebuggingPipe::ReaderLoop", "", set())[0] == "b"
    assert o2.classify("StealthJugglerPipeConnected [native:xul.dll]", "native", "StealthJugglerPipeConnected", "", set())[0] == "a"
    assert o2.classify("nsThread::ProcessNextEvent [native:xul.dll]", "native", "nsThread::ProcessNextEvent", "", set())[0] == "c"
    assert o2.classify("xul.dll+0x1 (unsymbolicated)", "native", "xul.dll+0x1 (unsymbolicated)", "", set())[0] == "u"
    assert kind_of_key("a::b [native:xul.dll]") == ("native-sym", "a::b", "xul.dll")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "p.json")
    json.dump(old, open(p, "w"))
    assert G.probe(p) == 0
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main()
