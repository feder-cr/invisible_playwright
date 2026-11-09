"""Which canvas text cases two runs draw differently.

tests/test_canvas_text_is_windows_e2e.py writes canvas_text_<tag>.json (case id -> SHA-256
of the RGBA bytes) into INVISIBLE_CANVAS_TEXT_OUT. Run it on Debian 12 and on Ubuntu 24.04
(and the retail reference, tests/data/canvas_text_ref/refs.json, is accepted too), then:

    python scripts/compare_canvas_text_runs.py debian.json ubuntu.json

The exit status is 0 when every case is equal on both sides and both draw the same set.
"""
from __future__ import annotations

import json
import sys


def load(path):
    """case id -> sha256, from a run file or from refs.json."""
    data = json.loads(open(path, encoding="utf-8").read())
    if "cases" in data:
        return {k: v["sha256"] for k, v in data["cases"].items()}
    return data


def compare(a, b):
    """(only in a, only in b, ids whose hash differs)."""
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    differ = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return only_a, only_b, differ


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    a, b = load(argv[1]), load(argv[2])
    only_a, only_b, differ = compare(a, b)
    for label, ids in (("only in %s" % argv[1], only_a), ("only in %s" % argv[2], only_b),
                       ("differ", differ)):
        if ids:
            print("%s (%d):" % (label, len(ids)))
            for k in ids:
                print("  " + k)
    print("%d cases compared, %d differ" % (len(set(a) & set(b)), len(differ)))
    return 1 if (only_a or only_b or differ) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
