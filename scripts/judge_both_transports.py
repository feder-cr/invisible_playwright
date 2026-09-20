"""Run the same e2e through BOTH transports and compare, test by test.

⛔ THIS IS THE JUDGEMENT ITEM 8 WAITS FOR. The Node driver and the Python
server answer the same protocol, so the only honest way to know the second one
is correct is to run the same session through both and compare the results
name by name. Deleting the driver before this has run leaves the new path with
nothing to be measured against - a build that agrees with itself.

⛔ AND A DIFFERENCE IS NOT AUTOMATICALLY OURS. In two days this comparison has
already answered in both directions: `go_back` fails identically on both arms,
so it is the engine ([B185]); an unanswered dialog failed only on ours, so it
was ours. The table below keeps them apart instead of counting failures.

    python scripts/judge_both_transports.py <firefox-binary>
    python scripts/judge_both_transports.py <binary> -k juggler   # a subset

⛔ THE MACHINE MUST BE QUIET. Both arms launch real browsers, and this project
has three recorded cases of measuring under load and reading the noise as a
product defect. The script refuses to start if a firefox is already alive.

⛔ AND THE ARMS RUN INTERLEAVED PER FILE, never all-of-A then all-of-B. Machine
load and disk cache drift over minutes; running one arm to completion first
puts every one of its tests in a different window from the other's, which turns
"this arm is worse" and "that minute was worse" into the same measurement.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The files worth running on both. ⛔ NOT the whole suite: most of it never
#: touches a transport, so running it twice would double the time and add no
#: information. These are the ones that drive a browser through the seam.
FILES = [
    "tests/test_juggler_transport.py",
    "tests/test_hidden_desktop.py",
    "tests/test_new_page_defaults.py",
    "tests/test_cross_origin_iframe.py",
    "tests/test_file_chooser.py",
]

ESITO = re.compile(r"^(tests[^\s:]+)::(\S+)\s+(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)",
                   re.M)


def quiet_machine() -> int:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process firefox -ErrorAction SilentlyContinue | Measure-Object).Count"],
        capture_output=True, text=True)
    try:
        return int((r.stdout or "0").strip() or 0)
    except ValueError:
        return -1


#: A checkout that still carries the Node driver, for the day this one does not.
#:
#: ⛔ THIS GATE EXISTS ONLY WHILE THERE ARE TWO ARMS, and the driver is on its
#: way out of the tree. When it goes, the second arm comes from a git worktree
#: at the last commit that carried it:
#:
#:     git worktree add /tmp/judge <that commit>
#:     INVPW_DRIVER_TREE=/tmp/judge python scripts/judge_both_transports.py <bin>
#:
#: A worktree rather than an installed release, because a published wrapper
#: pins a published core and a sealed engine and therefore cannot drive a
#: locally built binary - which is the only kind worth judging.
DRIVER_TREE_ENV = "INVPW_DRIVER_TREE"


def run_one(transport: str, binary: str, path: str, extra: list) -> dict:
    import os
    env = dict(os.environ)
    env["INVPW_TRANSPORT"] = transport
    env["INVPW_BINARY_PATH"] = binary
    tree = os.environ.get(DRIVER_TREE_ENV)
    if transport == "driver" and tree:
        # ⛔ PREPENDED, so the older tree's package wins the import. The TESTS
        # still come from this checkout: what is being borrowed is the driver,
        # not the suite, and running the old suite would compare two different
        # sets of assertions and call it a transport difference.
        src = str(pathlib.Path(tree) / "src")
        env["PYTHONPATH"] = os.pathsep.join(
            [src] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-o", "addopts=", "-v",
         "-p", "no:cacheprovider"] + extra,
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=3600)
    return {name: verdict
            for _, name, verdict in ESITO.findall(r.stdout + r.stderr)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("binary")
    p.add_argument("-k", dest="selection", default=None)
    p.add_argument("--allow-busy", action="store_true",
                   help="run even with a browser already alive. ⛔ The result "
                        "is then not a measurement, and the report says so.")
    a = p.parse_args()

    alive = quiet_machine()
    if alive and not a.allow_busy:
        print("REFUSING: %d firefox processes are already alive. A run under "
              "load is not a measurement - this project has three recorded "
              "cases of reading that noise as a product defect. Close them, or "
              "pass --allow-busy and read the result as indicative." % alive)
        return 2
    if alive:
        print("WARNING: %d firefox alive, the result is INDICATIVE" % alive)

    extra = ["-k", a.selection] if a.selection else []
    driver, juggler = {}, {}
    for path in FILES:
        if not (ROOT / path).exists():
            print("  (skipped, absent: %s)" % path)
            continue
        print("running %s ..." % path, flush=True)
        # ⛔ Interleaved per file: see the module docstring.
        driver.update(run_one("driver", a.binary, path, extra))
        juggler.update(run_one("juggler", a.binary, path, extra))

    names = sorted(set(driver) | set(juggler))
    ok = both_fail = only_ours = only_theirs = not_run = 0
    rows = []
    for name in names:
        d = driver.get(name, "ABSENT")
        j = juggler.get(name, "ABSENT")
        good = ("PASSED", "XFAIL", "SKIPPED")
        # ⛔ A TEST THE OTHER ARM NEVER RAN IS NOT A TEST THE OTHER ARM
        # FAILED, and folding the two was making this gate lie in the
        # comfortable direction. The driver arm imports an OLD `src/`, so any
        # test file naming something added since that commit fails to COLLECT
        # there and every test in it comes back ABSENT. Counted as "only the
        # driver fails", they printed under "ours is BETTER here" - measured
        # 2026-08-28 at 32 of them, essentially all of that kind. The number
        # grows on its own as this tree moves, which is the property that
        # turns a gate into decoration.
        if "ABSENT" in (d, j) and not (d in good and j in good):
            not_run += 1
            rows.append(("NOT RUN", name, d, j))
        elif d in good and j in good:
            ok += 1
        elif d not in good and j not in good:
            both_fail += 1
            rows.append(("ENGINE?", name, d, j))
        elif d in good:
            only_ours += 1
            rows.append(("OURS", name, d, j))
        else:
            only_theirs += 1
            rows.append(("DRIVER", name, d, j))

    print()
    print("=" * 78)
    print("%-8s %-46s %-10s %s" % ("verdict", "test", "driver", "juggler"))
    print("-" * 78)
    for kind, name, d, j in rows:
        print("%-8s %-46s %-10s %s" % (kind, name[:46], d, j))
    print("-" * 78)
    print("both green                 %d" % ok)
    print("BOTH fail  -> the ENGINE   %d" % both_fail)
    print("only OURS fails            %d   <- the ones that are ours to fix"
          % only_ours)
    print("only the DRIVER fails      %d   <- ours is BETTER here, verify why"
          % only_theirs)
    print("never ran on one arm       %d   <- NOT a comparison: the old arm "
          "could not collect these" % not_run)
    if not_run:
        print()
        print("  WARNING: %d of the rows above were never compared at all. The driver "
              "arm's `src/` is a worktree that ages: a test naming anything "
              "added since it was cut fails to collect there. When this number "
              "approaches the total, this gate has stopped answering its own "
              "question and the worktree needs re-cutting." % not_run)
    print()
    if only_ours:
        print("VERDICT: not ready to delete the driver. %d tests pass through "
              "it and fail through us." % only_ours)
        return 1
    print("VERDICT: no test passes on the driver and fails on us.")
    if both_fail:
        print("  (%d fail on BOTH: engine defects, not a reason to wait)"
              % both_fail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
