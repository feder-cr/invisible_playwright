#!/usr/bin/env python3
"""The single entry of the fake web (ci-release-18): our engine and Playwright's own Firefox on the same offline pages,
same client, same job. Red when our engine does not idle where stock does, and red as a broken control when stock does not
do what tests/fakeweb/pages.json declares.

    python scripts/run_fakeweb.py --binary PATH [--profile p2] [--patterns a,b] [--repetitions N] [--out FILE]

The pages are served by tests/fakeweb/sites.py (two origins on 127.0.0.1, every response finite). Only profile P2 (one reused
page, the stock client over the served browser) exists so far.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from fakeweb import profile_p2, sites  # noqa: E402

# The patterns that took a routed, reused page to `networkidle` never: a request whose end the page never gets.
HARD = ["leaving_beacons_subframe", "leaving_beacons", "cancel_among_beacons"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--profile", default="p2", choices=["p2"])
    ap.add_argument("--patterns", default=",".join(HARD), help="comma separated; 'all' for every page")
    ap.add_argument("--repetitions", type=int, default=10)
    ap.add_argument("--idle-timeout-ms", type=int, default=10000)
    ap.add_argument("--no-stock", action="store_true", help="candidate only (the verdict then reads pages.json alone)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    import importlib

    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright   # the stock client, by name

    declared = json.loads((ROOT / "tests" / "fakeweb" / "pages.json").read_text(encoding="utf-8"))["stock_firefox"]
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
