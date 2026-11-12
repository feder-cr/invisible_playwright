#!/usr/bin/env python3
"""Reads the JSON that `scripts/run_fakeweb.py --profile p4` writes and prints Markdown for the job's step summary.

    python scripts/fakeweb_p4_report.py journey FILE   # per walk: step, stock ok/idle, ours ok/idle, equal or different
    python scripts/fakeweb_p4_report.py trace FILE     # a --trace walk of stock and of ours: request types the client reported, keepalive counts

journey: exit 0 when ours equals stock on every step of every walk whose control is sound; 1 when it differs on a step of a
sound walk; 2 when a control is broken (stock is not as pages.json declares), 3 for both. A walk with a broken control is not
judged: its differences are listed, but only the broken control is the verdict there.
trace: with a traced stock walk, ours must do what stock does: exit 0 when, for each kind, ours reported the same set of resource
types as stock and left no more server hits unreported; 1 otherwise; 2 when stock reported no sendBeacon (a broken control, not a
difference of ours). Without a traced stock walk (--no-stock): exit 0 when every sendBeacon request the client reported has the
type `beacon`, there is at least one, and the client reported at least as many keepalive requests as the server saw; 1 otherwise.
"""
from __future__ import annotations

import collections
import json
import sys
from urllib.parse import parse_qs, urlsplit


def _mark(rec):
    if rec is None:
        return "missing"
    return "%s / %s" % ("ok" if rec.get("ok") else "FAIL", "idle" if rec.get("idle") else "busy")


def journey(report):
    """Returns (markdown, exit code)."""
    cand, stock = report["candidate"], report["stock"]
    stock_by = {(s["round"], s["n"]): s for s in stock}
    differs = collections.defaultdict(list)
    for r, n, field, want, got in report["fails"]:
        differs[(r, n)].append((field, want, got))
    broken_rounds = sorted({r for r, _n, _f, _e in report["broken_control"]})
    rounds = sorted({c["round"] for c in cand})
    out = ["## Fake web, profile P4: %d walk(s), stock first, ours second" % len(rounds), ""]
    for r in rounds:
        state = "control BROKEN" if r in broken_rounds else "control sound"
        out += ["### Walk %d (%s)" % (r, state), "",
                "| step | name | stock ok / idle | ours ok / idle | |", "|---|---|---|---|---|"]
        for c in (c for c in cand if c["round"] == r):
            d = differs.get((r, c["n"]), [])
            verdict = "different (%s)" % ", ".join(f for f, _w, _g in d) if d else "equal"
            out.append("| %d | %s | %s | %s | %s |" % (c["n"], c["name"], _mark(stock_by.get((r, c["n"]))), _mark(c), verdict))
        out.append("")
    out += ["### FAIL lines", ""]
    if report["fails"]:
        out.append("```")
        out += ["FAIL  round %d step %d %s: stock=%r candidate=%r" % f for f in report["fails"]]
        out.append("```")
    else:
        out.append("none")
    out += ["", "### Control", ""]
    if report["broken_control"]:
        out.append("**BROKEN CONTROL**: stock Firefox is not as `tests/fakeweb/pages.json` declares.")
        out.append("```")
        out += ["BROKEN CONTROL  stock step %d round %d (%s): %s" % (n, r, f, e) for r, n, f, e in report["broken_control"]]
        out.append("```")
    else:
        out.append("sound: stock Firefox did what pages.json declares on every step of every walk.")
    code = (1 if any(r not in broken_rounds for r, _n in differs) else 0) | (2 if broken_rounds else 0)
    out += ["", "**Verdict: %s**" % {0: "green, ours equals stock on every step", 1: "RED, ours differs from stock",
                                     2: "RED, broken control (not a difference of ours)",
                                     3: "RED, ours differs from stock and a control is broken"}[code], ""]
    return "\n".join(out), code


def _kind(method, path, ident, n, src, t):
    """What a request is, from what the page's script sent: sendBeacon, a keepalive fetch of one of three shapes, or None.
    The keepalive POST to /beacon is told from sendBeacon by its parameters (the site's script: load n=3, pagehide n=2)."""
    if path == "/r" and ident.startswith(("ka", "cak")):
        return "keepalive fetch"
    if path == "/beacon" and method == "POST":
        if (src == "load" and n == "3") or (t == "pagehide" and n == "2"):
            return "keepalive POST"
        return "sendBeacon"
    if path == "/hold" and ident.startswith("kapark"):
        return "parked keepalive"
    return None


def _client_kind(event):
    sp = urlsplit(event["url"])
    q = {k: v[0] for k, v in parse_qs(sp.query, keep_blank_values=True).items()}
    return _kind(event["method"], sp.path, q.get("id", ""), q.get("n", ""), q.get("src", ""), q.get("t", ""))


def _server_kind(event):       # the site records `id` as the id or, failing that, the t parameter
    return _kind(event["method"], event["path"], event["id"], event["n"], event.get("src", ""), event["id"])


KINDS = ("sendBeacon", "keepalive POST", "keepalive fetch", "parked keepalive")


def _counts(records):
    """Per kind: the request types the client reported, and how many hits the server saw."""
    client = collections.defaultdict(collections.Counter)
    server = collections.Counter()
    for rec in records:
        for e in rec.get("client_events", []):
            if e["ev"] == "request":
                kind = _client_kind(e)
                if kind:
                    client[kind][e["type"]] += 1
        for e in rec.get("server_events", []):
            kind = _server_kind(e)
            if kind:
                server[kind] += 1
    return client, server


def _types(counter):
    return ", ".join("%s: %d" % (t, c) for t, c in sorted(counter.items())) or "-"


def _unreported(client, server, kind):
    return max(0, server[kind] - sum(client[kind].values()))


def trace(report):
    """Returns (markdown, exit code). With a real stock walk (its records carry client_events): ours must do what stock does.
    Without one (--no-stock, stock is synthesized): the absolute rules."""
    client, server = _counts(report["candidate"])
    stock_records = report.get("stock") or []
    has_stock = any("client_events" in rec for rec in stock_records)
    if not has_stock:
        return _trace_absolute(client, server)
    s_client, s_server = _counts(stock_records)
    out = ["## Request types, stock Firefox and ours (one traced walk each)", "",
           ("| request | stock: client reported | stock: by resource type | stock: server saw | "
            "ours: client reported | ours: by resource type | ours: server saw |"), "|---|---|---|---|---|---|---|"]
    for kind in KINDS:
        out.append("| %s | %d | %s | %d | %d | %s | %d |" % (
            kind, sum(s_client[kind].values()), _types(s_client[kind]), s_server[kind],
            sum(client[kind].values()), _types(client[kind]), server[kind]))
    broken, problems = [], []
    if not s_client["sendBeacon"]:
        broken.append("stock reported no sendBeacon request (the site sends several per page): the control did not run as expected")
    else:
        for kind in KINDS:
            ours_types, stock_types = set(client[kind]), set(s_client[kind])
            if ours_types != stock_types:
                problems.append("%s: ours reported the resource types {%s}, stock reported {%s}" % (
                    kind, ", ".join(sorted(ours_types)), ", ".join(sorted(stock_types))))
            ours_lost, stock_lost = _unreported(client, server, kind), _unreported(s_client, s_server, kind)
            if ours_lost > stock_lost:
                problems.append("%s: the server saw %d request(s) ours never reported, stock left %d unreported" % (
                    kind, ours_lost, stock_lost))
    out += [""]
    if broken:
        out += ["**BROKEN CONTROL** (not a difference of ours)", ""] + ["- " + p for p in broken]
        code = 2
    elif problems:
        out += ["**Verdict: RED**, ours differs from stock", ""] + ["- " + p for p in problems]
        code = 1
    else:
        out.append("**Verdict: green**: ours reports the same resource types as stock for every kind, "
                   "and no more requests the server saw go unreported than with stock.")
        code = 0
    out += ["", "Exact counts are shown, not compared (timing can move one request)."]
    out.append("")
    return "\n".join(out), code


def _trace_absolute(client, server):
    out = ["## Request types of our browser (one walk, trace; stock was not traced)", "",
           "| request | client reported | by resource type | server saw |", "|---|---|---|---|"]
    for kind in KINDS:
        out.append("| %s | %d | %s | %d |" % (kind, sum(client[kind].values()), _types(client[kind]), server[kind]))
    problems = []
    beacons = client["sendBeacon"]
    if not beacons:
        problems.append("the client reported no sendBeacon request (the site sends several per page)")
    wrong = {t: c for t, c in beacons.items() if t != "beacon"}
    if wrong:
        problems.append("sendBeacon reported with a resource type other than `beacon`: %s" % wrong)
    for kind in ("keepalive POST", "keepalive fetch", "parked keepalive"):
        if sum(client[kind].values()) < server[kind]:
            problems.append("%s: the server saw %d, the client reported %d" % (kind, server[kind], sum(client[kind].values())))
    out += [""]
    if problems:
        out += ["**Verdict: RED**", ""] + ["- " + p for p in problems]
    else:
        out.append("**Verdict: green**: every sendBeacon is a `beacon`, and no keepalive request the server saw went unreported.")
    out.append("")
    return "\n".join(out), 1 if problems else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ("journey", "trace"):
        print(__doc__, file=sys.stderr)
        return 64
    try:
        with open(argv[1], encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as exc:
        print("## Fake web, profile P4\n\n**RED: no report (%s). The runner did not finish.**\n" % exc)
        return 4
    text, code = (journey if argv[0] == "journey" else trace)(report)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
