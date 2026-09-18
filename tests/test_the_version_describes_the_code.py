"""The version this package reports describes the code, not the install record.

⛔ THE DEFECT THESE HOLD. `__version__` came from
`importlib.metadata.version("invisible-playwright")`, which answers about the
DISTRIBUTION the installer put there. An editable install writes that metadata
once and the code keeps moving, so on the machine this is developed on the
record said 0.16.2 while the tree said 0.22.1, six releases apart, minutes after
that checkout had been brought to zero commits behind `origin/main`. Pulling
does not touch the record, which is why this is a defect in the code and not a
stale checkout.

⛔ AND THIS IS THE PACKAGE A MEASUREMENT NAMES. Every bench that drives a
browser reports "measured against invisible-playwright X", and read from the
program X was the moment somebody ran `pip install -e`. A finding was about to
be written with a version six releases wrong; it survived only because its
author happened to read the number off the tree instead. The CI installs with
`pip install -e .`, so the broken install mode is the one the suite runs in.

⛔ THESE TESTS USE REAL `pip` INSTALLS OF A REAL DISTRIBUTION, AND THAT IS NOT
CEREMONY. The claim has two halves - that an editable install freezes its
metadata while recording the source tree (PEP 610), and that this code reads
the tree when it does - and a half tested against a double leaves the joint
tested by nobody. A hand-written `.dist-info` would assert what I believe pip
does instead of what pip does. Measured cost of the two installs: about 40
seconds for the file, once, in module-scoped fixtures.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from invisible_playwright._version import (EDITABLE, declared_by, source_tree,
                                           versions)

PKG = "tinydist"

PYPROJECT = """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "%s"
version = "%%s"
""" % PKG


def _tree(at: Path, version: str) -> Path:
    """A real, minimal, installable distribution declaring `version`."""
    root = at / PKG
    (root / "src" / PKG).mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_bytes((PYPROJECT % version).encode("utf-8"))
    (root / "src" / PKG / "__init__.py").write_bytes(b"")
    return root


def _declare(root: Path, version: str) -> None:
    """Move what the tree says, the way a `git pull` does: the files change and
    nothing reinstalls."""
    root.joinpath("pyproject.toml").write_bytes(
        (PYPROJECT % version).encode("utf-8"))


def _venv(at: Path) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(at)], check=True,
                   capture_output=True)
    if sys.platform == "win32":
        return at / "Scripts" / "python.exe"
    return at / "bin" / "python"


# ⛔ LOADED BY PATH, NOT IMPORTED, AND THAT IS PART OF THE CLAIM. Importing
# `invisible_playwright` runs the package `__init__`, which pulls in the core
# and refuses when the engine configuration it ships does not match the
# environment - in a bare venv that is every time, and the failure would look
# like this module's. `_version` imports nothing but the standard library on
# purpose, so loading the file alone is both what the test needs and a check
# that the module really is free-standing.
ASK = """
import importlib.util, json, sys
import importlib.metadata as md
spec = importlib.util.spec_from_file_location("_v", %r)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(json.dumps({"record": md.version(%r), "versions": mod.versions(%r)}))
"""


def _asks(python: Path) -> dict:
    """Run the question INSIDE that environment and hand back what it printed.

    It has to be asked from in there: `importlib.metadata` answers about the
    interpreter that is running, so asking from here would describe this
    environment instead of that one.
    """
    module = Path(__file__).resolve().parents[1] / "src" / "invisible_playwright" / "_version.py"
    done = subprocess.run([str(python), "-c", ASK % (str(module), PKG, PKG)],
                          capture_output=True, text=True, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def editable(tmp_path_factory):
    """A real editable install, made at 1.0.0 and then left behind by its tree.

    This is the situation exactly: installed once, and the source moves after.
    """
    at = tmp_path_factory.mktemp("editable")
    root = _tree(at, "1.0.0")
    python = _venv(at / "venv")
    subprocess.run([str(python), "-m", "pip", "install", "-q", "--no-deps",
                    "-e", str(root)], check=True, capture_output=True)
    return python, root


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    """A real NORMAL install: the code that runs is the installed copy, so the
    tree moving afterwards must change nothing."""
    at = tmp_path_factory.mktemp("wheel")
    root = _tree(at, "1.0.0")
    python = _venv(at / "venv")
    subprocess.run([str(python), "-m", "pip", "install", "-q", "--no-deps",
                    str(root)], check=True, capture_output=True)
    return python, root


def test_an_editable_install_reports_what_its_tree_says(editable):
    """⛔ THE KNOWN-BAD, ON THE REAL PACKAGING SYSTEM. Install at 1.0.0, move
    the tree to 2.0.0, reinstall nothing. The old code answered 1.0.0 here,
    because that is what the record says and the record is what it read.

    The record is asserted to be STALE in the same breath, because without that
    the test cannot tell "it read the tree" from "pip quietly updated the
    record", and an assertion that cannot separate the two answers of its
    instrument is not asserting anything.
    """
    python, root = editable
    _declare(root, "2.0.0")
    got = _asks(python)

    assert got["record"] == "1.0.0", (
        "pip updated the install record on its own, so this test is not "
        "measuring what it claims to measure")
    code, record = got["versions"]
    assert code == "2.0.0" + EDITABLE, (
        "the version reported is %r, but the code that runs declares 2.0.0" % code)
    assert record == "1.0.0", "the install record must still be reported as it is"


def test_the_two_facts_stay_separate_and_named(editable):
    """The record is kept, not discarded: `pip`, `pip check` and anything
    reading `.dist-info` see that number, and diagnosing the skew needs both.
    What changed is which of the two is called the version."""
    python, root = editable
    _declare(root, "3.0.0")
    got = _asks(python)
    code, record = got["versions"]
    assert code != record, "the two facts collapsed back into one"
    assert code.startswith("3.0.0"), code
    assert record == "1.0.0", record


def test_an_editable_tree_is_never_mistaken_for_the_release(editable):
    """Even when the two numbers agree, an editable tree is not the published
    artifact of that number: it can carry uncommitted work. The local segment
    says so, and it is PEP 440 so it parses and sorts."""
    python, root = editable
    _declare(root, "1.0.0")
    got = _asks(python)
    code, record = got["versions"]
    assert code == "1.0.0" + EDITABLE, code
    assert code != record, (
        "an editable tree reported itself as the released version")


def test_a_normal_install_is_not_second_guessed(wheel):
    """⛔ THE ARM THAT KEEPS THE REMEDY FROM OVERREACHING. For a wheel the
    metadata and the code came out of the same build, so the record IS the
    description of the code and there is nothing truer to read. A tree sitting
    next to it is not what runs, and must not be consulted: a remedy that read
    it would report a version the running code does not have."""
    python, root = wheel
    _declare(root, "9.9.9")
    got = _asks(python)
    code, record = got["versions"]
    assert code == "1.0.0", (
        "a normal install reported %r, taking it from a source tree that is "
        "not the code being run" % code)
    assert record == "1.0.0"
    assert EDITABLE not in code


def test_nothing_installed_at_all_says_so_instead_of_guessing():
    """A checkout on `sys.path` with no install record has nothing to read, and
    says that rather than inventing a number."""
    code, record = versions("this-distribution-does-not-exist")
    assert code == "0.0.0+unknown", code
    assert record == "", record


def test_a_tree_that_declares_nothing_readable_falls_back(tmp_path):
    """`dynamic = ["version"]` says nothing without running a build backend, so
    the record stays the answer. That is the same answer as before this module,
    which is the point: the fallback is a known state, not a guess."""
    root = tmp_path / "dyn"
    root.mkdir()
    root.joinpath("pyproject.toml").write_bytes(
        b'[project]\nname = "dyn"\ndynamic = ["version"]\n')
    assert declared_by(root) is None
    assert declared_by(tmp_path / "there-is-nothing-here") is None


def test_no_module_in_this_package_types_a_version():
    """Derived, never typed. The fallbacks and the local segment are not version
    numbers, and nothing else may be one."""
    import re

    import invisible_playwright

    typed = [p.name for p in Path(invisible_playwright.__file__).parent.rglob("*.py")
             if re.search(r'__version__\s*=\s*["\']\d+\.\d+',
                          p.read_text(encoding="utf-8"))]
    assert not typed, "a version literal is back in %s" % typed


def test_the_running_install_is_described_by_what_it_reports():
    """Whatever mode this suite is running in, the report has to match it. The
    CI runs `pip install -e`, so the editable branch is the one under the
    suite; a wheel run takes the other."""
    from importlib.metadata import distribution

    import invisible_playwright

    dist = distribution("invisible-playwright")
    tree = source_tree(dist)
    if tree is None:
        assert invisible_playwright.__version__ == dist.version
        assert EDITABLE not in invisible_playwright.__version__
    else:
        declared = declared_by(tree)
        assert declared is not None, (
            "this tree is installed editable and declares no readable version")
        assert invisible_playwright.__version__ == declared + EDITABLE
    assert invisible_playwright.__install_record_version__ == dist.version
