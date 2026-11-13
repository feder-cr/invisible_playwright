"""The e2e that needs the browser's sandbox runs in a step that turns user namespaces on, and is left out of the normal run.

``tests/test_the_socket_process_is_sandboxed_e2e.py`` (suite ``wrapper.e2e.udp.sandbox``) FAILS by design when the socket and a tab process have no
seccomp filter. On the Ubuntu 24.04 runner AppArmor refuses user namespaces to a normal user (``kernel.apparmor_restrict_unprivileged_userns=1``), so
collected in the normal run of e2e.yml it is red on a runner without the sysctl, and no test of the repository can make it green there. The engine's
gate runs it in a leg that turns the restriction off first; e2e.yml now does the same, the way it already handles the root files: the normal run
ignores the file by name (``SANDBOX_FILES``) and a step of its own, after the run, runs exactly that file as the runner user after
``sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0``, reads its own junit and refuses anything but exactly that many tests, all passed.

This test reads the workflow as text (PyYAML is not a dependency of this package). Known-bad input: e2e.yml as it was, with no sandbox step and no
exclusion -> ``problems`` names both.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from test_the_root_e2e_run_in_the_root_step_and_nowhere_else import _steps

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "e2e.yml"
FILE = "tests/test_the_socket_process_is_sandboxed_e2e.py"
SYSCTL = "kernel.apparmor_restrict_unprivileged_userns=0"


def _env(body: str, key: str) -> list:
    found = re.search(r"^\s*%s: \"?([^\"\n]*)\"?\s*$" % key, body, re.M)
    return found.group(1).split() if found else []


def problems(text: str) -> list:
    out = []
    steps = _steps(text)
    sandbox = [body for name, body in steps if "sandbox e2e" in name]
    runner = [body for name, body in steps if "scripts/run_e2e.py" in body]
    if len(sandbox) != 1:
        return ["there is no (or more than one) step for the sandbox e2e: %d" % len(sandbox)]
    if len(runner) != 1:
        return ["the normal run is not one step: %d" % len(runner)]
    sandbox, runner = sandbox[0], runner[0]
    bodies = [body for _name, body in steps]
    head, _, script = sandbox.partition("run: |")
    if bodies.index(sandbox) < bodies.index(runner):
        out.append("the sandbox step comes BEFORE the normal run")
    if "if: success() || failure()" not in head:
        out.append("the sandbox step does not run after a red normal run (`if: success() || failure()`)")
    if FILE not in _env(runner, "SANDBOX_FILES") or "$SANDBOX_FILES" not in runner or "--ignore=$f" not in runner:
        out.append("the normal run does not ignore %s (SANDBOX_FILES -> --ignore)" % FILE)
    if FILE not in _env(sandbox, "SANDBOX_FILES"):
        out.append("the sandbox step does not run %s (SANDBOX_FILES)" % FILE)
    expected = len(re.findall(r"^def test_", (ROOT / FILE).read_text(encoding="utf-8"), re.M))
    if _env(sandbox, "SANDBOX_TESTS") != [str(expected)]:
        out.append("the sandbox step does not demand exactly %d test(s)" % expected)
    if SYSCTL not in script or script.find(SYSCTL) > script.find("pytest"):
        out.append("the sandbox step does not turn user namespaces on (%s) before pytest" % SYSCTL)
    if "sudo -H" in script or "sudo python" in script or "sudo -E env" in script:
        out.append("the sandbox step runs the test as root: the sandbox leg is the runner user")
    if "-p no:rerunfailures" not in script or "--junitxml" not in script:
        out.append("the sandbox step has no junit or allows a rerun")
    if not all('counts["%s"]' % k in script for k in ("failures", "errors", "skipped")):
        out.append("the sandbox step does not refuse failures, errors and skips")
    for name, body in steps:
        if body is sandbox or body is runner:
            continue
        # The comment block above a step belongs, by the slicing of `_steps`, to the step before it: only the lines that are not comments count.
        if FILE in " ".join(l for l in body.splitlines() if not l.lstrip().startswith("#")):
            out.append("step %r names %s outside the sandbox step" % (name, FILE))
    return out


def test_the_workflow_runs_the_sandbox_e2e_in_its_own_step_and_leaves_it_out_of_the_normal_run():
    assert problems(WORKFLOW.read_text(encoding="utf-8")) == []


def test_the_file_is_one_of_the_tests_of_the_sandbox_suite_of_the_gate():
    suite = next(s for s in json.loads((ROOT / "ci" / "required-suites.json").read_text(encoding="utf-8")) if s["id"] == "wrapper.e2e.udp.sandbox")
    assert FILE in suite["tests"] and suite["os"] == ["linux"] and suite["needs_root"] is False


BAD_BEFORE = """
    steps:
      - name: Run the full e2e suite under a virtual display, without the root ones
        run: xvfb-run -a python -u scripts/run_e2e.py "$FF"
"""


def test_the_workflow_as_it_was_is_refused():
    found = problems(BAD_BEFORE)
    assert found and "no (or more than one) step for the sandbox e2e" in found[0]


def test_the_current_workflow_without_the_exclusion_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8").replace("for f in $ROOT_FILES $SANDBOX_FILES;", "for f in $ROOT_FILES;")
    assert any("does not ignore" in p for p in problems(text))


def test_a_sandbox_step_without_the_sysctl_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8").replace(SYSCTL, "kernel.apparmor_restrict_unprivileged_userns=1")
    assert any("does not turn user namespaces on" in p for p in problems(text))


def test_a_sandbox_step_that_may_skip_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8")
    sandbox = next(body for name, body in _steps(text) if "sandbox e2e" in name)
    assert any("refuse failures" in p for p in problems(text.replace(sandbox, sandbox.replace('counts["skipped"]', 'counts["nothing"]'))))


def test_the_file_named_in_another_step_is_refused():
    text = WORKFLOW.read_text(encoding="utf-8") + "      - name: Also\n        run: python -m pytest %s\n" % FILE
    assert any("outside the sandbox step" in p for p in problems(text))
