"""The table of the background traffic: python scripts/bg_traffic_table.py DIR  (the files bg_traffic.py wrote)

For each proxy kind: one row per destination (the name when it is known, else host:port), the arms that contacted it (ours / retail / retail-gd / stock) as
"runs seen in / runs, median connections per run, median bytes per run", then the lists a reader asks for: what OURS does and the plain retail does not, what the retail does and ours
does not. A destination is the SNI of the TLS flight, else the name the lab's DoH gave the address, else the CONNECT name, else the address. Local pages and the lab's DoH are not
destinations. Only events after the page was loaded count (the start of the browser is part of the first seconds): `all` adds the ones before.
"""
import collections
import glob
import json
import os
import statistics
import sys

d = sys.argv[1]
ARMS = ("ours", "retail", "retail-gd", "stock")
runs = collections.defaultdict(list)          # (kind, arm) -> [result]
for path in sorted(glob.glob(os.path.join(d, "*.json"))):
    r = json.load(open(path))
    if r.get("events") is not None:
        runs[(r["kind"], r["arm"])].append(r)


def destination(e):
    host = e.get("sni") or e.get("name") or e["host"]
    return "%s:%s" % (host, e["port"])


def per_run(rs, after_load=True):
    out = []
    for r in rs:
        c = collections.defaultdict(lambda: [0, 0])
        for e in r["events"]:
            if e["kind"] not in ("connect", "get"):
                continue
            if after_load and r["loaded_at"] is not None and e["t"] < r["loaded_at"]:
                continue
            k = destination(e)
            c[k][0] += 1
            c[k][1] += e.get("bytes", 0)
        out.append(c)
    return out


for kind in ("http", "socks"):
    arms = {a: runs[(kind, a)] for a in ARMS if runs.get((kind, a))}
    if not arms:
        continue
    print("=" * 100)
    print("PROXY: %s   (runs per arm: %s; errors: %s)" % (kind, {a: len(v) for a, v in arms.items()},
                                                          {a: [r["error"] for r in v if r["error"]] for a, v in arms.items() if any(r["error"] for r in v)}))
    for after in (True, False):
        pr = {a: per_run(v, after) for a, v in arms.items()}
        dests = sorted(set().union(*[set(c) for a in pr for c in pr[a]]))
        print("-" * 100)
        print("%s the page loaded; cell = runs seen in / runs, connections per run (median), bytes the client sent per run (median)" % ("AFTER" if after else "INCLUDING THE START OF THE BROWSER, before"))
        print("%-48s" % "destination" + "".join("%-26s" % a for a in arms))
        seen = collections.defaultdict(dict)
        for dest in dests:
            row = "%-48s" % dest[:47]
            for a in arms:
                cs = [c[dest] for c in pr[a] if dest in c]
                seen[dest][a] = len(cs)
                row += "%-26s" % (("%d/%d  %3d  %7d" % (len(cs), len(pr[a]), statistics.median(x[0] for x in cs), statistics.median(x[1] for x in cs))) if cs else "-")
            print(row)
        if after:
            half = lambda a, dest: seen[dest].get(a, 0) * 2 >= max(len(pr.get(a, [1])), 1)     # in at least half the runs
            for left, right, label in (("ours", "retail", "OURS and not the plain retail"), ("retail", "ours", "the plain RETAIL and not ours"),
                                       ("retail-gd", "ours", "the retail driven by geckodriver and not ours"), ("stock", "ours", "the stock Playwright Firefox and not ours")):
                if left in arms and right in arms:
                    print("   %s: %s" % (label, [dest for dest in dests if half(left, dest) and not seen[dest].get(right, 0)] or "nothing"))
    doh = {a: sorted({x["name"] for r in v for x in r["doh"]}) for a, v in arms.items()}
    print("-" * 100)
    print("NAMES ASKED OF THE LAB'S DoH (name resolution of the browser, ours only: the others resolve by the proxy):", doh.get("ours", []))
