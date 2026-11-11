"""No test that passes today may fail tomorrow.

⛔ THIS IS THE NET A REFACTOR NEEDS, and "the suite stays green" is not
available as one: of the 698 sync tests, 163 pass. Most of the red is either a
decision this package took on purpose - `perimeter.py` refuses 77 names - or a
hole nobody has filled yet. A gate that asked for green would be red forever
and would teach people to skip it.

So the question is narrower, and answerable without any green: **did anything
that worked stop working?**

⛔ READ FROM A JUNIT XML, AND THE FIRST VERSION READ THE PROGRESS LINE INSTEAD.
That looked cheap - pytest prints one character per test as it goes, in
collection order, so zipping it against `--collect-only` seemed to give the
same map without re-running anything. It is wrong, and by a margin a spot check
would have missed: on the real report there are **711 characters for 698
collected tests**. A test that fails and then errors in teardown prints TWO.
Pairing them would have attributed every outcome after the thirteenth
double-printing test to the wrong test, and the map would have looked
plausible. The length check refused it, which is the only reason it was caught.

A JUnit XML has one entry per test with its outcome. It costs a flag.

    python scripts/upstream_baseline.py <binary> --junit-xml out.xml
    python scripts/upstream_regressions.py --record out.xml
    python scripts/upstream_regressions.py --check  out.xml
    python scripts/upstream_regressions.py --selftest

A run split over several jobs leaves several reports. Pass them all: they are
merged before the comparison, and the floor refuses a merge that lost a shard.

    python scripts/upstream_regressions.py --check a.xml b.xml c.xml d.xml
    python scripts/upstream_regressions.py --check --min-fraction 0.95 a.xml
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: Where the reference lives. Committed on purpose: a net kept out of the
#: repository is a net that disagrees with the branch it is protecting.
REFERENCE = ROOT / "tests" / "gates" / "upstream_baseline.json"


def outcomes(xml_text: str) -> dict:
    """test id -> "passed" | "failed" | "error" | "skipped".

    ⛔ A `<testcase>` WITH NO CHILD IS THE ONLY PASS. JUnit says what went
    wrong by adding a child element - `failure`, `error`, `skipped` - and says
    a pass by adding nothing. Reading it the other way round, looking for a
    positive marker, silently turns every pass into an unknown.
    """
    root = ET.fromstring(xml_text)
    out = {}
    for case in root.iter("testcase"):
        name = "%s::%s" % (case.get("classname", ""), case.get("name", ""))
        esito = "passed"
        for child in case:
            tag = child.tag
            if tag in ("failure", "error", "skipped"):
                esito = tag if tag != "failure" else "failed"
                break
        # ⛔ A test can be recorded twice - a failure in the call phase and an
        # error in teardown. The WORSE outcome wins, so a teardown error can
        # never mask a passing call phase.
        if out.get(name, "passed") == "passed":
            out[name] = esito
    return out


def merge(reports: list) -> dict:
    """One outcome map from several, the worse outcome winning on a clash.

    Shards split the suite by file, so a test normally appears in exactly one
    report. If one appears in two (a file moved between shards, a report
    uploaded twice), a pass in one must not hide a failure in the other.
    """
    rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    merged: dict = {}
    for one in reports:
        for test, esito in one.items():
            if test not in merged or rank[esito] > rank[merged[test]]:
                merged[test] = esito
    return merged


def floor_of(recorded: int, fraction: float) -> int:
    """How many recorded tests must still be collected for a run to count.

    A fraction of the RECORDED set, not of what this run collected: a shard
    that died before writing its report removes its whole share of the suite,
    and a floor taken from the run itself would shrink with it.
    """
    return math.ceil(recorded * fraction)


def differences(a: dict, b: dict) -> list:
    """(test, outcome in a, outcome in b) for every test that differs."""
    return [(t, a.get(t, "absent"), b.get(t, "absent"))
            for t in sorted(set(a) | set(b)) if a.get(t) != b.get(t)]


def compare(before: dict, after: dict) -> tuple:
    """(regressions, repairs, disappeared). Only the first fails the gate."""
    regressions, repairs, gone = [], [], []
    for test, was in sorted(before.items()):
        now = after.get(test)
        if now is None:
            gone.append(test)
        elif was == "passed" and now != "passed":
            regressions.append((test, was, now))
        elif was != "passed" and now == "passed":
            repairs.append((test, was, now))
    return regressions, repairs, gone


def _xml(*cases: tuple) -> str:
    parts = ['<testsuites><testsuite name="p">']
    for classname, name, child in cases:
        parts.append('<testcase classname="%s" name="%s">' % (classname, name))
        if child:
            parts.append("<%s message='x'/>" % child)
        parts.append("</testcase>")
    parts.append("</testsuite></testsuites>")
    return "".join(parts)


def _selftest() -> int:
    before = outcomes(_xml(("a", "t1", None), ("a", "t2", None),
                           ("b", "t3", "failure")))
    cases = [
        ("identical", _xml(("a", "t1", None), ("a", "t2", None),
                           ("b", "t3", "failure")), 0),
        ("a passing test turns red", _xml(("a", "t1", None), ("a", "t2", "failure"),
                                          ("b", "t3", "failure")), 1),
        ("a passing test turns into an error",
         _xml(("a", "t1", None), ("a", "t2", "error"), ("b", "t3", "failure")), 1),
        ("a red one heals: NOT a regression",
         _xml(("a", "t1", None), ("a", "t2", None), ("b", "t3", None)), 0),
        ("a passing test turns into a skip: IS a regression",
         _xml(("a", "t1", None), ("a", "t2", "skipped"), ("b", "t3", "failure")), 1),
    ]
    broken = 0
    for label, xml, expected in cases:
        found, _, _ = compare(before, outcomes(xml))
        if len(found) != expected:
            broken += 1
        print("  %-50s %s (%d regressions, expected %d)"
              % (label, "ok" if len(found) == expected else "BROKEN",
                 len(found), expected))

    # ⛔ the case the progress-line version got wrong: one test recorded twice,
    # a failing call phase and an erroring teardown. It must not read as a pass.
    twice = _xml(("a", "t1", "failure"), ("a", "t1", "error"))
    read_back = outcomes(twice)
    ok = read_back.get("a::t1") == "failed"
    if not ok:
        broken += 1
    print("  %-50s %s (read %r)"
          % ("a test recorded twice is not a pass", "ok" if ok else "BROKEN",
             read_back.get("a::t1")))

    # and a test the reference knows that is no longer collected must not be
    # silently counted as fine.
    _, _, gone = compare(before, outcomes(_xml(("a", "t1", None))))
    ok2 = len(gone) == 2
    if not ok2:
        broken += 1
    print("  %-50s %s (%d)" % ("tests that vanished are reported",
                               "ok" if ok2 else "BROKEN", len(gone)))

    # several reports: disjoint ones are the union, and on a clash the worse
    # outcome wins, so a green copy cannot cover a red one.
    one = outcomes(_xml(("a", "t1", None)))
    two = outcomes(_xml(("a", "t2", "failure")))
    both = merge([one, two])
    ok3 = both == {"a::t1": "passed", "a::t2": "failed"}
    clash = merge([outcomes(_xml(("a", "t1", None))),
                   outcomes(_xml(("a", "t1", "error")))])
    ok3 = ok3 and clash == {"a::t1": "error"}
    if not ok3:
        broken += 1
    print("  %-50s %s" % ("merged reports keep the worse outcome",
                          "ok" if ok3 else "BROKEN"))

    # the floor is a share of the RECORDED tests, rounded up.
    ok4 = floor_of(698, 0.95) == 664 and floor_of(10, 0.5) == 5
    if not ok4:
        broken += 1
    print("  %-50s %s" % ("the floor is a share of the recorded set",
                          "ok" if ok4 else "BROKEN"))
    print("selftest: %d cases, %d broken" % (len(cases) + 4, broken))
    return 1 if broken else 0


def _load(paths: list) -> dict:
    """The merged outcomes of every report, or an exit-2 message.

    A report that is missing, empty or not XML is a shard that did not
    deliver. Reading it as "no tests" would let a dead shard pass for a clean
    one, so it stops the run instead.
    """
    reports = []
    for path in paths:
        f = pathlib.Path(path)
        if not f.is_file():
            print("MISSING REPORT: %s" % path)
            raise SystemExit(2)
        try:
            reports.append(outcomes(f.read_text(encoding="utf-8",
                                                errors="replace")))
        except ET.ParseError as e:
            print("UNREADABLE REPORT: %s (%s)" % (path, e))
            raise SystemExit(2)
    return merge(reports)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("report", nargs="*",
                   help="one or more JUnit XMLs from the baseline, merged")
    p.add_argument("--record", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--min-fraction", type=float, default=None, metavar="F",
                   help="fail unless at least this share of the recorded "
                        "tests was collected: a shard that vanished takes its "
                        "tests with it, and what is not collected cannot "
                        "regress")
    p.add_argument("--save-merged", default=None, metavar="JSON",
                   help="write the merged outcomes of the reports to JSON")
    p.add_argument("--compare-to", default=None, metavar="JSON",
                   help="list the tests whose outcome differs from a merged "
                        "JSON saved by another run (informational: it never "
                        "changes the exit code)")
    p.add_argument("--diff-out", default=None, metavar="TXT",
                   help="write that list to a file instead of printing it")
    a = p.parse_args()
    if a.selftest:
        return _selftest()
    if not a.report:
        p.error("a JUnit XML is needed")

    now = _load(a.report)
    if a.save_merged:
        pathlib.Path(a.save_merged).write_bytes(
            json.dumps(now, indent=1, sort_keys=True).encode("utf-8"))
    if a.record:
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_bytes(json.dumps(now, indent=1, sort_keys=True)
                              .encode("utf-8"))
        counts: dict = {}
        for v in now.values():
            counts[v] = counts.get(v, 0) + 1
        print("recorded %d tests in %s" % (len(now), REFERENCE.name))
        print("  " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
        return 0

    if not REFERENCE.exists():
        print("no reference recorded yet: run with --record first")
        return 2
    before = json.loads(REFERENCE.read_text(encoding="utf-8"))
    regressions, repairs, gone = compare(before, now)

    for test, was, is_now in regressions:
        print("REGRESSION  %s: %s -> %s" % (test, was, is_now))
    if repairs:
        print("(%d test(s) got BETTER, which this gate does not refuse)"
              % len(repairs))
    if gone:
        print("(%d recorded test(s) were not collected this time)" % len(gone))
    if a.compare_to:
        other = json.loads(pathlib.Path(a.compare_to).read_text(
            encoding="utf-8"))
        diff = differences(other, now)
        lines = ["DIFFERENT FROM THE OTHER PLATFORM: %d test(s)" % len(diff)]
        lines += ["  %s: %s -> %s" % d for d in diff]
        if a.diff_out:
            pathlib.Path(a.diff_out).write_bytes(
                ("\n".join(lines) + "\n").encode("utf-8"))
            print(lines[0] + " (listed in %s)" % a.diff_out)
        else:
            print("\n".join(lines))
    short = False
    if a.min_fraction is not None:
        need = floor_of(len(before), a.min_fraction)
        have = len(before) - len(gone)
        if have < need:
            short = True
            print("TOO FEW TESTS COLLECTED: %d of the %d recorded, the floor "
                  "is %d (%.0f%%). A shard probably did not run."
                  % (have, len(before), need, a.min_fraction * 100))
    if regressions:
        print()
        print("UPSTREAM REGRESSIONS: %d test(s) that passed no longer do."
              % len(regressions))
        return 1
    if short:
        return 1
    print("UPSTREAM REGRESSIONS: none. %d recorded, %d of them passing."
          % (len(before), sum(1 for v in before.values() if v == "passed")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
