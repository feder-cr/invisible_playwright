"""The wait of the install job for the index, and the upgrade test's two versions (no network, no browser).

Ledger #476 of 0.50.1: the Linux job of `user-install` failed in ``test_upgrade_e2e.py`` with "the upgrade did not
move the wrapper" ('0.49.1' != '0.49.1') three minutes after the upload: the step that waited for the index had asked the
JSON endpoint of the version, which answered, while the ``pip install --upgrade`` that followed was served an older page.
The wait now asks pip itself and ends in an error that says what is missing; the upgrade installs the version the same
listing named. The installers are two - the upgrade test installs with pip, the release test with uv (the README's way in) - so
each round asks both, and the job goes on after three rounds in a row in which both said yes (one yes proves that one request saw the
version, not that the next is served by the same node). The script of the step is run here as it stands in the workflow, with a
``pip`` and a ``uv`` that answer as a lagging index does.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "user-install.yml"


def _wait_script() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: wait for the index to serve this version")
    block = text[start:]
    m = re.search(r"python - <<'PY'\n(.*?)\n\s*PY\n", block, re.S)
    assert m, "the wait step has no python script"
    return textwrap.dedent(m.group(1)) + "\n"


def _tool(tmp_path, name, plan):
    """A regular package `name` on the shim path whose `__main__` answers call number i as plan[i] says (the last letter repeats):
    y = resolves it, n = no such version, h = does not answer (sleeps 30 s). It counts its calls in `<name>-calls`."""
    shim = tmp_path / "shim" / name
    shim.mkdir(parents=True)
    (shim / "__init__.py").write_text("", encoding="utf-8")   # a REGULAR package: a namespace one loses to the real one of the environment
    (shim / "__main__.py").write_text(textwrap.dedent("""
        import pathlib, sys, time
        p = pathlib.Path(%r)
        n = int(p.read_text()) if p.exists() else 0
        p.write_text(str(n + 1))
        sys.stdin.read() if %r == "uv" else None
        plan = %r
        what = plan[min(n, len(plan) - 1)]
        if what == "h":
            time.sleep(30)
        if what == "y":
            sys.exit(0)
        sys.stderr.write("ERROR: No matching distribution found for invisible-playwright==9.9.9" + chr(10))
        sys.exit(1)
    """) % (str(tmp_path / (name + "-calls")), name, plan), encoding="utf-8")


def _run_the_wait(tmp_path, pip="y", uv="y", *, seconds=3.0, poll=0.05, call_seconds=None):
    """Run the script of the step in a project named ``invisible-playwright`` 9.9.9, with a ``pip`` and a ``uv`` that answer as their plans say.
    Returns (completed process, pip calls, uv calls)."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "invisible-playwright"\nversion = "9.9.9"\n', encoding="utf-8")
    _tool(tmp_path, "pip", pip)
    _tool(tmp_path, "uv", uv)
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "shim"), INDEX_WAIT_SECONDS=str(seconds), INDEX_WAIT_POLL=str(poll))
    if call_seconds is not None:
        env["INDEX_WAIT_CALL_SECONDS"] = str(call_seconds)
    try:
        r = subprocess.run([sys.executable, "-c", _wait_script()], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("the wait did not end within 60 s with INDEX_WAIT_SECONDS=%s: it does not read its deadline" % seconds)

    def calls(name):
        f = tmp_path / (name + "-calls")
        return int(f.read_text()) if f.exists() else 0
    return r, calls("pip"), calls("uv")


def test_the_wait_asks_both_installers_three_rounds_in_a_row(tmp_path):
    r, pip_calls, uv_calls = _run_the_wait(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (pip_calls, uv_calls) == (3, 3), "one yes is not enough: pip %d, uv %d" % (pip_calls, uv_calls)
    assert "pip and uv resolve invisible-playwright 9.9.9 from the index, 3 rounds in a row" in r.stdout


def test_uv_alone_saying_no_keeps_the_wait_going(tmp_path):
    """The release test installs with uv: pip seeing the version is not enough."""
    r, pip_calls, uv_calls = _run_the_wait(tmp_path, pip="y", uv="nyyy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert uv_calls == 4 and pip_calls == 4, "the round with uv's no counted: pip %d, uv %d" % (pip_calls, uv_calls)


def test_a_no_between_the_yes_starts_the_count_again(tmp_path):
    r, pip_calls, _ = _run_the_wait(tmp_path, pip="yynyyy")
    assert r.returncode == 0 and pip_calls == 6, r.stdout + r.stderr


def test_the_wait_ends_in_an_error_that_names_the_missing_version_when_the_index_never_has_it(tmp_path):
    r, pip_calls, _ = _run_the_wait(tmp_path, pip="n", uv="n", seconds=1.0)
    assert r.returncode == 1, "a forgotten publish must fail the job here: " + r.stdout + r.stderr
    assert "::error::the index does not have invisible-playwright 9.9.9 yet" in r.stdout
    assert "minutes" in r.stdout and "pip said: ERROR: No matching distribution" in r.stdout and "0 of 3 rounds" in r.stdout
    assert pip_calls >= 2


def test_pip_yes_and_uv_never_is_an_error_naming_uv(tmp_path):
    r, _, uv_calls = _run_the_wait(tmp_path, pip="y", uv="n", seconds=1.0)
    assert r.returncode == 1 and "uv said:" in r.stdout, r.stdout + r.stderr
    assert uv_calls >= 2


def test_a_call_that_does_not_answer_is_a_no_and_does_not_stop_the_wait(tmp_path):
    r, pip_calls, _ = _run_the_wait(tmp_path, pip="hyyy", seconds=20.0, call_seconds=0.5)
    assert r.returncode == 0 and pip_calls == 4, r.stdout + r.stderr


def test_a_call_that_never_answers_ends_in_the_error_with_what_happened(tmp_path):
    r, _, _ = _run_the_wait(tmp_path, pip="h", seconds=1.5, call_seconds=0.3)
    assert r.returncode == 1 and "pip did not answer within" in r.stdout, r.stdout + r.stderr


def test_the_harness_has_uv_for_the_wait_to_ask():
    """Read as text: PyYAML is not a dependency of this package (nor of [dev]), as tests/test_ci_runs_once_per_commit.py says."""
    found = re.search(r"^[ \t]*-[ \t]*name:[ \t]*Test harness only[ \t]*\n[ \t]*run:[ \t]*(?P<run>.+)$", WORKFLOW.read_text(encoding="utf-8"), re.M)
    assert found, "the harness step is gone"
    assert "pip" in found.group("run") and found.group("run").split()[-1] == "uv", found.group("run")


def test_the_wait_step_still_passes_the_gate_of_the_core():
    from invisible_core import install_job
    assert install_job.problems(WORKFLOW.read_text(encoding="utf-8")) == []


def test_the_upgrade_goes_from_the_second_newest_to_the_newest_of_one_listing():
    import test_upgrade_e2e as upgrade
    assert upgrade._previous_and_latest(["0.48.0", "0.49.1", "0.50.1"]) == ("0.49.1", "0.50.1")
