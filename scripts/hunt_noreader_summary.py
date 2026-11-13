"""HUNT SCAFFOLDING, NEVER MERGED: the step summary of windows-flakes-noreader-hunt.yml for one OS.

Usage: python scripts/hunt_noreader_summary.py <os label> <evidence dir>

Reads evidence/netidle_<arm>_<round>_<proc>.log (HUNT-ROW json lines of scripts/hunt_netidle.py) and evidence/typing_<arm>_<i>.xml (junit of
the typing test) and prints markdown: per arm the rows that were not on /landed when goto returned, out of the rows made, and the pass counts
of the typing test per variant.
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

ARMS = ("old", "new", "stock")
TEST = "test_a_store_that_answers_the_focus_does_not_eat_the_text"


def netidle(ev):
    print("### network idle rows: misses (not on /landed when goto returned) out of the rows made")
    print()
    print("| arm | misses | rows | goto errors | processes with no row | misses by path |")
    print("|---|---|---|---|---|---|")
    for arm in ARMS:
        logs = sorted(glob.glob(os.path.join(ev, "netidle_%s_*.log" % arm)))
        rows, dead = [], 0
        for log in logs:
            got = []
            with open(log, errors="replace") as f:
                for line in f:
                    if line.startswith("HUNT-ROW "):
                        got.append(json.loads(line[len("HUNT-ROW "):]))
            if not got:
                dead += 1
            rows += got
        miss = [r for r in rows if not r["landed_at_return"]]
        errs = [r for r in rows if r["error"] is not None]
        by = {}
        for r in miss:
            by[r["path"]] = by.get(r["path"], 0) + 1
        print("| %s | %d | %d | %d | %d of %d | %s |" % (arm, len(miss), len(rows), len(errs), dead, len(logs),
                                                        ", ".join("%s %d" % kv for kv in sorted(by.items())) or "-"))
    print()
    print("Expected 400 rows per arm (4 processes x 5 rounds x 20 rows).")


def typing(ev):
    print()
    print("### typing test, 10 runs of the file under -n 4 --dist loadfile with the two load files")
    print()
    print("| arm | variant | passed | failed | skipped | runs that gave no result |")
    print("|---|---|---|---|---|---|")
    notes = []
    for arm in ("old", "new"):
        files = sorted(glob.glob(os.path.join(ev, "typing_%s_*.xml" % arm)))
        count = {}
        seen = 0
        for path in files:
            try:
                root = ET.parse(path).getroot()
            except Exception as exc:
                notes.append("%s: unreadable junit %s (%s)" % (arm, os.path.basename(path), exc))
                continue
            found = False
            for case in root.iter("testcase"):
                if TEST not in case.get("name", ""):
                    continue
                found = True
                variant = re.search(r"\[(.*)\]", case.get("name")).group(1)
                c = count.setdefault(variant, {"pass": 0, "fail": 0, "skip": 0})
                if case.find("failure") is not None or case.find("error") is not None:
                    c["fail"] += 1
                    kid = case.find("failure") if case.find("failure") is not None else case.find("error")
                    notes.append("%s %s (%s): %s" % (arm, variant, os.path.basename(path), (kid.get("message") or "")[:200]))
                elif case.find("skipped") is not None:
                    c["skip"] += 1
                else:
                    c["pass"] += 1
            seen += found
        empty = 10 - seen
        for variant in sorted(count) or ["-"]:
            c = count.get(variant, {"pass": 0, "fail": 0, "skip": 0})
            print("| %s | %s | %d of 10 | %d | %d | %d |" % (arm, variant, c["pass"], c["fail"], c["skip"], empty))
    if notes:
        print()
        print("```")
        print("\n".join(notes))
        print("```")


def load_files(ev):
    print()
    print("### whole pytest runs (typing file plus the two load files), tests failed in the load files")
    for arm in ("old", "new"):
        bad = []
        for path in sorted(glob.glob(os.path.join(ev, "typing_%s_*.xml" % arm))):
            try:
                root = ET.parse(path).getroot()
            except Exception:
                continue
            for case in root.iter("testcase"):
                if TEST in case.get("name", ""):
                    continue
                if case.find("failure") is not None or case.find("error") is not None:
                    bad.append(case.get("classname", "") + "::" + case.get("name", ""))
        print("- %s: %d failed (%s)" % (arm, len(bad), ", ".join(sorted(set(bad))[:6]) or "none"))


if __name__ == "__main__":
    label, evidence = sys.argv[1], sys.argv[2]
    print("## %s" % label)
    print()
    netidle(evidence)
    typing(evidence)
    load_files(evidence)
