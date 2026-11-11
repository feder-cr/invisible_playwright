"""Leave a record of every test report as it happens (loaded with `-p`).

A CI shard that hits its step limit is killed before pytest can write its JUnit
XML, so a timed-out shard used to leave nothing behind and the next plan had to
guess what the files in it cost. This plugin appends one JSON line per test
report to the file named by $INVPW_RECORD, and closes the file after every
line, so what a killed process has already written is on disk. Without the
variable it does nothing.

Why a hook of our own and not the `pytest-reportlog` plugin: the line is five
fields, the file is read by `upstream_baseline.py --write-weights`, and a hook
here is one less package to pin and install on two operating systems. The
reruns the suite turns on are reported too (outcome "rerun"): their time is
real and is most of the time of this suite.
"""
from __future__ import annotations

import json
import os
import time


def pytest_runtest_logreport(report) -> None:
    path = os.environ.get("INVPW_RECORD")
    if not path:
        return
    line = json.dumps({
        "id": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "duration": round(float(getattr(report, "duration", 0.0) or 0.0), 3),
        "at": round(time.time(), 1),
    })
    # Append and close each time: nothing is left in a buffer for a kill to
    # take. About 1500 lines for a whole run; the cost is not measurable.
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
