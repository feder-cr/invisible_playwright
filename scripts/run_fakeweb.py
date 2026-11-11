#!/usr/bin/env python3
"""The single entry of the fake web (ci-release-18): our engine and Playwright's own Firefox on the same offline pages,
same client, same job. Red when our engine does not idle where stock does, and red as a broken control when stock does not
do what tests/fakeweb/pages.json declares.

    python scripts/run_fakeweb.py --binary PATH [--profile p2|p4] [--patterns a,b] [--repetitions N] [--out FILE]

Profile P2: one reused page loads hard pages (tests/fakeweb/sites.py, two origins on 127.0.0.1, every response finite);
red when our engine does not idle where stock does. Profile P4: a user journey of 17 steps over a small multi-page site
(tests/fakeweb/journey_site.py); stock Firefox runs first, then our engine, and the verdict compares every step field by
field (ok, url, title, marker, cookies, history length, open pages, idle, the step's own values). --repetitions is the
number of walks (P4: default 1). The stock client drives both.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from fakeweb import journey_site, profile_p2, profile_p4, sites  # noqa: E402

# The patterns that took a routed, reused page to `networkidle` never: a request whose end the page never gets.
HARD = ["leaving_beacons_subframe", "leaving_beacons", "cancel_among_beacons"]


def run_p4(args, sync_playwright, not_idle_steps):
    rounds = args.repetitions or 1
    kw = {"idle_timeout_ms": args.idle_timeout_ms or 15000}
    origin_a, origin_o, stop = journey_site.start((0, 0))
    try:     # stock first, then ours: the same invocation, one browser at a time
        stock = [] if args.no_stock else profile_p4.run_on_stock(sync_playwright, origin_a, origin_o, rounds, **kw)
        candidate = profile_p4.run_on_candidate(sync_playwright, args.binary, origin_a, origin_o, rounds, **kw)
    finally:
        stop()
    if args.no_stock:       # stock is what pages.json says it is
        stock = profile_p4.synthesize_stock(candidate, not_idle_steps)
    fails, broken = profile_p4.verdict(candidate, stock, not_idle_steps)
    report = {"profile": "p4", "rounds": rounds, "candidate": candidate, "stock": stock,
              "fails": fails, "broken_control": broken}
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("%-5s %-42s %-6s %-6s %s" % ("step", "name", "stock", "ours", "idle (stock/ours)"))
    stock_by = {(s["round"], s["n"]): s for s in stock}
    for c in candidate:
        s = stock_by.get((c["round"], c["n"]), {})
        diff = [f for r, n, f, _a, _b in fails if (r, n) == (c["round"], c["n"])]
        print("%-5s %-42s %-6s %-6s %s/%s%s" % (
            "%d.%d" % (c["round"], c["n"]), c["name"][:42], "ok" if s.get("ok") else "FAIL", "ok" if c["ok"] else "FAIL",
            "idle" if s.get("idle") else "busy", "idle" if c["idle"] else "busy",
            ("   DIFF: " + ",".join(diff)) if diff else ""))
    for r, n, field, want, got in fails:
        print("FAIL  round %d step %d %s: stock=%r candidate=%r" % (r, n, field, want, got))
    for r, n, field, err in broken:
        print("BROKEN CONTROL  stock step %d round %d (%s): %s" % (n, r, field, err))
    print("steps: %d, fails: %d, broken controls: %d" % (len(candidate), len(fails), len(broken)))
    return 1 if (fails or broken) else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--profile", default="p2", choices=["p2", "p4"])
    ap.add_argument("--patterns", default=",".join(HARD), help="comma separated; 'all' for every page")
    ap.add_argument("--repetitions", type=int, default=None, help="p2: loads per page (default 10); p4: walks (default 1)")
    ap.add_argument("--idle-timeout-ms", type=int, default=None, help="default 10000 (p2) / 15000 (p4)")
    ap.add_argument("--no-stock", action="store_true", help="candidate only (the verdict then reads pages.json alone)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    import importlib

    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright   # the stock client, by name

    pages = json.loads((ROOT / "tests" / "fakeweb" / "pages.json").read_text(encoding="utf-8"))
    declared = pages["stock_firefox"]
    if args.profile == "p4":
        return run_p4(args, sync_playwright, pages["journey_p4"]["not_idle_steps"])
    if args.repetitions is None:
        args.repetitions = 10
    if args.idle_timeout_ms is None:
        args.idle_timeout_ms = 10000
    patterns = [p for p in sites.PATTERNS if p != "quiet"] if args.patterns == "all" else args.patterns.split(",")
    unknown = [p for p in patterns if p not in sites.PATTERNS]
    if unknown:
        ap.error("unknown patterns: %s" % unknown)

    origin_a, _origin_o, stop = sites.start((0, 0))
    try:
        kw = {"idle_timeout_ms": args.idle_timeout_ms}
        candidate = profile_p2.run_on_candidate(sync_playwright, args.binary, origin_a, patterns, args.repetitions, **kw)
        stock = [] if args.no_stock else profile_p2.run_on_stock(sync_playwright, origin_a, patterns, args.repetitions, **kw)
    finally:
        stop()

    if args.no_stock:       # stock is what pages.json says it is
        stock = [dict(c, idle=declared.get(c["pattern"], "idle") == "idle") for c in candidate]
    fails, broken = profile_p2.verdict(candidate, stock, declared)
    report = {"profile": args.profile, "patterns": patterns, "repetitions": args.repetitions,
              "candidate": candidate, "stock": stock, "fails": fails, "broken_control": broken}
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    for key, err in fails:
        print("FAIL  round %d  %s%s: %s" % (key[0], key[1], " (canary after %s)" % key[3] if key[2] else "", err))
    for key, err in broken:
        print("BROKEN CONTROL  stock did not idle on %s round %d: %s" % (key[1], key[0], err))
    print("cells: %d, fails: %d, broken controls: %d" % (len(candidate), len(fails), len(broken)))
    return 1 if (fails or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
