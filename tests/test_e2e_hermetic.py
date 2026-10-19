"""The two RELEASE e2e files do not let the environment leak into the venv.

**Why this file exists.** `test_release_e2e.py` and `test_upgrade_e2e.py`
create a venv and install the package into it FROM THE INDEX: they are the
only thing that verifies what a user actually receives. But a venv INHERITS
the environment, and until 2026-08-14 `subprocess.run(env=None)` passed it
through intact. Measured on 2026-08-11: `PYTHONPATH` pointing at the bench's
sources and `INVISIBLE_SEAL_FILE` pointing at a local seal produced **sixteen
failures in one day, none of them the product's**. And the dangerous
direction is the other one: a GREEN that comes from an environment no user
has.

**Why it's a test and not a comment.** The fix lives in TWO copies, one per
file, and the duplication is enforced by a gate in the core
(`test_no_install_e2e_file_imports_a_package_the_runner_does_not_have`, in
`invisible_core/tests/test_marker_vocabulary.py`): those files must be
collectible with only stdlib and pytest on the runner, so a shared module
would be a collection error. Two copies diverge unless something compares
them.

The case that matters is the LAST one: the check that proves that without the
fix the variable would really get through. A test that only checks the
corrected version cannot distinguish "the fix works" from "the problem never
existed".
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from invisible_core import install_job

HERE = Path(__file__).resolve().parent
E2E_FILES = ("test_release_e2e.py", "test_upgrade_e2e.py")

#: What the copies must cover: the core's one definition (core 50.48.0), the
#: interpreter's variables and every variable its library reads. These test
#: files can import the core; the two e2e files cannot.
EXPECTED = install_job.LEAKS
#: Set in the caller's environment by the cases below: the two measured on
#: 2026-08-11, PYTHONHOME, and the engine folder a developer may have set.
_SET = ("PYTHONPATH", "INVISIBLE_SEAL_FILE", "PYTHONHOME",
        "INVISIBLE_PLAYWRIGHT_ENGINE_DIR")


def _load(name: str):
    """Import the e2e module by PATH, not by name.

    The name would depend on how pytest populated `sys.path`, and this test
    must hold even outside a run that collects those files.
    """
    path = HERE / name
    spec = importlib.util.spec_from_file_location(name[:-3] + "_loaded", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules():
    return {n: _load(n) for n in E2E_FILES}


def test_both_files_declare_the_same_variables(modules):
    """Two copies that diverge are worse than a single copy."""
    seen = {n: tuple(sorted(m._LEAKS)) for n, m in modules.items()}
    values = set(seen.values())
    assert len(values) == 1, (
        "the two e2e files clean up DIFFERENT environments, so one of them "
        "lets through something the other one stops:\n  "
        + "\n  ".join("%s -> %s" % (n, v) for n, v in sorted(seen.items())))
    missing = sorted(set(EXPECTED) - set(values.pop()))
    assert not missing, (
        "the copies leave %s in the venvs they build: invisible_core.install_job."
        "LEAKS names them, and the core's library reads every one" % missing)


@pytest.mark.parametrize("name", E2E_FILES)
def test_clean_env_removes_the_variables_and_leaves_the_rest(modules, name, monkeypatch):
    m = modules[name]
    monkeypatch.setenv("PYTHONPATH", "/c/src/firefox-stealth/release/invisible_core/src")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", "C:/tmp/seal-locale.json")
    monkeypatch.setenv("PYTHONHOME", "/opt/altrove")
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_ENGINE_DIR", "C:/tmp/engines")
    monkeypatch.setenv("UNA_QUALSIASI", "resta")
    for k in set(EXPECTED) - set(_SET):
        monkeypatch.delenv(k, raising=False)

    env, removed = m._clean_env()

    for k in EXPECTED:
        assert k not in env, "%s: %s is still in the subprocess environment" % (name, k)
    assert sorted(removed) == sorted(_SET), (
        "%s: claims to have removed %s" % (name, removed))
    assert env.get("UNA_QUALSIASI") == "resta", (
        "%s: cleaned up more than it should have. An emptied environment "
        "breaks things that are unrelated to it - PATH, TEMP, the proxy "
        "variables - and the fix becomes a new defect." % name)


@pytest.mark.parametrize("name", E2E_FILES)
def test_the_subprocess_really_does_not_see_them(modules, name, monkeypatch):
    """The REAL path: not `_clean_env` in isolation, but `_run`.

    This is the difference between testing the helper and testing what the
    file actually does. The original defect was not in a helper: it was
    `env=None` in the call.
    """
    m = modules[name]
    monkeypatch.setenv("PYTHONPATH", "/percorso/del/banco")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", "C:/tmp/seal-locale.json")

    script = ("import os;"
              "print(os.environ.get('PYTHONPATH'), os.environ.get('INVISIBLE_SEAL_FILE'))")
    out = m._run([sys.executable, "-c", script], timeout=60).stdout.strip()
    assert out == "None None", (
        "%s: the subprocess launched by _run still sees the caller's "
        "environment: %r" % (name, out))


def test_check_without_the_fix_the_variable_WOULD_PASS_THROUGH(monkeypatch):
    """The known-bad input, which here is the WORLD BEFORE the fix.

    Without this, the three tests above cannot distinguish "the fix works"
    from "the problem never existed". It reproduces the call as it used to
    be - `env=None` - and expects the variable to arrive at its destination.
    """
    monkeypatch.setenv("PYTHONPATH", "/percorso/del/banco")
    script = "import os; print(os.environ.get('PYTHONPATH'))"
    r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                       text=True, timeout=60, env=None)
    assert r.stdout.strip() == "/percorso/del/banco", (
        "the check does not reproduce the defect: with env=None the variable "
        "should have passed through, and it did not. So the tests above are "
        "not demonstrating what they appear to, and it is the BENCH that is "
        "broken - not the fix that works. Got: %r" % r.stdout)
