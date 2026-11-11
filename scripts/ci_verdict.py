#!/usr/bin/env python3
"""CI verdict - the one judge of the mandatory test suites (stdlib only).

`ci/required-suites.json` lists the suites a round must run, per operating system, with the least number of tests each
must collect. A test that is skipped, deselected or never collected is a hole nobody sees, so this script reads the
junit pytest wrote and fails on every one of them.

    ci_verdict.py args  --required ci/required-suites.json --suite wrapper.e2e.udp --os linux [--with-marker]
        prints, one per line, the pytest arguments of the suite (files and node ids); with --with-marker, `-m` and the
        suite's marker come first. An error (exit 2) if the suite does not run on that OS.

    ci_verdict.py judge --required ci/required-suites.json --suite wrapper.e2e.udp --os linux
                        --junit junit.xml --binary PATH [--out result/wrapper.e2e.udp.linux.json]
                        [--main-required PATH]
        reads the junit, writes the result json and exits 1, printing EVERY problem with its reason, one per line, when:
        the junit or the row is missing; a test failed, errored or was skipped; fewer tests were collected than the
        minimum; a required file or node id did not pass; and, with --main-required (the file of main), a suite of
        main is gone, its minimum went down or its list of tests lost an element (the list only grows). A
        --main-required that does not exist is said and passed: main has no list yet.

A required entry of "tests" is a file (at least one test of it must exist and every one must pass) or a node id
`tests/x.py::name` (that test must pass; a parametrized test is matched on the name before `[`, and every one of its
parameters must pass).

Exit 0 on a green verdict, 1 on a red one, 2 on a wrong use of `args`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OSES = ("linux", "windows")


class Usage(Exception):
    pass


# --------------------------------------------------------------------------------------------- the required list
def load_required(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Usage("cannot read the required list %s: %s" % (path, exc))
    if not isinstance(data, list):
        raise Usage("%s must be a JSON list of suites" % path)
    return data


def find_suite(suites, suite_id, os_name):
    if os_name not in OSES:
        raise Usage("--os must be one of %s, not %r" % (", ".join(OSES), os_name))
    rows = [s for s in suites if isinstance(s, dict) and s.get("id") == suite_id]
    if not rows:
        raise Usage("no suite %r in the required list" % suite_id)
    if len(rows) > 1:
        raise Usage("the suite %r appears %d times in the required list" % (suite_id, len(rows)))
    row = rows[0]
    if os_name not in row.get("os", []):
        raise Usage("the suite %r does not run on %s (it runs on: %s)"
                    % (suite_id, os_name, ", ".join(row.get("os", [])) or "nowhere"))
    return row


# ------------------------------------------------------------------------------------------------- the junit
def node_id(classname, name):
    """`tests.test_x` + `test_y[p]` -> `tests/test_x.py::test_y[p]`; a class segment (capitalised) goes after the file."""
    parts = [p for p in (classname or "").split(".") if p]
    module, klass = [], []
    for i, part in enumerate(parts):
        if part[:1].isupper() and module:
            klass = parts[i:]
            break
        module.append(part)
    if not module:
        return name or ""
    return "::".join(["/".join(module) + ".py"] + klass + [name or ""])


def read_junit(path):
    """-> list of (node id, outcome) with outcome in passed|failed|errors|skipped."""
    root = ET.parse(path).getroot()
    out = []
    for case in root.iter("testcase"):
        nid = node_id(case.get("classname"), case.get("name"))
        if case.get("file"):  # xunit1 junit carries the file, which is the truth
            nid = case.get("file").replace("\\", "/") + "::" + (case.get("name") or "")
        tags = {child.tag for child in case}
        if "error" in tags:
            outcome = "errors"
        elif "failure" in tags:
            outcome = "failed"
        elif "skipped" in tags:
            outcome = "skipped"
        else:
            outcome = "passed"
        out.append((nid, outcome))
    return out


def _base(nid):
    """The node id without the parameters of its last segment."""
    return nid.split("[", 1)[0]


def required_status(entry, cases):
    """-> None if the required entry is satisfied, else why not."""
    if "::" in entry:
        mine = [o for n, o in cases if n == entry or _base(n) == entry]
        what = "node id"
    else:
        mine = [o for n, o in cases if n.split("::", 1)[0] == entry]
        what = "file"
    if not mine:
        return "the %s %s did not run (it is not in the junit)" % (what, entry)
    bad = sorted({o for o in mine if o != "passed"})
    if bad:
        return "the %s %s has tests that did not pass (%s)" % (what, entry, ", ".join(bad))
    return None


def sha256_of(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def commit_sha():
    env = os.environ.get("GITHUB_SHA")
    if env:
        return env
    try:
        here = Path(__file__).resolve().parent.parent
        return subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------------------------------------ against main
def against_main(current, main):
    """The list only grows: every suite of main is still here, with no lower minimum and no lost test or OS."""
    problems = []
    by_id = {s.get("id"): s for s in current if isinstance(s, dict)}
    for old in main:
        if not isinstance(old, dict):
            continue
        sid = old.get("id")
        new = by_id.get(sid)
        if new is None:
            problems.append("the suite %s of main is gone from the list" % sid)
            continue
        if int(new.get("min_tests", 0)) < int(old.get("min_tests", 0)):
            problems.append("the minimum of %s went down: %s on main, %s here"
                            % (sid, old.get("min_tests"), new.get("min_tests")))
        lost = [t for t in old.get("tests", []) if t not in new.get("tests", [])]
        if lost:
            problems.append("the suite %s lost %d required test(s) that main has: %s" % (sid, len(lost), ", ".join(lost)))
        lost_os = [o for o in old.get("os", []) if o not in new.get("os", [])]
        if lost_os:
            problems.append("the suite %s no longer runs on %s, where main runs it" % (sid, ", ".join(lost_os)))
    return problems


# ------------------------------------------------------------------------------------------------ the commands
def cmd_args(ns):
    row = find_suite(load_required(ns.required), ns.suite, ns.os)
    lines = []
    if ns.with_marker and row.get("marker"):
        lines += ["-m", row["marker"]]
    lines += list(row.get("tests", []))
    # bytes, so that Windows does not turn the newlines into CRLF (a CR would stay glued to the last argument)
    sys.stdout.buffer.write("".join(line + "\n" for line in lines).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def cmd_judge(ns):
    problems = []
    result = {"suite": ns.suite, "os": ns.os, "commit": commit_sha(), "binary_sha256": sha256_of(ns.binary),
              "collected": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "missing_tests": [],
              "problems": problems}
    try:
        suites = load_required(ns.required)
        row = find_suite(suites, ns.suite, ns.os)
    except Usage as exc:
        problems.append("no result for this row: %s" % exc)
        return _finish(ns, result)

    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    cases = []
    if not os.path.isfile(ns.junit):
        problems.append("the junit %s is missing: the suite %s did not report on %s" % (ns.junit, ns.suite, ns.os))
    else:
        try:
            cases = read_junit(ns.junit)
        except (ET.ParseError, OSError) as exc:
            problems.append("the junit %s cannot be read: %s" % (ns.junit, exc))
    for _n, outcome in cases:
        counts[outcome] += 1
    collected = len(cases)

    if counts["failed"]:
        problems.append("%d test(s) failed" % counts["failed"])
    if counts["errors"]:
        problems.append("%d test(s) ended in error" % counts["errors"])
    if counts["skipped"]:
        problems.append("%d test(s) were skipped: a required suite allows no skip" % counts["skipped"])
    if collected < int(row.get("min_tests", 0)):
        problems.append("collected %d test(s), fewer than the minimum %s" % (collected, row.get("min_tests")))
    missing = []
    for entry in row.get("tests", []):
        why = required_status(entry, cases)
        if why:
            missing.append(entry)
            problems.append(why)

    if ns.main_required:
        if not os.path.isfile(ns.main_required):
            print("the list of main %s does not exist: main has no required list yet, nothing to compare"
                  % ns.main_required)
        else:
            try:
                problems += against_main(suites, load_required(ns.main_required))
            except Usage as exc:
                problems.append(str(exc))

    result.update(counts, collected=collected, missing_tests=missing)
    return _finish(ns, result)


def _finish(ns, result):
    problems = result["problems"]
    if ns.out:
        out = Path(ns.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if problems:
        for p in problems:
            print("VERDICT RED %s/%s: %s" % (ns.suite, ns.os, p))
        return 1
    print("VERDICT GREEN %s/%s: %d collected, %d passed" % (ns.suite, ns.os, result["collected"], result["passed"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("args", help="print the pytest arguments of a suite")
    a.add_argument("--required", required=True)
    a.add_argument("--suite", required=True)
    a.add_argument("--os", required=True)
    a.add_argument("--with-marker", action="store_true", help="print -m and the marker of the suite first")
    j = sub.add_parser("judge", help="judge the junit of a suite")
    j.add_argument("--required", required=True)
    j.add_argument("--suite", required=True)
    j.add_argument("--os", required=True)
    j.add_argument("--junit", required=True)
    j.add_argument("--binary")
    j.add_argument("--out")
    j.add_argument("--main-required")
    ns = ap.parse_args(argv)
    if ns.cmd == "judge":
        return cmd_judge(ns)
    try:
        return cmd_args(ns)
    except Usage as exc:
        print("ci_verdict: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
