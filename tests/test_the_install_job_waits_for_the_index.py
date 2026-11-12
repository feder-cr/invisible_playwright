"""The wait of the install job for the index, and the upgrade test's two versions (no network, no browser).

Ledger #476 of 0.50.1: the Linux job of `user-install` failed in ``test_upgrade_e2e.py`` with "the upgrade did not
move the wrapper" ('0.49.1' != '0.49.1') three minutes after the upload: the step that waited for the index had asked the
JSON endpoint of the version, which answered, while the ``pip install --upgrade`` that followed was served an older page.
The wait now asks pip itself and ends in an error that says what is missing; the upgrade installs the version the same
listing named. The script of the step is run here as it stands in the workflow, with a ``pip`` that answers as a lagging
index does.
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


def _run_the_wait(tmp_path, *, succeeds_on_call, seconds=2.0, poll=0.05):
    """Run the script of the step in a project named ``invisible-playwright`` 9.9.9, with a ``pip`` that fails until its
    ``succeeds_on_call``-th call (0: never)."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "invisible-playwright"\nversion = "9.9.9"\n', encoding="utf-8")
    shim = tmp_path / "shim" / "pip"
    shim.mkdir(parents=True)
    (shim / "__init__.py").write_text("", encoding="utf-8")   # a REGULAR package: a namespace one loses to the pip of the environment
    counter = tmp_path / "calls"
    (shim / "__main__.py").write_text(textwrap.dedent("""
        import pathlib, sys
        p = pathlib.Path(%r)
        n = (int(p.read_text()) if p.exists() else 0) + 1
        p.write_text(str(n))
        if %d and n >= %d:
            sys.exit(0)
        sys.stderr.write("ERROR: No matching distribution found for invisible-playwright==9.9.9\\n")
        sys.exit(1)
    """) % (str(counter), succeeds_on_call, succeeds_on_call), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "shim"), INDEX_WAIT_SECONDS=str(seconds), INDEX_WAIT_POLL=str(poll))
    try:
        r = subprocess.run([sys.executable, "-c", _wait_script()], cwd=tmp_path, env=env, capture_output=True, text=True,
                           timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail("the wait did not end within 30 s with INDEX_WAIT_SECONDS=%s: it does not read its deadline" % seconds)
    calls = int(counter.read_text()) if counter.exists() else 0
    return r, calls


def test_the_wait_asks_pip_and_goes_on_when_pip_gets_the_version(tmp_path):
    r, calls = _run_the_wait(tmp_path, succeeds_on_call=3)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == 3, "it polled %d times, pip answered on the 3rd" % calls
    assert "pip downloads invisible-playwright 9.9.9" in r.stdout


def test_the_wait_ends_in_an_error_that_names_the_missing_version_when_the_index_never_has_it(tmp_path):
    r, calls = _run_the_wait(tmp_path, succeeds_on_call=0, seconds=1.0)
    assert r.returncode == 1, "a forgotten publish must fail the job here: " + r.stdout + r.stderr
    assert "::error::the index does not have invisible-playwright 9.9.9 yet" in r.stdout
    assert "minutes" in r.stdout and "No matching distribution" in r.stdout
    assert calls >= 2


def test_the_wait_step_still_passes_the_gate_of_the_core():
    from invisible_core import install_job
    assert install_job.problems(WORKFLOW.read_text(encoding="utf-8")) == []


def test_the_upgrade_goes_from_the_second_newest_to_the_newest_of_one_listing():
    import test_upgrade_e2e as upgrade
    assert upgrade._previous_and_latest(["0.48.0", "0.49.1", "0.50.1"]) == ("0.49.1", "0.50.1")
