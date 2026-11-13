"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of the typing hunt.

Usage: python scripts/hunt_typing_summary.py EVIDENCE_DIR OS_NAME
Reads EVIDENCE_DIR/{alone,loaded}_<arm>*.log (HUNT-TYPING lines of scripts/hunt_typing.py) and EVIDENCE_DIR/test_<arm>_*.xml (junit of the
typing e2e test under -n 4) and prints one table per OS: per arm and mode, p50 / max of the slip at the last key and of the smallest keydown
gap, p50 / p95 of the dwell error (absolute, and the signed p50), the planned smallest gap, and the test's pass count.
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

ARMS = ["pypi", "git", "stock"]
MODES = ["alone", "loaded"]


def q(v, f):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * f))] if v else None


def runs(evidence, mode, arm):
    out = []
    for path in sorted(glob.glob(os.path.join(evidence, "%s_%s*.log" % (mode, arm)))):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("HUNT-TYPING "):
                    try:
                        out.append(json.loads(line[len("HUNT-TYPING "):]))
                    except ValueError:
                        pass
    return out


def row(evidence, mode, arm):
    r = runs(evidence, mode, arm)
    slip = [x["slip_last_ms"] for x in r if x.get("slip_last_ms") is not None]
    gap = [x["min_keydown_gap_ms"] for x in r if x.get("min_keydown_gap_ms") is not None]
    plan_gap = [x["plan_min_keydown_gap_ms"] for x in r if x.get("plan_min_keydown_gap_ms") is not None]
    err = []
    for x in r:
        err.extend(x.get("dwell_err_ms") or [])
    absr = [abs(e) for e in err]
    incomplete = sum(1 for x in r if not x.get("all_keys_seen"))
    f = lambda v: "-" if v is None else str(v)
    return "| %s | %d | %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        mode, len(r), incomplete, f(q(slip, .5)), f(max(slip) if slip else None), f(q(gap, .5)), f(min(gap) if gap else None),
        f(q(absr, .5)), f(q(absr, .95)), f(q(err, .5)), f(min(plan_gap) if plan_gap else None))


def tests(evidence, arm):
    passed = total = 0
    for path in glob.glob(os.path.join(evidence, "test_%s_*.xml" % arm)):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            if "test_a_store_that_answers_the_focus_does_not_eat_the_text" not in (case.get("name") or ""):
                continue
            total += 1
            if not any(c.tag in ("failure", "error", "skipped") for c in case):
                passed += 1
    return passed, total


def main():
    evidence, os_name = sys.argv[1], sys.argv[2]
    print("## typing hunt (%s)" % os_name)
    print("Slip = page minus plan at the last key; gap = smallest gap between two keydowns the page saw; dwell error = (keyup - keydown) "
          "seen by the page minus the planned dwell. Times in ms.")
    for arm in ARMS:
        print()
        print("### arm: %s" % arm)
        print("| mode | runs | runs with missing keys | slip p50 | slip max | min gap p50 | min gap min | dwell err abs p50 | dwell err abs p95 "
              "| dwell err signed p50 | planned min gap |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for mode in MODES:
            print(row(evidence, mode, arm))
    print()
    print("### test_a_store_that_answers_the_focus_does_not_eat_the_text, x20 under `-n 4 --dist loadfile` with 3 other e2e files")
    for arm in ["pypi", "git"]:
        p, t = tests(evidence, arm)
        print("- core %s: %d passed of %d" % (arm, p, t))


if __name__ == "__main__":
    main()
