#!/usr/bin/env python3
"""CI host gate: what a page reads is a function of the declared identity and of
Firefox's code, never of the machine the browser runs on.

Two properties follow, and they are checked for every value the page dumps
(whole APIs, not a hand-picked list), so the gate needs no knowledge of where
the next leak will be:

  1. HOST-INDEPENDENCE. The same identity on different hosts (the Windows,
     Linux x86_64 and Linux arm64 legs) gives the same bytes.
  2. RETAIL EQUALITY. A value that does not depend on the identity equals what
     retail Firefox for Windows answers on the same page, at the same device
     pixel ratio.

Values are compared leaf by leaf: an object the page reads (navigator, screen)
mixes fields the identity decides with fields it does not, and each is held to
its own property.

Which values depend on the identity is measured AND declared. Each leg visits
the page with several seeds at a fixed DPR; a value that changes with the seed
depends on the identity and is held to property 1 only. A value the identity
decides can still come out the same for every seed (16 cores, a 1920x1080
screen), so those are declared as well.

The only exceptions live in host_gate/exceptions.json, each with its reason:
  - identity: values the declared identity decides; exempt from property 2,
    never from property 1;
  - noise: values that differ between two launches of retail itself;
  - network: values the identity takes from the network (language and zone
    from the egress IP), which CI without a proxy cannot reproduce;
  - measurement: values that record how the reference was taken rather than
    what Firefox answers (focus, on the hidden desktop retail ran on);
  - known_defects: open divergences with the work that closes them. This list
    only shrinks: an entry that no longer matches a difference fails the gate,
    so it is removed when its fix lands.

This replaces ci_font_gate.py, which checked one surface (the font list) of
the same rule.

    python ci_host_gate.py dump <firefox> --leg linux-x86_64 --out dumps/
    python ci_host_gate.py compare dumps/          (reference: host_gate/reference/firefox-159)
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import gzip
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent / "host_gate"
PAGE = HERE / "host_gate_page.html"
EXCEPTIONS = HERE / "exceptions.json"
REFERENCE = HERE / "reference" / "firefox-159"
FORMAT = "host-gate/1"

#: DPRs the reference is recorded at, and the seeds visited at each. Three seeds
#: so that a value that depends on the identity is very unlikely to look
#: constant by chance.
DPRS = (1.0, 1.5)
SEEDS = (11, 12, 13)

#: The leg every other leg is compared with: the platform the identity claims.
ANCHOR = "win-x86_64"


# -
# Dump: one leg, every (dpr, seed) visit, the way the reference was measured:
# the page over http://127.0.0.1 (a secure context), en-US, the zone the retail
# reference reported (retail on Windows ignores TZ and answers the machine's),
# the device pixel ratio through layout.css.devPixelsPerPx, no drawn cursor.

class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # the page also posts its result; the dump reads it from the page
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def reference_items(reference: Path, dpr: float) -> dict | None:
    """Retail's items at one DPR, from dpr<x>.json.gz (or a plain dpr<x>.json)."""
    for name in ("dpr%s.json.gz" % dpr, "dpr%s.json" % dpr):
        path = reference / name
        if path.is_file():
            raw = path.read_bytes()
            return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)["items"]
    return None


def reference_zone(reference: Path) -> str:
    return reference_items(reference, 1.0)["surface.intl"]["DateTimeFormat"]["timeZone"]


def dump(firefox: str, leg: str, out: Path, reference: Path, timeout_ms: int = 180_000) -> int:
    from invisible_playwright import InvisiblePlaywright

    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    zone = reference_zone(reference)
    try:
        for dpr in DPRS:
            for seed in SEEDS:
                with InvisiblePlaywright(seed=seed, binary_path=firefox, headless=True, show_cursor=False,
                                         locale="en-US", timezone=zone,
                                         extra_prefs={"layout.css.devPixelsPerPx": str(dpr)}) as browser:
                    page = browser.new_context().new_page()
                    page.goto("http://127.0.0.1:%d/?dpr%s" % (server.server_address[1], dpr))
                    page.wait_for_function("document.title === 'done'", timeout=timeout_ms)
                    result = page.evaluate("window.__hostGate")
                if result.get("format") != FORMAT:
                    print("the page answered format %r, not %r" % (result.get("format"), FORMAT))
                    return 2
                path = out / ("%s_dpr%s_seed%d.json" % (leg, dpr, seed))
                path.write_text(json.dumps({"leg": leg, "dpr": dpr, "seed": seed, "result": result}),
                                encoding="utf-8")
                print("dumped %s: %d items" % (path.name, len(result["items"])))
    finally:
        server.shutdown()
    return 0


# -
# Compare: every leg, every visit, against each other and against retail.

KINDS = ("identity", "noise", "network", "measurement", "known_defects")


def load_exceptions(path: Path = EXCEPTIONS) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - set(KINDS)
    if unknown:
        raise SystemExit("exceptions.json: unknown kinds %s" % sorted(unknown))
    for kind in KINDS:
        for entry in data.get(kind, []):
            if not entry.get("keys") or not entry.get("why"):
                raise SystemExit("exceptions.json: every %s entry needs keys and why" % kind)
    return data


def matching(entries: list, key: str, leg: str | None = None) -> list:
    return [e for e in entries
            if any(fnmatch.fnmatchcase(key, p) for p in e["keys"])
            and (leg is None or not e.get("legs") or leg in e["legs"])]


def is_image(value) -> bool:
    return isinstance(value, dict) and "data" in value and "enc" in value


def leaves(items: dict) -> dict:
    """Every value as a leaf with a dotted key; an image stays one value."""
    out = {}

    def walk(key, value):
        if isinstance(value, dict) and value and not is_image(value):
            for k, v in value.items():
                walk("%s.%s" % (key, k), v)
        else:
            out[key] = value

    for key, value in items.items():
        walk(key, value)
    return out


def describe(a, b) -> str:
    """A short account of a difference; for images, how many bytes differ."""
    if isinstance(a, dict) and isinstance(b, dict) and "data" in a and "data" in b:
        x, y = base64.b64decode(a["data"]), base64.b64decode(b["data"])
        if len(x) != len(y) or (a.get("w"), a.get("h")) != (b.get("w"), b.get("h")):
            return "image %sx%s vs %sx%s" % (a.get("w"), a.get("h"), b.get("w"), b.get("h"))
        diff = [abs(p - q) for p, q in zip(x, y) if p != q]
        return "image: %d of %d bytes differ, max delta %d" % (len(diff), len(x), max(diff, default=0))
    text = lambda v: json.dumps(v, sort_keys=True)[:120]
    return "%s vs %s" % (text(a), text(b))


def compare(dumps: Path, reference: Path, exceptions: dict) -> list:
    """Every failure as one line. Empty means the gate passes."""
    visits = {}   # (leg, dpr, seed) -> items
    for path in sorted(dumps.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        visits[(d["leg"], d["dpr"], d["seed"])] = leaves(d["result"]["items"])
    if not visits:
        return ["no dumps in %s" % dumps]
    legs = sorted({k[0] for k in visits})
    refs = {}
    for dpr in DPRS:
        items = reference_items(reference, dpr)
        if items is None:
            return ["no retail reference for dpr %s in %s" % (dpr, reference)]
        refs[dpr] = leaves(items)

    identity = exceptions.get("identity", [])
    excusable = exceptions.get("noise", []) + exceptions.get("network", []) + exceptions.get("measurement", [])
    known = exceptions.get("known_defects", [])
    used = set()
    failures = []

    def excused(key, leg):
        if matching(excusable, key):
            return True
        hit = matching(known, key, leg)
        for e in hit:
            used.add(id(e))
        return bool(hit)

    for dpr in DPRS:
        for leg in legs:
            missing = [s for s in SEEDS if (leg, dpr, s) not in visits]
            if missing:
                failures.append("%s dpr %s: no dump for seeds %s" % (leg, dpr, missing))
        present = [(leg, s) for leg in legs for s in SEEDS if (leg, dpr, s) in visits]
        keys = sorted(set().union(*(visits[(l, dpr, s)].keys() for l, s in present)) | set(refs[dpr]))
        for key in keys:
            # 1. host-independence: for each seed, every leg gives the Windows
            # leg's value. Windows is the anchor because it is the platform the
            # identity claims: a leg that differs from it is the one that moved.
            for seed in SEEDS:
                values = {leg: visits[(leg, dpr, seed)].get(key, "<missing>")
                          for leg in legs if (leg, dpr, seed) in visits}
                base = ANCHOR if ANCHOR in values else next(iter(values), None)
                for leg, value in values.items():
                    if leg != base and value != values[base] and not excused(key, leg):
                        failures.append("host  %s dpr %s seed %d: %s differs from %s: %s"
                                        % (key, dpr, seed, leg, base, describe(value, values[base])))
            # 2. retail equality, for a value that is the same for every seed
            for leg in legs:
                per_seed = [visits[(leg, dpr, s)].get(key, "<missing>")
                            for s in SEEDS if (leg, dpr, s) in visits]
                if len({json.dumps(v, sort_keys=True) for v in per_seed}) != 1 or matching(identity, key):
                    continue  # depends on the identity: property 1 only
                retail = refs[dpr].get(key, "<missing>")
                if per_seed[0] != retail and not excused(key, leg):
                    failures.append("retail %s dpr %s on %s: %s"
                                    % (key, dpr, leg, describe(per_seed[0], retail)))

    for e in known:
        if id(e) not in used:
            failures.append("known defect no longer seen, remove it from exceptions.json: %s (%s)"
                            % (e["keys"], e["why"]))
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("firefox")
    d.add_argument("--leg", required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--reference", type=Path, default=REFERENCE)
    c = sub.add_parser("compare")
    c.add_argument("dumps", type=Path)
    c.add_argument("--reference", type=Path, default=REFERENCE)
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        return dump(args.firefox, args.leg, args.out, args.reference)
    failures = compare(args.dumps, args.reference, load_exceptions())
    for f in failures:
        print("FAIL " + f)
    print("HOST GATE %s: %d failures" % ("OK" if not failures else "FAILED", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
