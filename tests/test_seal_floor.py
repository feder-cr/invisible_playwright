"""The import-time core assertion, exercised end to end in a subprocess.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md section 4.14 and 7.
Audit: 16-version-divergence-matrix.md S1 and axis fact 7.

Two failures live here, and they are different failures.

S1, the original one: a core so old it carries no release seal at all. Every
symbol the wrapper imports from it traces back to the core's first commit, so
the pair never raises ImportError and the user runs new wrapper code against old
constants, silently. This file converts exactly that population into one message
on their first upgrade.

The one that outlives it: once the wrapper declares `invisible-core==N.M.0`,
dependency resolution makes S1 unreachable, and what is left is a core that
carries a seal and is still the wrong version - an install that skipped
resolution (--no-deps, a lockfile), or a partial install whose files moved but
whose dist-info did not.

THE STUB CORES BELOW CARRY THE REAL pin.py AND _version.py. Since the
comparison moved into invisible_core, a stub without them cannot reach the
comparison at all: it would only ever produce the prelude's "core missing or too
old" message, and every assertion here would be about the wrong code path. So
the stub is built by copying this repo's installed core modules beside a
fabricated seal.json, which means the version under test is derived the real
way, from a seal tag and CORE_REVISION.

No browser, no network. The real installation is never disturbed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import invisible_core.pin as core_pin
from invisible_playwright import _pin

# The remedy is a projection of the pin declared in pyproject, exactly as the
# shipped message is. Hardcoding it here would be a second source of truth for
# the same number, which is the mistake this whole change removes.
DECLARED = _pin.declared_core_pin()

CORE_PKG = Path(importlib.util.find_spec("invisible_core").origin).resolve().parent


def child_env(**overrides) -> dict:
    """Every stub below is a deliberately broken environment, and a mismatch is
    REPAIRED at import now. Without the kill switch, `test_a_sealed_core_at_the
    _wrong_version_is_refused` would run a real `pip install --force-reinstall`
    against whatever environment the suite happens to be running in. The repair
    is exercised on purpose in the core's tests/test_pin.py, through a recording
    installer, never through pip."""
    env = dict(os.environ)
    env[core_pin.AUTOFIX_ENV] = "off"
    env.pop(core_pin.AUTOFIX_ATTEMPTED_ENV, None)
    env.update(overrides)
    return env


def stub_core(tmp_path: Path, *, with_seal: bool, version: str | None = None,
              with_seal_json: bool = True) -> Path:
    """A core on sys.path built out of the real pin modules.

    ``with_seal=False`` reproduces the pre-seal field population: on disk,
    version-derivable, but carrying no invisible_core.seal. The __init__ reaches
    for it the way the shipped one does (transitively, through download.py), so
    the failure arrives where it arrives in a real installation: while
    invisible_core is importing, before anything inside it can report on itself.

    ``with_seal_json=False`` is the other damaged shape, and the one that was
    measured to produce a raw FileNotFoundError: the package is complete except
    for seal.json, which _version.py reads at import time.

    ``version`` is delivered the way the real package delivers it, through
    seal.json's tag number and CORE_REVISION, so nothing here fakes the
    derivation.
    """
    version = version or DECLARED or "18.0.0"
    major, minor = version.split(".")[0], version.split(".")[1]
    suffix = "with_seal" if with_seal else "no_seal"
    if not with_seal_json:
        suffix = "no_seal_json"
    root = tmp_path / f"stub_{suffix}"
    pkg = root / "invisible_core"
    pkg.mkdir(parents=True)
    # A light __init__: the real one imports requests and friends, which the
    # floor would then classify as a dependency failure rather than let the
    # comparison run. It keeps the two imports that decide this file's cases:
    # the seal module and the derived version, both of which the shipped
    # __init__ reaches transitively.
    (pkg / "__init__.py").write_text(
        "from .seal import active_seal  # noqa: F401\n"
        "from ._version import __version__  # noqa: F401\n",
        encoding="utf-8")
    # Copy whichever shape the INSTALLED core has, because that is the shape a
    # user's environment has. Cores published before the rename carry `_pin.py`
    # only; newer ones carry `pin.py` plus a two-line `sys.modules` alias at
    # `_pin.py`. Copying `pin.py` unconditionally made this whole file fail
    # against a published core that predated it - eight tests, and the stub was
    # never built.
    for name in ("pin.py", "_pin.py"):
        if (CORE_PKG / name).exists():
            shutil.copy2(CORE_PKG / name, pkg / name)
    assert (pkg / "_pin.py").exists(), (
        f"the installed core at {CORE_PKG} has neither pin.py nor _pin.py, so "
        f"there is no pin machinery to build a stub from")
    text = (CORE_PKG / "_version.py").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^CORE_REVISION\s*=\s*\d+", f"CORE_REVISION = {minor}", text)
    (pkg / "_version.py").write_text(text, encoding="utf-8")
    if with_seal_json:
        (pkg / "seal.json").write_text(
            json.dumps({"tag": f"firefox-{major}"}), encoding="utf-8")
    if with_seal:
        (pkg / "seal.py").write_text(
            "def active_seal():\n    return None\n", encoding="utf-8")
    return root


def run_import(stub_root: Path, module: str) -> subprocess.CompletedProcess:
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(stub_root)!r})
        import {module}
        print("IMPORT-OK")
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=child_env())


def test_the_stub_actually_shadows_the_installed_core(tmp_path):
    """Guard the test itself: if the stub were not the module being imported,
    every assertion below would be vacuous. Both the package and the pin module
    have to come from the stub."""
    root = stub_core(tmp_path, with_seal=True)
    code = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {str(root)!r})
        import invisible_core, invisible_core.pin as p
        print(json.dumps({{"pkg": invisible_core.__file__, "pin": p.__file__}}))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=child_env())
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert str(root) in got["pkg"], got
    assert str(root) in got["pin"], got


def test_the_stub_derives_its_version_the_real_way(tmp_path):
    """The stub is only worth trusting if its version comes out of seal.json and
    CORE_REVISION, like the shipped package's does."""
    root = stub_core(tmp_path, with_seal=True, version="19.2.0")
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(root)!r})
        from invisible_core import _pin
        print("VERSION:" + str(_pin.installed_core_version()))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=child_env())
    assert r.returncode == 0, r.stderr
    assert "VERSION:19.2.0" in r.stdout, r.stdout


def test_import_fails_loudly_when_the_core_has_no_seal(tmp_path):
    """S1: the whole silent stale-core field population, converted into one
    message the user can act on.

    It reaches the floor rather than a check inside the core, and that is the
    point: invisible_core.__init__ reaches the seal module transitively, so the
    import dies before any line of the core can report on the core. There used
    to be a probe inside _pin for this state, and it could never fire."""
    r = run_import(stub_core(tmp_path, with_seal=False), "invisible_playwright")
    assert r.returncode != 0, r.stdout
    assert "IMPORT-OK" not in r.stdout
    err = " ".join(r.stderr.split())
    assert "ImportError" in r.stderr, r.stderr
    assert "invisible_core.seal" in err, err
    assert "pip install" in err, err


def test_a_core_whose_seal_json_is_gone_gets_the_message_not_a_traceback(tmp_path):
    """MEASURED before this change: deleting invisible_core/seal.json made
    `import invisible_playwright` die with a raw FileNotFoundError naming an
    absolute path inside site-packages. _version.py reads the seal at import and
    invisible_core.__init__ reaches _version, so the failure is not an
    ImportError and the floor's ImportError branch never saw it.

    That was the one broken state a user met as a traceback. It is now the same
    shape of message as every other one: what is wrong, why, and what to run."""
    r = run_import(stub_core(tmp_path, with_seal=True, with_seal_json=False),
                   "invisible_playwright")
    assert r.returncode != 0, r.stdout
    assert "IMPORT-OK" not in r.stdout
    tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
    assert "FileNotFoundError" not in tail, (
        "the raw traceback is still the last thing the user sees:\n" + r.stderr)
    err = " ".join(r.stderr.split())
    assert "ImportError" in err, err
    assert "seal.json" in err, err
    assert "pip install --force-reinstall invisible-playwright" in err, err
    assert not re.search(r"invisible-core==\d", err), err


def test_the_message_names_the_remedy_and_the_reason(tmp_path):
    """A floor that only says "too old" sends the user to a command they have to
    invent. The remedy names an index distribution, which is what pip can
    actually resolve and what `pip check` can actually verify afterwards."""
    r = run_import(stub_core(tmp_path, with_seal=False), "invisible_playwright")
    err = " ".join(r.stderr.split())
    assert "invisible-core" in err, err
    assert "git+" not in err, (
        "the remedy still points at the git channel, which is the path whose "
        "direct_url.json lies and which pip check cannot verify:\n" + err)


def test_the_floor_is_the_first_thing_the_package_does(tmp_path):
    """Ordering matters: if any other import runs first, the user gets a
    ModuleNotFoundError about some internal module instead of the remedy."""
    r = run_import(stub_core(tmp_path, with_seal=False), "invisible_playwright")
    err = r.stderr
    tail = err.strip().splitlines()[-1] if err.strip() else ""
    assert "invisible-core" in err or "invisible_core" in err, err
    assert "ModuleNotFoundError" not in tail, (
        "the floor did not fire first; the user sees an internal import error:\n" + err)


def test_a_core_that_carries_a_seal_at_the_declared_version_passes_the_floor(tmp_path):
    """The check must be a floor, not a wall: a sealed core at the version this
    wrapper declares clears it. (The stub cannot satisfy the rest of the
    wrapper's imports, so this asserts only that the failure is no longer
    ours.)"""
    r = run_import(stub_core(tmp_path, with_seal=True), "invisible_playwright")
    err = " ".join(r.stderr.split())
    assert "version mismatch" not in err, err
    assert "carries no release seal" not in err, err


@pytest.mark.skipif(DECLARED is None,
                    reason="this wrapper declares no exact core pin yet")
def test_a_sealed_core_at_the_wrong_version_is_refused(tmp_path):
    """The case the seal-presence check was structurally blind to: importable,
    sealed, and two engine releases away from what this wrapper was built
    against."""
    wrong = f"{int(DECLARED.split('.')[0]) + 1}.0.0"
    r = run_import(stub_core(tmp_path, with_seal=True, version=wrong),
                   "invisible_playwright")
    assert r.returncode != 0, r.stdout
    err = " ".join(r.stderr.split())
    assert "ImportError" in r.stderr, r.stderr
    assert wrong in err and DECLARED in err, err
    # Which remedy is offered depends on how the core was installed (an editable
    # core is deliberately never told to reinstall), so assert the refusal and
    # both versions here; the per-branch wording is unit-tested in the core's
    # tests/test_pin.py.
    assert "invisible-core" in err, err


def test_the_real_installation_clears_the_floor():
    """The installed core must satisfy its own floor, or every user is blocked."""
    r = subprocess.run(
        [sys.executable, "-c", "import invisible_playwright; print('IMPORT-OK')"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "IMPORT-OK" in r.stdout
