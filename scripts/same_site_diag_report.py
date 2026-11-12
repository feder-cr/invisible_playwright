"""Hunt scaffolding (never merged): reads the records of tests/test_fission_strategy1_bench.py::test_diag_same_site.

    python scripts/same_site_diag_report.py DIR

Prints, in markdown, how many runs changed the set of the processes of the sites, and for each one that did: the process that
appeared, its parent (the browser of the test or another one), when it was born against the instants of the test, whether it
was still there ten seconds later, and what the engine logged about starting it.
"""
import collections
import glob
import json
import os
import re
import sys

ABOUT_PROCESSES = re.compile(r"webIsolated|Launch|launch|GetNewOrUsed|remoteType|Isolat|Prealloc|process switch|ProcessSwitch")


def main(argv):
    folder = argv[1]
    recs = []
    for path in sorted(glob.glob(os.path.join(folder, "rec_*.json"))):
        with open(path, encoding="utf-8") as fh:
            recs.append(json.load(fh))
    by_arm = collections.Counter((r["arm"], bool(r.get("changed"))) for r in recs)
    print("## Same-site control, %s: %d runs\n" % (os.path.basename(os.path.normpath(folder)), len(recs)))
    print("| arm | runs | changed |\n|---|---|---|")
    for arm in sorted({r["arm"] for r in recs}):
        print("| %s | %d | %d |" % (arm, by_arm[(arm, True)] + by_arm[(arm, False)], by_arm[(arm, True)]))
    print()
    for r in recs:
        if not r.get("changed"):
            continue
        t = r["t"]
        rows = r["tables"].get("after", [])
        after = {row["pid"]: row for row in rows}
        parents = [row for row in rows if row["kind"] == "parent"]
        print("### %s: %s -> %s\n" % (r["tag"], r["before"], r["after"]))
        print("- browsers below the worker at the second read: %s"
              % [(p["pid"], "born %+.1f s" % (p["born"] - t["start"])) for p in parents])
        own = {after[p]["ppid"] for p in r["before"] if p in after}
        for pid in sorted(set(r["after"]) - set(r["before"])):
            row = after.get(pid)
            if not row:
                print("- pid %d: not in the table" % pid)
                continue
            whose = "the browser of the page's process" if row["ppid"] in own else "ANOTHER browser"
            print("- NEW pid %d: parent %d (%s), name %r, born at start%+.2f s; goto done %+.2f, first read %+.2f, "
                  "frame added %+.2f, leaf reported %+.2f, second read %+.2f; threads %s, rss %s MB; still there 10 s later: %s"
                  % (pid, row["ppid"], whose, row["name"], row["born"] - t["start"], t["goto_done"] - t["start"],
                     t["before_read"] - t["start"], t["frame_added"] - t["start"], t["leaf_reported"] - t["start"],
                     t["after_read"] - t["start"], row["threads"], row["rss_mb"], pid in r.get("later", [])))
            print("  - args: %s" % " ".join(row["args"]))
        for pid in sorted(set(r["before"]) - set(r["after"])):
            print("- GONE pid %d" % pid)
        print("- frames after: %s" % r.get("frames"))
        print("- content processes at the second read: %s"
              % [(x["pid"], x["name"], "born %+.2f" % (x["born"] - t["start"])) for x in rows if x["kind"] != "parent"])
        logs = sorted(glob.glob(os.path.join(folder, "moz_%s*" % r["tag"])))
        print("- engine log files: %s" % [os.path.basename(p) for p in logs])
        for path in logs:
            if ".child-" in os.path.basename(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                keep = [line.rstrip() for line in fh if ABOUT_PROCESSES.search(line)]
            print("\n<details><summary>%s: %d lines about processes (last 120)</summary>\n\n```"
                  % (os.path.basename(path), len(keep)))
            for line in keep[-120:]:
                print(line[:400])
            print("```\n</details>\n")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
