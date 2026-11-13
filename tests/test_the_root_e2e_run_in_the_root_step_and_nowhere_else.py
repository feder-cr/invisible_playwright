"""The e2e of the suite `wrapper.e2e.udp.exit` need root, and e2e.yml runs them as root, in their own step, and nowhere else (no browser here).

Run 43262636367 of PR 483: e2e.yml collected every `@pytest.mark.e2e` test as the runner user, and the seven tests whose lab exit is a documentation
address added to `lo` (`ip addr add`) ERRORED: "cannot hold 198.51.100.9 on lo (needs root and iproute2 on Linux)". The gate runs them as root (`sudo -H -E
env -u XDG_RUNTIME_DIR INVPW_REQUIRE_TRAP=1 ...`, the engine copied to /opt/ff). e2e.yml now does the same in a step of its own and leaves them out of the
run of the runner user by name - not by a skip, which would be silent. This test holds the three together, reading the suite's list from
ci/required-suites.json and the workflow as text (PyYAML is not a dependency of this package):

  * the run of the runner user ignores every file and deselects every node of the suite;
  * the root step runs exactly those, under sudo with the gate's environment, from /opt/ff, with no rerun plugin and no `|| true` after the judge;
  * no other step names them.

Known-bad inputs: e2e.yml as it was (no root step, no exclusion) -> `problems` names both; the root step BEFORE the run of the runner user
(a5606dd, run 43271443150: every launch after it waited for the browser until its timeout) -> `problems` names the order.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "e2e.yml"
SUITE = "wrapper.e2e.udp.exit"


def _suite(name: str = SUITE) -> dict:
    return next(s for s in json.loads((ROOT / "ci" / "required-suites.json").read_text(encoding="utf-8")) if s["id"] == name)


def _steps(text: str) -> list:
    """(name, body) of each step, by the dash at the indentation of the first `- name:` (comments and bodies included)."""
    marks = [m for m in re.finditer(r"^( +)- name: (.+)$", text, re.M)]
    return [(m.group(2).strip(), text[m.start():(marks[i + 1].start() if i + 1 < len(marks) else len(text))]) for i, m in enumerate(marks)]


def problems(text: str, suite: dict) -> list:
    out = []
    files = [t for t in suite["tests"] if "::" not in t]
    nodes = [t for t in suite["tests"] if "::" in t]
    steps = _steps(text)
    root = [body for name, body in steps if "root e2e" in name]
    # the Linux run (xvfb): the Windows parts of e2e.yml leave the linux_only suites out by reading ci/required-suites.json
    runner = [body for name, body in steps if "scripts/run_e2e.py" in body and "xvfb-run" in body]
    if len(root) != 1:
        return ["there is no (or more than one) step for the root e2e: %d" % len(root)]
    if len(runner) != 1:
        return ["the run of the runner user is not one step: %d" % len(runner)]
    root, runner = root[0], runner[0]
    bodies = [body for _name, body in steps]
    if bodies.index(root) < bodies.index(runner):
        out.append("the root step comes BEFORE the run of the runner user: what root leaves behind reaches every launch after it")
    if "if: success() || failure()" not in root.split("run: |")[0]:
        out.append("the root step does not run after a red run of the runner user (`if: success() || failure()`)")
    for f in files:
        if f not in re.search(r"ROOT_FILES: (.*)", root).group(1).split() or f not in re.search(r"ROOT_FILES: (.*)", runner).group(1).split():
            out.append("%s is not in ROOT_FILES of both the root step and the run of the runner user" % f)
    for n in nodes:
        if n not in re.search(r"ROOT_NODES: (.*)", root).group(1).split() or n not in re.search(r"ROOT_NODES: (.*)", runner).group(1).split():
            out.append("%s is not in ROOT_NODES of both steps" % n)
    if '--ignore=$f' not in runner or '--deselect=$n' not in runner or '"${IGNORE[@]}" "${DESELECT[@]}"' not in runner:
        out.append("the run of the runner user does not pass the --ignore / --deselect of the root tests to run_e2e.py")
    if 'ROOT_TESTS: "%d"' % suite["min_tests"] not in root:
        out.append("the root step does not demand exactly %d tests" % suite["min_tests"])
    for need in ("sudo -H -E env -u PYTEST_ADDOPTS -u XDG_RUNTIME_DIR INVPW_REQUIRE_TRAP=1", "sudo cp -a", "/opt/ff", "-p no:rerunfailures", "--junitxml"):
        if need not in root:
            out.append("the root step lacks `%s`" % need)
    if not all('counts["%s"]' % k in root for k in ("failures", "errors", "skipped")):
        out.append("the root step does not refuse failures, errors and skips")
    for name, body in steps:
        if body is root or body is runner:
            continue
        for t in files + nodes:
            if t in body.replace("\n", " ") and "ROOT_" not in body:
                out.append("step %r names %s outside the root step" % (name, t))
    return out


def test_the_workflow_runs_the_root_e2e_in_the_root_step_and_leaves_them_out_of_the_other_run():
    assert problems(WORKFLOW.read_text(encoding="utf-8"), _suite()) == []


def test_the_suite_is_the_one_the_gate_runs_as_root_on_linux_only():
    suite = _suite()
    assert suite["os"] == ["linux"] and suite["needs_root"] == ["linux"] and suite["min_tests"] == 7 and suite["exact"] is True


BAD_BEFORE = """
    steps:
      - name: Fetch
        run: echo hi
      - name: Run the full e2e suite under a virtual display
        run: xvfb-run -a python -u scripts/run_e2e.py "$FF"
"""


def test_the_workflow_as_it_was_is_refused():
    found = problems(BAD_BEFORE, _suite())
    assert found and "no (or more than one) step for the root e2e" in found[0]


def test_a_root_step_without_the_exclusion_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8").replace("--ignore=$f", "--ignorex=$f")
    assert any("does not pass the --ignore" in p for p in problems(text, _suite()))


def test_a_root_step_that_may_skip_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8").replace('counts["skipped"]', 'counts["nothing"]')
    assert any("refuse failures" in p for p in problems(text, _suite()))


def test_a_root_step_before_the_run_of_the_runner_user_is_refused():
    """Run 43271443150 of PR 483: with the root step first, every launch of the run that followed waited for the browser until its timeout."""
    text = WORKFLOW.read_text(encoding="utf-8")
    steps = _steps(text)
    root = next(body for name, body in steps if "root e2e" in name)
    runner = next(body for name, body in steps if "scripts/run_e2e.py" in body)
    assert runner + root in text, "the two steps are expected next to each other, the run of the runner user first"
    swapped = text.replace(runner + root, root + runner)
    assert any("BEFORE the run of the runner user" in p for p in problems(swapped, _suite()))


def test_a_root_step_that_does_not_run_after_a_red_run_is_refused():
    root = next(body for name, body in _steps(WORKFLOW.read_text(encoding="utf-8")) if "root e2e" in name)
    text = WORKFLOW.read_text(encoding="utf-8").replace(root, root.replace("if: success() || failure()", "if: success()", 1))
    assert any("success() || failure()" in p for p in problems(text, _suite()))


def test_a_root_test_named_in_another_step_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8") + (
        "      - name: Also\n        run: python -m pytest tests/test_a_proxy_that_cannot_carry_udp_leaks_nothing_direct_e2e.py\n")
    assert any("outside the root step" in p for p in problems(text, _suite()))
