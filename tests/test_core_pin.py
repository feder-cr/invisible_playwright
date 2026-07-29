"""This package's wiring into the core-pin assertion (src/.../_pin.py).

THE COMPARISON IS NOT TESTED HERE ANY MORE. It moved into invisible_core.pin,
where it is exercised against real site-packages layouts built under tmp_path
(invisible_core/tests/test_pin.py). That move is the point: the profile manager
consumes the same core and asks the same question, and two copies of the answer
drift until the two products disagree about why an environment is broken.

What is left in this package, and what this file tests:
  * the prelude - the few lines that can still run when invisible_core cannot be
    imported at all, which is the one state a module inside the core can never
    report on. Tested in a subprocess against a stub core;
  * the name binding - every call from this package asks about
    invisible-playwright and never about the other consumer;
  * that no second copy of the comparison grew back here;
  * that the expected core version is written in pyproject and nowhere else.

Context for the numbers below: the core's version is derived from its release
seal (tag firefox-N -> N.CORE_REVISION.0), so 18.0.0 and 19.0.0 are two engine
releases apart, which is exactly the skew this assertion exists to refuse.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

import invisible_core.pin as core_pin
from invisible_playwright import _pin

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "invisible_playwright"
PYPROJECT = ROOT / "pyproject.toml"
SHIM = (SRC / "_pin.py").read_text(encoding="utf-8")

REMEDY = "pip install --force-reinstall invisible-playwright"


def child_env(**overrides) -> dict:
    """Every subprocess below builds a deliberately broken environment, and a
    mismatch is REPAIRED at import now. Without the kill switch one forgotten
    subprocess would run a real `pip install --force-reinstall` against whatever
    environment the suite is running in. The repair itself is exercised in the
    core's tests/test_pin.py, through a recording installer, never through pip."""
    env = dict(os.environ)
    env[core_pin.AUTOFIX_ENV] = "off"
    env.pop(core_pin.AUTOFIX_ATTEMPTED_ENV, None)
    env.update(overrides)
    return env


def declared_pin_in_pyproject() -> str:
    """Read with the SHARED parser, not with a regex of this file's own.

    A test that parses the pin its own way tests its own regex. This file used to
    carry one of six copies of that parsing, each with a different acceptance
    set, which is the defect the parser consolidation removed; reading the pin
    here through invisible_core.pin is what keeps the copy from growing back."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declaration = core_pin.pin_from_requirements(
        data["project"]["dependencies"], dist_name="invisible-playwright")
    assert declaration.status == "pinned", declaration
    return declaration.version


def stub_core(tmp_path: Path, *, name: str, init_body: str = "",
              pin_body: str | None = None) -> Path:
    """A core on sys.path. ``pin_body=None`` ships no ``invisible_core.pin``.

    ``pin_body`` lets a stub echo back what this package HANDED it, which is
    the only way to observe the value rather than the plumbing around it - see
    ``test_the_snapshot_reports_what_was_actually_observed``.
    """
    root = tmp_path / name
    pkg = root / "invisible_core"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(init_body), encoding="utf-8")
    if pin_body is not None:
        # A real core carries BOTH `pin.py` (the implementation since
        # 2026-07-27) and `_pin.py` (the alias), so the stub writes both. This
        # package imports `_pin`, which is what the PUBLISHED core has - see the
        # note in src/.../_pin.py for why that sequencing is not optional.
        (pkg / "pin.py").write_text(textwrap.dedent(pin_body), encoding="utf-8")
        (pkg / "_pin.py").write_text(
            "import sys as _s\nfrom . import pin as _m\n_s.modules[__name__] = _m\n",
            encoding="utf-8")
    return root


def run_import(stub_root: Path) -> subprocess.CompletedProcess:
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(stub_root)!r})
        import invisible_playwright
        print("IMPORT-OK")
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=child_env())


# ── the comparison lives in the core, and this package uses THAT one ─────────

def test_the_check_this_package_runs_is_the_core_s_own_function():
    """Identity, not similarity. If this ever becomes a local reimplementation,
    the two products can diverge on the same environment."""
    assert _pin.pin_problem is core_pin.pin_problem
    assert _pin.installed_core_version is core_pin.installed_core_version
    assert _pin.recorded_core_version is core_pin.recorded_core_version
    assert _pin.editable_core_path is core_pin.editable_core_path
    assert _pin.repair_core is core_pin.repair_core


def test_the_requirement_parser_this_package_uses_is_the_core_s_own():
    """The consolidation, from this side. Six copies of "parse the
    invisible-core== pin" disagreed on which spellings count; the wrapper reaches
    for the one implementation and defines none."""
    assert _pin.parse_requirement is core_pin.parse_requirement
    assert _pin.pin_from_requirements is core_pin.pin_from_requirements
    assert _pin.canonical_requirement is core_pin.canonical_requirement
    assert _pin.normalise_name is core_pin.normalise_name


def test_no_second_copy_of_the_comparison_grew_back_in_this_package():
    """The failure mode this guards: someone re-adds a local pin_problem "just
    for the wrapper", and from then on the two products explain the same broken
    environment differently."""
    local_defs = set(re.findall(r"^def (\w+)", SHIM, re.M))
    forbidden = {"pin_problem", "probe_core", "installed_core_version",
                 "recorded_core_version", "editable_core_path",
                 "parse_requirement", "pin_from_requirements",
                 "canonical_requirement", "normalise_name", "repair_core"}
    assert local_defs & forbidden == set(), local_defs & forbidden


def test_this_package_declares_no_requirement_regex_of_its_own():
    """A `re.compile` here carrying a requirement operator is a second acceptance
    set, and the weakest acceptance set is the one that decides."""
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(SHIM.splitlines(), 1)
        if "re.compile" in line
    ]
    assert offenders == [], offenders


def test_the_wrapper_asks_about_its_own_distribution(monkeypatch):
    """Not the manager's. The two consumers share a core and each has its own
    Requires-Dist; asking with the wrong name reads the wrong pin."""
    asked = []
    monkeypatch.setattr(_pin, "_assert_core_pin", lambda name: asked.append(name))
    _pin.assert_core_pin()
    assert asked == ["invisible-playwright"]

    asked.clear()
    monkeypatch.setattr(_pin, "_declared_core_pin", lambda name: asked.append(name))
    _pin.declared_core_pin()
    assert asked == ["invisible-playwright"]


def test_the_import_time_form_is_the_one_that_repairs(monkeypatch):
    """assert_core_pin reports, enforce_core_pin repairs. The package __init__
    has to call the second one, or the owner's change is inert."""
    init = (SRC / "__init__.py").read_text(encoding="utf-8")
    first_call = next(
        line for line in init.splitlines()
        if line.startswith("_assert_core_pin(") or line.startswith("_enforce_core_pin("))
    assert first_call.startswith("_enforce_core_pin("), first_call
    assert "from ._pin import enforce_core_pin" in init, init


def test_the_snapshot_of_sys_modules_is_taken_before_the_core_is_imported():
    """The whole soundness argument for repairing in process: nobody can be
    holding an object from the version about to be replaced if nothing imported
    it yet. Read one line later and it is always True, and the guard becomes
    theatre that never fires."""
    lines = SHIM.splitlines()
    snapshot_at = next(i for i, ln in enumerate(lines) if "_CORE_PREIMPORTED = " in ln)
    import_at = next(i for i, ln in enumerate(lines)
                     if "from invisible_core.pin import" in ln)
    assert snapshot_at < import_at, (snapshot_at, import_at)


def test_the_enforcement_passes_that_snapshot_through(monkeypatch):
    """Not recomputed inside the core: by the time any line of invisible_core
    runs, invisible_core is imported, so a check made there always says yes."""
    seen = {}

    def spy(dist_name, *, core_preimported, stream=None):
        seen["dist_name"] = dist_name
        seen["core_preimported"] = core_preimported

    monkeypatch.setattr(_pin, "_enforce_core_pin", spy)
    _pin.enforce_core_pin()
    assert seen["dist_name"] == "invisible-playwright"
    assert seen["core_preimported"] is _pin._CORE_PREIMPORTED


def test_the_cli_surface_of_this_module_is_intact():
    """cli.py's `version` subcommand imports these three by name. Losing one in
    a refactor turns `invisible-playwright version` (the command users paste
    into bug reports) into an ImportError."""
    for name in ("declared_core_pin", "installed_core_version", "recorded_core_version"):
        assert callable(getattr(_pin, name)), name


# ── the prelude: the part that cannot be delegated ──────────────────────────

def test_a_core_too_old_to_carry_the_check_is_refused_by_name(tmp_path):
    """A core with no invisible_core.pin cannot run the comparison, and cannot
    say so itself. This is the message that population sees on first upgrade."""
    r = run_import(stub_core(tmp_path, name="ancient"))
    assert r.returncode != 0, r.stdout
    assert "IMPORT-OK" not in r.stdout
    err = " ".join(r.stderr.split())
    assert "ImportError" in err, err
    assert "invisible_core.pin" in err, err
    assert REMEDY in err, err


def test_that_refusal_carries_no_version_literal_and_no_git_install(tmp_path):
    """The remedy names this distribution, so pip resolves the core version out
    of our own Requires-Dist. A version literal here would be a second source of
    truth, and the git channel is not the source of truth any more."""
    r = run_import(stub_core(tmp_path, name="ancient2"))
    err = " ".join(r.stderr.split())
    assert "git+" not in err, err
    assert not re.search(r"invisible-core==\d", err), err


def test_a_core_whose_own_dependency_is_missing_is_not_reported_as_too_old(tmp_path):
    """`pip install --no-deps invisible-core` leaves a core that is on disk and
    raises on import of a third-party module. Collapsing that into "invisible-core
    is missing or too old" sends the user to the wrong package, so the prelude
    classifies by the name of the module that actually went missing."""
    root = stub_core(
        tmp_path, name="nodeps",
        init_body="import a_dependency_that_was_never_installed\n")
    r = run_import(root)
    assert r.returncode != 0, r.stdout
    err = " ".join(r.stderr.split())
    assert "a_dependency_that_was_never_installed" in err, err
    assert "--no-deps" in err, err
    assert "too old" not in err, err
    assert REMEDY in err, err


def test_the_prelude_is_the_first_thing_the_package_does(tmp_path):
    """Ordering matters: if any other import runs first, the user gets a
    ModuleNotFoundError about some internal module instead of the remedy."""
    r = run_import(stub_core(tmp_path, name="ordering"))
    tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
    assert "ModuleNotFoundError" not in tail, (
        "the prelude did not fire first; the user sees an internal import "
        "error:\n" + r.stderr)


# ── the expected version is single-sourced ───────────────────────────────────

def test_the_declared_pin_is_read_back_out_of_the_installed_metadata():
    """The mechanism behind the single source, end to end and unmocked: pip
    generates Requires-Dist from pyproject, and this reads the requirement back
    out of it. If the two disagree, the installed metadata is stale.

    This is an assertion about the ENVIRONMENT, not about the code, and it is
    deliberately not skipped: a stale editable install means the runtime
    assertion is reading a contract nobody edits any more. On 2026-07-25 it
    caught exactly that on the maintainer's machine - the dist-info still
    carried `invisible-core @ git+...` and `playwright<1.61` from version 0.3.0,
    three changes behind the pyproject beside it. The failure has to name the
    cure, or it becomes the red test everybody learns to ignore.
    """
    installed = _pin.declared_core_pin()
    declared = declared_pin_in_pyproject()
    assert installed == declared, (
        f"the installed metadata and pyproject.toml disagree about the core pin.\n"
        f"  pyproject.toml declares : {declared}\n"
        f"  installed metadata says : {installed}\n"
        f"     (None means the metadata carries no comparable version at all, "
        f"which is what a leftover direct reference looks like)\n"
        f"Nothing is wrong with the source. The editable install's dist-info was "
        f"generated from an older pyproject and pip does not regenerate it on "
        f"edit. Refresh it, from this directory:\n"
        f"  pip install -e . --no-deps\n"
        f"--no-deps matters while invisible-core is not on the index yet: a plain "
        f"reinstall would try to resolve the pin and fail."
    )


def test_the_expected_core_version_is_declared_only_in_pyproject():
    """The original bug in miniature is writing the expected version twice. The
    pin literal must appear in pyproject and NOWHERE under src/, or the two
    copies will drift and the assertion will enforce the stale one."""
    want = declared_pin_in_pyproject()
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if want in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        f"the core pin {want!r} is hardcoded in {offenders}; it must be read back "
        f"from this distribution's own metadata via _pin.declared_core_pin()"
    )


def test_no_source_file_hardcodes_a_core_version_requirement():
    """Catches the same mistake written a different way, e.g. an
    `invisible-core>=18` or `CORE_VERSION = "18.0.0"` constant appearing later."""
    pattern = re.compile(r"invisible[-_]core\s*[=><!~]=\s*[\"']?\d", re.I)
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "a core version requirement is hardcoded in source:\n" + "\n".join(offenders)
    )


def test_pyproject_declares_the_pin_exactly_once():
    """Counted through the SHARED parser, not a regex of this file's own.

    This assertion used to read `r'"invisible-core==[^"]+"'`, which is a seventh
    copy of the requirement parsing the consolidation was supposed to have
    removed - the file's own header says a test that parses the pin its own way
    tests its own regex. It survived because nothing had exercised it against a
    spelling it did not anticipate. Declaring the distribution as
    `invisible_core` did exactly that: the pin was still there, still correct,
    and this test read zero of it.

    PEP 503 makes `invisible-core`, `invisible_core`, `Invisible.Core` one name;
    importlib.metadata and pip normalise all of them, so an assertion that only
    accepts one spelling is asserting a coincidence.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pins = [r for r in data["project"]["dependencies"]
            if core_pin.normalise_name(core_pin.parse_requirement(r).name)
            == core_pin.normalise_name(core_pin.CORE_NAME)]
    assert len(pins) == 1, (
        f"expected exactly one core requirement, found {len(pins)}: {pins}\n"
        f"(matched by normalised name, so any legal spelling counts)")
    assert core_pin.pin_from_requirements(
        data["project"]["dependencies"], dist_name="invisible_playwright").status == "pinned"


def test_pyproject_declares_no_direct_reference_and_no_opt_in_for_one():
    """`pip check` is structurally blind to a violated direct reference: it
    carries no version specifier, so there is nothing to compare and a broken
    environment reports exit 0. The hatch opt-in is the switch that would let
    one back in without a build failure."""
    # Comments are allowed to mention the construct; only declarations count.
    live = "\n".join(
        line for line in PYPROJECT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "invisible-core @ git+" not in live
    assert "allow-direct-references = true" not in live


# ── graceful skips stay graceful ─────────────────────────────────────────────

def test_a_consumer_with_no_install_record_declares_nothing():
    """A source checkout never declared anything, so there is nothing to
    enforce. Reported as not-checkable, which is not the same as a pass."""
    report = core_pin.pin_report("invisible-playwright-not-installed")
    assert report["want"] is None
    assert report["verdict"] == "not-checkable"
    assert report["reason"]
    core_pin.assert_core_pin("invisible-playwright-not-installed")  # must not raise


# ── the prelude does not act ─────────────────────────────────────────────────

def test_this_module_never_shells_out_to_pip():
    """The repair exists, but it does NOT live here. There is exactly one place
    in the three distributions where a pip command is built and exactly one place
    where it can be run, both inside invisible_core.pin behind its seam, and
    that is what the guards there are attached to. A second installer in this
    file would be covered by none of them."""
    assert "subprocess" not in SHIM
    assert "pip.main" not in SHIM
    assert "check_call" not in SHIM
    assert "INSTALL_RUNNER" not in SHIM, "a second installer seam grew here"


def test_the_only_non_stdlib_import_here_is_the_guarded_core_import():
    """Ordering hazard: an unguarded heavy import at module level would fire
    before the prelude's except branch could explain anything. The one import
    that is allowed is invisible_core.pin, and it must sit inside the try."""
    lines = SHIM.splitlines()
    try_at = next(i for i, ln in enumerate(lines) if ln.startswith("try:"))
    except_at = next(i for i, ln in enumerate(lines) if ln.startswith("except ImportError"))
    for line in lines:
        if not re.match(r"^(import|from)\s", line):
            continue
        assert "playwright" not in line.lower(), line
        assert "invisible_core" not in line, (
            "the core import must be indented inside the try block: " + line)
    guarded = "\n".join(lines[try_at:except_at])
    assert "from invisible_core.pin import" in guarded


def test_the_messages_carry_no_em_dashes():
    """Project rule: no em-dashes anywhere, including user-facing error text."""
    assert "\u2014" not in SHIM and "\u2013" not in SHIM


# ─────────────────────────────────────────────── the pre-import guard itself
#
# _CORE_PREIMPORTED decides whether the repair may run at all: an in-process
# reload is only sound while nothing has imported the core yet. A mutation run
# on 2026-07-25 replaced the three-line `any(...)` with a constant and the whole
# suite stayed green (22 passed), so the guard that makes the repair safe had
# nothing holding it.
#
# It cannot be tested in-process: it is computed once, at module import, and by
# the time any test body runs both packages are long imported. So each case is a
# fresh interpreter. The two together kill any constant: True-always fails the
# first, False-always fails the second.


def preimport_flag(core_first: bool) -> str:
    code = textwrap.dedent(f"""
        import sys
        if {core_first!r}:
            import invisible_core          # the state the guard exists to detect
        import invisible_playwright._pin as p
        print("FLAG", p._CORE_PREIMPORTED)
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=child_env())
    assert "FLAG" in r.stdout, f"child did not report:\nSTDOUT{r.stdout}\nSTDERR{r.stderr}"
    return r.stdout.split("FLAG", 1)[1].strip().splitlines()[0]


def test_the_guard_is_false_when_this_package_is_imported_first():
    """The normal path. If this reads True the repair never runs at all, and the
    mismatch it exists to fix is reported instead of repaired."""
    assert preimport_flag(core_first=False) == "False"


def test_the_guard_is_true_when_the_core_was_already_imported():
    """The unsafe path. If this reads False the repair proceeds to reload a
    module already bound elsewhere in the process, which is the exact situation
    the guard was written to refuse."""
    assert preimport_flag(core_first=True) == "True"


def _core_pin_imports() -> list[str]:
    """Every name this package pulls out of ``invisible_core.pin``.

    Parsed from the source with ``ast`` rather than written down here or matched
    with a regex: a stub that lags behind that import list kills the child on
    ImportError, and an ImportError in a subprocess looks exactly like the
    assertion this test is meant to be making.
    """
    import ast

    tree = ast.parse(Path(_pin.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "invisible_core.pin":
            return [a.name for a in node.names]
    raise AssertionError("this package no longer imports from invisible_core.pin")


def test_the_snapshot_reports_what_was_actually_observed(tmp_path):
    """THE KILLING TEST for ``_CORE_PREIMPORTED``, and until 2026-07-26 this
    package did not have one.

    Replacing the three-line ``any(name == "invisible_core" or ...)`` with a
    hardcoded ``False`` left the whole suite green. The two tests that touched
    the value could not see the difference: one is a source-order scan, and a
    hardcoded line still sits before the import, so it passed; the other
    asserts ``seen["core_preimported"] is _pin._CORE_PREIMPORTED``, which is a
    tautology whatever the value came from.

    It matters because ``_CORE_PREIMPORTED`` is the ENTIRE soundness argument
    for the in-process repair. ``repair_core()`` refuses to swap the core when
    it is true and the core-side guard is real, but THIS package computes the
    input. Degrade it to a constant False and
    ``import invisible_core; import invisible_playwright`` with a mismatched
    pin drops ``invisible_core.*`` out of ``sys.modules`` and re-imports it
    underneath objects the caller already holds - two versions alive in one
    process, every test still passing.

    The twin already existed next door in the manager and killed the same
    mutation there; this is that pair ported over. A stub core echoes back what
    it was handed, imported once in a child that has NOT touched
    ``invisible_core`` (the answer must be False) and once in a child that has
    (it must be True). No constant satisfies both.
    """
    # The stub has to satisfy the WHOLE `from invisible_core.pin import (...)`
    # list this package makes, or the child dies on ImportError before the
    # value is ever printed - and an ImportError in a subprocess looks exactly
    # like the assertion below failing. Derived from the source, so adding a
    # name to that import cannot silently disarm this test.
    stub = "import sys\n" + "".join(
        f"class {name}:\n    pass\n" if name[0].isupper() else f"{name} = None\n"
        for name in _core_pin_imports() if name != "enforce_core_pin"
    ) + textwrap.dedent("""
        def enforce_core_pin(dist_name, *, core_preimported, stream=None):
            print("PREIMPORTED:" + repr(core_preimported))
            sys.stdout.flush()
    """)
    root = stub_core(tmp_path, name="snapshot", pin_body=stub)

    clean = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(root)!r})
            import invisible_playwright
        """)], capture_output=True, text=True, env=child_env())
    assert "PREIMPORTED:False" in clean.stdout, clean.stdout + clean.stderr

    dirty = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(root)!r})
            import invisible_core
            import invisible_playwright
        """)], capture_output=True, text=True, env=child_env())
    assert "PREIMPORTED:True" in dirty.stdout, dirty.stdout + dirty.stderr
