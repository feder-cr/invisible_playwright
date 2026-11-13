"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of the typing hunt.

Usage: python scripts/hunt_typing_summary.py EVIDENCE_DIR OS_NAME
Reads EVIDENCE_DIR/{alone,loaded}_<arm>*.log (HUNT-TYPING lines of scripts/hunt_typing.py) and EVIDENCE_DIR/test_<arm>_*.xml (junit of the
typing e2e test under -n 4) and prints one table per OS: per arm and mode, p50 / max of the slip at the last key, p50 / min of the smallest
keydown gap, p50 / p95 of the dwell error (absolute, and the signed p50) and the planned smallest gap, the last three on both page clocks
(h = performance.now() in the handler, e = event.timeStamp), and the test's pass count per core arm.
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

ARMS = ["pypi", "git", "both", "stock"]
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

    def col(key):
        return [x[key] for x in r if x.get(key) is not None]

    def flat(key):
        out = []
        for x in r:
            out.extend(x.get(key) or [])
        return out

    slip = col("slip_last_ms")
    gap_h, gap_e = col("min_keydown_gap_h_ms"), col("min_keydown_gap_e_ms")
    plan_gap = col("plan_min_keydown_gap_ms")
    err_h, err_e = flat("dwell_err_h_ms"), flat("dwell_err_e_ms")
    abs_h, abs_e = [abs(e) for e in err_h], [abs(e) for e in err_e]
    incomplete = sum(1 for x in r if not x.get("all_keys_seen"))
    f = lambda v: "-" if v is None else str(v)
    return "| %s | %d | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        mode, len(r), incomplete, f(q(slip, .5)), f(max(slip) if slip else None),
        f(q(gap_h, .5)), f(min(gap_h) if gap_h else None), f(q(gap_e, .5)), f(min(gap_e) if gap_e else None),
        f(q(abs_h, .5)), f(q(abs_h, .95)), f(q(err_h, .5)), f(q(abs_e, .5)), f(q(abs_e, .95)), f(q(err_e, .5)),
        f(min(plan_gap) if plan_gap else None))


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
    print("Slip = page (handler clock) minus plan at the last key; gap = smallest gap between two keydowns the page saw; dwell error = "
          "(keyup - keydown) seen by the page minus the planned dwell. h = performance.now() read in the handler, e = event.timeStamp. "
          "Times in ms.")
    for arm in ARMS:
        print()
        print("### arm: %s" % arm)
        print("| mode | runs | runs with missing keys | slip p50 | slip max | min gap p50 (h) | min gap min (h) | min gap p50 (e) "
              "| min gap min (e) | dwell err abs p50 (h) | dwell err abs p95 (h) | dwell err signed p50 (h) | dwell err abs p50 (e) "
              "| dwell err abs p95 (e) | dwell err signed p50 (e) | planned min gap |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for mode in MODES:
            print(row(evidence, mode, arm))
    print()
    print("### test_a_store_that_answers_the_focus_does_not_eat_the_text, x20 under `-n 4 --dist loadfile` with 3 other e2e files")
    for arm in ["pypi", "git", "both"]:
        p, t = tests(evidence, arm)
        print("- core %s: %d passed of %d" % (arm, p, t))


if __name__ == "__main__":
    main()
