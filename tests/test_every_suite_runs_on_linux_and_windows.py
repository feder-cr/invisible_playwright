"""Every test of the product runs on Linux AND on Windows (no browser here).

Federico, 12/11: a test seen only on Linux proves nothing about the system the product imitates. On that day 562 of the 808
e2e of this package had never run on Windows in any round (the e2e of a pull request ran on Linux only, and the candidate's
gate carried 246 of them), and two suites of the gate (`wrapper.e2e.udp.exit`, `wrapper.e2e.udp.sandbox`) were Linux only.
Where Windows behaves differently the test adapts to the system; it does not stay on one.

What this holds, reading the files as text (PyYAML is not a dependency of this package):

  * every row of ci/required-suites.json names both systems, or names its Windows twin (`windows_twin`: the id of a row
    that runs on Windows), or says why it can only be Linux (`linux_only`: a test of Linux converging on Windows, which has
    no Windows counterpart because Windows IS the reference);
  * the jobs of e2e.yml that run scripts/run_e2e.py run on a Linux and on a Windows runner between them;
  * the unit job of tests.yml runs on both.

Known-bad inputs: the files as they were on 12/11 (two Linux-only rows with no twin; e2e.yml on ubuntu-24.04 only) ->
`problems` names each of them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SUITES = ROOT / "ci" / "required-suites.json"
WORKFLOWS = ROOT / ".github" / "workflows"


def suite_problems(rows: list) -> list:
    out = []
    windows_ids = {r["id"] for r in rows if "windows" in r.get("os", [])}
    for r in rows:
        systems = set(r.get("os", []))
        if {"linux", "windows"} <= systems:
            continue
        twin = r.get("windows_twin")
        reason = (r.get("linux_only") or "").strip()
        if "windows" not in systems and twin:
            if twin not in windows_ids:
                out.append("%s: its windows_twin %r is not a row that runs on Windows" % (r["id"], twin))
            continue
        if "windows" not in systems and reason:
            continue
        missing = sorted({"linux", "windows"} - systems)
        out.append("%s runs on %s only: it needs %s, a windows_twin, or a linux_only reason"
                   % (r["id"], "/".join(sorted(systems)) or "nothing", " and ".join(missing)))
    return out


def _jobs(text: str) -> list:
    """(name, body) of each job under `jobs:` (two-space indentation, as every workflow here is written)."""
    body = text.split("\njobs:\n", 1)[1] if "\njobs:\n" in text else ""
    marks = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
    return [(m.group(1), body[m.start():(marks[i + 1].start() if i + 1 < len(marks) else len(body))]) for i, m in enumerate(marks)]


def _runs_on_both(job: str) -> bool:
    runners = re.findall(r"\b(ubuntu-[0-9a-z.]+|windows-[0-9a-z.]+)\b", job)
    return any(r.startswith("ubuntu") for r in runners) and any(r.startswith("windows") for r in runners)


def workflow_problems(e2e_text: str, tests_text: str) -> list:
    out = []
    e2e = [(n, b) for n, b in _jobs(e2e_text) if "scripts/run_e2e.py" in b]
    if not e2e:
        out.append("e2e.yml has no job that runs scripts/run_e2e.py")
    elif not _runs_on_both(" ".join(b for _n, b in e2e)):
        out.append("the jobs of e2e.yml that run scripts/run_e2e.py (%s) do not run on both a Linux and a Windows runner"
                   % ", ".join(n for n, _b in e2e))
    unit = [(n, b) for n, b in _jobs(tests_text) if re.search(r"python -m pytest|pytest ", b) and "matrix" in b]
    if not unit:
        out.append("tests.yml has no unit job with a matrix")
    for name, body in unit:
        if not _runs_on_both(body):
            out.append("tests.yml job %r does not run on both a Linux and a Windows runner" % name)
    return out


def test_every_suite_of_the_gate_runs_on_both_systems_or_names_its_twin():
    assert suite_problems(json.loads(SUITES.read_text(encoding="utf-8"))) == []


def test_the_e2e_and_the_unit_tests_of_a_pull_request_run_on_both_systems():
    assert workflow_problems((WORKFLOWS / "e2e.yml").read_text(encoding="utf-8"),
                             (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")) == []


# --------------------------------------------------------------------------------------------- the checkers can fail
ROWS_OF_12_11 = [
    {"id": "wrapper.e2e.udp", "os": ["linux", "windows"]},
    {"id": "wrapper.e2e.udp.exit", "os": ["linux"]},
    {"id": "wrapper.e2e.udp.sandbox", "os": ["linux"]},
]

E2E_OF_12_11 = """
jobs:
  core-on-index:
    uses: ./.github/workflows/core-on-index.yml
  e2e:
    name: e2e (linux, xvfb)
    runs-on: ubuntu-24.04
    steps:
      - run: xvfb-run -a python -u scripts/run_e2e.py "$FF"
"""

TESTS_OF_12_11 = """
jobs:
  unit:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        include:
          - { os: ubuntu-latest, python: '3.11' }
          - { os: windows-latest, python: '3.12' }
    steps:
      - run: python -m pytest -m unit
"""


def test_the_rows_of_12_11_are_refused():
    found = suite_problems(ROWS_OF_12_11)
    assert len(found) == 2 and all("linux only" in p for p in found), found


def test_a_twin_that_does_not_run_on_windows_is_refused():
    rows = [{"id": "a", "os": ["linux"], "windows_twin": "b"}, {"id": "b", "os": ["linux"]}]
    assert any("windows_twin 'b' is not a row that runs on Windows" in p for p in suite_problems(rows))


def test_a_twin_or_a_reason_is_accepted():
    rows = [{"id": "a", "os": ["linux"], "windows_twin": "b"}, {"id": "b", "os": ["windows"], "windows_twin": None},
            {"id": "c", "os": ["linux"], "linux_only": "Linux converging on Windows: Windows is the reference"}]
    assert [p for p in suite_problems(rows) if not p.startswith("b ")] == []


def test_the_e2e_of_12_11_is_refused_and_the_unit_job_passes():
    found = workflow_problems(E2E_OF_12_11, TESTS_OF_12_11)
    assert found == ["the jobs of e2e.yml that run scripts/run_e2e.py (e2e) do not run on both a Linux and a Windows runner"], found
