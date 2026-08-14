"""What happens to somebody who ALREADY has this installed.

Every install test next door starts from an empty venv, which is the path a new
user takes exactly once. Every existing user takes a different one - `pip
install -U`, or an environment that has drifted - and none of it was covered.

That matters more here than in most packages, because of the shape of the
coupling: the wrapper declares an EXACT `invisible-core==X.Y.Z`, so an upgrade
has to move two distributions in step, and a half-moved environment is repaired
IN PROCESS at import rather than raising. A repair that silently does the wrong
thing therefore looks identical to one that worked - from the outside, the
import simply returns.

The failures these are aimed at:

  * `pip install -U invisible-playwright` leaves the OLD core behind, so the
    wrapper drives an engine configured by a core it was never tested against;
  * the repair runs when the core is already imported, swapping it underneath
    objects the caller holds - two versions alive in one process;
  * a user who pins an old core by hand gets a green `pip check` and a broken
    session, which is the failure mode the exact specifier was adopted to make
    loud in the first place.

Marked `e2e`: builds venvs and talks to the index.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import os

import subprocess

import pytest

# Le variabili che NON devono attraversare un venv costruito qui, e la copia di
# `_clean_env` accanto a loro.
#
# **La duplicazione con `test_release_e2e.py` e' voluta e c'e' un gate che la
# pretende**: `test_no_install_e2e_file_imports_a_package_the_runner_does_not_have`
# nel core parsa questi file e rifiuta ogni import di modulo che non sia stdlib,
# pytest, packaging o pip, con il messaggio "Keep the helpers local in these
# files - the duplication is the point". Un modulo condiviso qui sarebbe un
# errore di RACCOLTA sul runner, cioe' un job rosso che non ha testato niente.
# Fattorizzare e' la mossa giusta ovunque tranne che in questi due file.
#
# Misurato 2026-08-11: `PYTHONPATH` verso i sorgenti del banco fa importare al
# venv il core del banco invece di quello installato (2 fallimenti qui, 5
# nell'altro file); `INVISIBLE_SEAL_FILE` verso un sigillo locale fa fallire i
# test che scaricano la release pubblicata. Sedici rossi in un giorno, nessuno
# del prodotto - e il verso pericoloso e' l'altro: un verde da un ambiente che
# nessun utente ha.
_NON_ERMETICHE = ("PYTHONPATH", "INVISIBLE_SEAL_FILE", "PYTHONHOME")


def _clean_env(base=None):
    """L'ambiente per un sottoprocesso, senza le variabili che filtrano.

    Torna anche cosa ha tolto: una variabile rimossa in silenzio e' la stessa
    classe di difetto di una ereditata in silenzio, col segno cambiato.
    """
    env = dict(os.environ if base is None else base)
    tolte = [k for k in _NON_ERMETICHE if k in env]
    for k in tolte:
        env.pop(k, None)
    return env, tolte


# ── venv mechanics, LOCAL ON PURPOSE ────────────────────────────────────────
# These three live in `invisible_core.testing` and every other suite in the
# three repos imports them from there. NOT this file. The job that runs it
# installs pytest on the runner and nothing else, deliberately: what is being
# tested is what a stranger gets from the index, and a second copy of anything
# on the runner's path would mean the assertions read the wrong one.
#
# So `from invisible_core.testing import ...` here is a COLLECTION error -
# ModuleNotFoundError, whole job red, nothing tested. It happened on every leg
# of all three repos on 2026-07-28, the day the helpers were shared, and it
# passes on any development machine because an editable install is always on
# the path. The core's suite now parses these files and refuses the import, so
# the rule does not depend on this comment being read.
def _run(cmd, *, timeout: int = 600, check: bool = True, env=None, cwd=None):
    # `env=None` significava EREDITA, che e' il difetto. Adesso il default e'
    # l'ambiente ripulito, e anche un `env=` esplicito viene ripulito, perche'
    # chi ne costruisce uno parte quasi sempre da `os.environ`.
    env, tolte = _clean_env(env)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout, env=env,
                       cwd=str(cwd) if cwd is not None else None)
    if check and r.returncode != 0:
        nota = ""
        if tolte:
            nota = ("\n--- ambiente ---\nrimosse prima di lanciare: {}\n"
                    "(sono le variabili che entrerebbero nel venv e gli farebbero "
                    "misurare qualcosa che non e' cio' che riceve un utente)"
                    .format(", ".join(tolte)))
        raise AssertionError(
            "{} exited {}\n--- stdout ---\n{}\n--- stderr ---\n{}{}".format(
                " ".join(str(c) for c in cmd), r.returncode,
                r.stdout[-3000:], r.stderr[-3000:], nota))
    return r


def _venv_python(venv_dir):
    bindir = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return Path(venv_dir) / bindir / name


def _make_venv(target, *, upgrade_pip: bool = True):
    target = Path(target)
    _run([sys.executable, "-m", "venv", target], timeout=300)
    py = _venv_python(target)
    assert py.exists(), "no venv python at {}".format(py)
    if upgrade_pip:
        _run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], timeout=300)
    return py


run_checked = _run
venv_python = _venv_python
make_venv = _make_venv


# The venv mechanics live in `invisible_core.testing`, and they are on the index
# as of the core release this package's pin now names - the pin moved in the same
# change, which is the order that was got wrong on 2026-07-28 and is written into
# CLAUDE.md's pre-push gate: publish the core, move the pin, then use the name.
#
# (No version number above, deliberately. A sibling guard,
# `test_the_expected_core_version_is_written_only_in_pyproject`, forbids a second
# copy of it anywhere in this repo - and it caught the first draft of this very
# comment, which is exactly the behaviour it is for.)
_run = run_checked
_venv_python = venv_python
_make_venv = make_venv

pytestmark = pytest.mark.e2e

WRAPPER = "invisible-playwright"
CORE = "invisible-core"


def _fresh_venv(root: Path, name: str) -> Path:
    return _make_venv(root / name)


def _versions(py: Path) -> dict:
    out = _run([str(py), "-c",
                "import json; from importlib.metadata import version; "
                f"print(json.dumps({{'wrapper': version({WRAPPER!r}), "
                f"'core': version({CORE!r})}}))"], timeout=60).stdout
    return json.loads(out.strip().splitlines()[-1])


def _released_versions(py: Path, dist: str) -> list[str]:
    """Versions the index will serve, newest last. Read from pip itself so the
    test does not carry a hardcoded list that goes stale on the next release."""
    r = _run([str(py), "-m", "pip", "index", "versions", dist],
             timeout=180, check=False)
    text = r.stdout + r.stderr
    for line in text.splitlines():
        if "Available versions:" in line:
            return [v.strip() for v in
                    line.split("Available versions:", 1)[1].split(",")][::-1]
    pytest.skip(f"pip could not list versions for {dist}: {text[-500:]}")


@pytest.fixture(scope="module")
def workspace():
    root = Path(tempfile.mkdtemp(prefix="invpw-upgrade-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upgrading_from_the_previous_release_moves_the_core_too(workspace: Path):
    """The one every existing user will run.

    Installs the PREVIOUS published wrapper, then upgrades. Both distributions
    must end up where the new wrapper's pin says, and pip must agree. Before
    the pin was an exact specifier this was the failure: a wrapper that moved
    while its core stayed put, reported clean by `pip check` because a direct
    URL has no version to compare.
    """
    py = _fresh_venv(workspace, "upgrade")
    released = _released_versions(py, WRAPPER)
    if len(released) < 2:
        pytest.skip("only one release on the index; nothing to upgrade FROM")
    previous = released[-2]

    _run([str(py), "-m", "pip", "install", "--no-cache-dir",
          f"{WRAPPER}=={previous}"], timeout=900)
    before = _versions(py)
    assert before["wrapper"] == previous

    _run([str(py), "-m", "pip", "install", "--no-cache-dir", "--upgrade",
          WRAPPER], timeout=900)
    after = _versions(py)

    assert after["wrapper"] != previous, "the upgrade did not move the wrapper"
    _run([str(py), "-m", "pip", "check"], timeout=180)

    declared = _run([str(py), "-c",
                     "from importlib.metadata import requires; "
                     f"print([r for r in requires({WRAPPER!r}) "
                     "if r.startswith('invisible')][0])"], timeout=60).stdout.strip()
    assert after["core"] in declared, (
        f"after upgrading, the wrapper declares {declared!r} and the "
        f"environment holds {CORE} {after['core']} - the two moved out of step")


def test_the_upgraded_environment_imports_and_reports_one_core(workspace: Path):
    """An import that repairs must leave ONE core in sys.modules.

    The dangerous outcome is not an exception - it is a swap performed under
    objects the caller already holds, which leaves two versions alive and shows
    up much later as behaviour that does not match the version anybody reports.
    """
    py = _venv_python(workspace / "upgrade")
    if not py.exists():
        pytest.skip("the upgrade fixture did not run")
    out = _run([str(py), "-c",
                "import invisible_playwright, sys, invisible_core; "
                "from importlib.metadata import version; "
                "mods = sorted({m.split('.')[0] for m in sys.modules "
                "               if m.startswith('invisible_core')}); "
                "print(mods); print(version('invisible-core')); "
                "print(invisible_core.__file__)"], timeout=180).stdout.strip()
    mods, reported, path = out.splitlines()[:3]
    assert mods == "['invisible_core']", f"more than one core root: {mods}"
    # The imported module must be the INSTALLED one, not a checkout that
    # happened to be on the path - a venv test that silently read the
    # developer's working tree would pass everywhere and mean nothing.
    assert "site-packages" in path, (
        f"the core was imported from {path}, which is not the install under "
        f"test")
    # And the version the metadata reports must be the one that module derives
    # from its own packaged seal: if a swap left a stale module object behind,
    # these disagree.
    derived = _run([str(py), "-c",
                    "import invisible_core; print(invisible_core.__version__)"],
                   timeout=120).stdout.strip()
    assert derived == reported, (
        f"metadata says {CORE} {reported} but the imported module derives "
        f"{derived} - two versions are alive in one process")


def test_a_hand_pinned_old_core_is_reported_by_pip_check(workspace: Path):
    """The failure the exact specifier exists to expose.

    A user pins an old core by hand - a lockfile, a constraints file, a
    `--no-deps` install. With a direct-URL dependency there is no version for
    pip to compare and `pip check` reports clean on a broken environment; with
    a real specifier it exits 1 and names both sides.
    """
    py = _fresh_venv(workspace, "pinned")
    _run([str(py), "-m", "pip", "install", "--no-cache-dir", WRAPPER],
         timeout=900)
    cores = _released_versions(py, CORE)
    if len(cores) < 2:
        pytest.skip("only one core release on the index")
    older = cores[-2]

    _run([str(py), "-m", "pip", "install", "--no-cache-dir", "--no-deps",
          "--force-reinstall", f"{CORE}=={older}"], timeout=600)

    checked = _run([str(py), "-m", "pip", "check"], timeout=180, check=False)
    assert checked.returncode != 0, (
        "pip check reported a deliberately mismatched environment as clean. "
        "That is the exact blindness the direct-URL dependency had, and it "
        "means the pin has stopped being a real specifier")
    assert CORE in (checked.stdout + checked.stderr)


def test_the_mismatch_repairs_itself_at_import_without_a_restart(workspace: Path):
    """Auto-repair, driven end to end rather than argued about.

    The environment above is genuinely broken. Importing the wrapper must fix
    it in the SAME process - that is the documented behaviour, and the reason
    the repair is worth its complexity. Asserted on the version afterwards,
    not on the absence of an exception, because "no exception" is also what a
    repair that did nothing looks like.
    """
    py = _venv_python(workspace / "pinned")
    if not py.exists():
        pytest.skip("the pinned fixture did not run")
    before = _versions(py)["core"]

    out = _run([str(py), "-c",
                "import invisible_playwright; "
                "from importlib.metadata import version; "
                "print('AFTER', version('invisible-core'))"],
               timeout=900)
    after = out.stdout.strip().splitlines()[-1].split()[-1]
    assert after != before, (
        f"the core is still {before} after importing the wrapper - the repair "
        f"did not run, or ran and changed nothing.\n{out.stdout[-2000:]}")
    _run([str(py), "-m", "pip", "check"], timeout=180)
