"""Tally of the rows webgl_first_context_probe.py wrote: per mode, the rounds, the nulls, the failed launches and the browser's reasons."""
import collections
import glob
import json
import sys

folder = sys.argv[1] if len(sys.argv) > 1 else "evidence"
rows = []
for f in glob.glob(folder + "/rows_p*.jsonl"):
    for line in open(f, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
by = collections.defaultdict(collections.Counter)
reasons = collections.defaultdict(collections.Counter)
versions = {}
for r in rows:
    m = r["mode"]
    if "exception" in r:
        by[m]["launch failed"] += 1
        continue
    by[m]["rounds"] += 1
    by[m]["webgl null"] += (not r["webgl"])
    by[m]["webgl2 null"] += (not r["webgl2"])
    for e in r.get("errors", []):
        reasons[m][e[:160]] += 1
    if r.get("browser_version"):
        versions[m] = r["browser_version"]
print("%-14s %8s %10s %11s %13s  browser" % ("mode", "rounds", "webgl null", "webgl2 null", "launch failed"))
for m in ("ours", "stock-headed", "stock-headless", "retail-headed"):
    c = by[m]
    print("%-14s %8d %10d %11d %13d  %s" % (m, c["rounds"], c["webgl null"], c["webgl2 null"], c["launch failed"], versions.get(m, "")))
for m, c in reasons.items():
    for e, n in c.most_common(5):
        print("reason (%s) x%d: %s" % (m, n, e))
