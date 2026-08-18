"""Prelude for the core-pin assertion, then delegation to the core.

THE COMPARISON IS NOT HERE. It lives in invisible_core.pin, because the other
consumer of the core (the profile manager) asked the same question and two
copies of the answer drift: the day the two products disagree about why an
environment is broken, the check has stopped being a diagnosis. That repository
was deleted on 2026-08-18 and the comparison stays where it is - the core is
what owns the pin, and it is installed on its own by anybody who wants it.
This module is the small part that genuinely cannot be delegated, plus
name-bound wrappers so the rest of this package can keep calling the functions
without repeating our own distribution name.

WHAT CANNOT BE DELEGATED. A module inside invisible_core cannot report that
invisible_core is absent, unimportable, or so damaged that it cannot derive its
own version: by the time any line of it runs, it imported. So the import below
is the whole floor, and its except branches classify the failure:

  * ImportError naming a module under invisible_core - either the core is not
    installed at all, or it is older than this check and ships no
    invisible_core.pin (or no invisible_core.pin.enforce_core_pin). Those
    cannot be told apart from here and do not need to be: one remedy fixes all
    of them, `pip install --force-reinstall invisible-playwright`, which makes
    pip resolve the core version from our own Requires-Dist. No version literal
    in the message, so nothing here can drift out of step with pyproject.
  * ImportError naming any other module - the core is installed and one of ITS
    dependencies is missing. That is what `pip install --no-deps` leaves behind,
    and reporting it as "invisible-core is not installed" sends the user to the
    wrong package.
  * anything else - the core is on disk and its import raised something that is
    not an ImportError at all. Measured: delete invisible_core/seal.json and the
    import dies with a raw FileNotFoundError naming an absolute path, because
    invisible_core.__init__ reaches _version.py, which reads the seal. That is a
    partial install, and without this branch it is the one broken state the user
    sees as a traceback instead of a remedy.

Both message sets are deliberately short and carry no version number: everything
that needs one is on the other side of this import.

WHY THE REPAIR IS NOT ATTEMPTED IN THIS FILE. enforce_core_pin() repairs a
version mismatch by installing the declared version and picking it up in
process. It can only do that because it knows WHICH version is declared, and
that comes from the one requirement parser, which lives in the core. When the
core cannot be imported at all there is no parser to ask, and the only remedy
that needs no version literal is a force-reinstall of THIS distribution, i.e. of
the package that is halfway through importing right now. Replacing your own
files mid-import is not a repair, so this floor reports and stops.
"""
from __future__ import annotations

import sys as _sys

DIST_NAME = "invisible-playwright"

# Captured BEFORE the core is imported, and it is the whole soundness argument
# for the in-process repair: if nothing has imported invisible_core by the time
# this package's __init__ starts, then nobody is holding an object from the
# version the repair is about to replace. Read here, at the top of the first
# module the package imports, because one line later it is always True.
_CORE_PREIMPORTED = any(
    name == "invisible_core" or name.startswith("invisible_core.")
    for name in list(_sys.modules)
)

# `invisible_core.pin`, and the ORDER that made it safe to write.
#
# Nineteen names imported here at module level is a public module whatever the
# underscore says, so the core renamed `_pin` to `pin`. Moving this import in
# the SAME change turned both consumers' CI red on every leg -
# `ModuleNotFoundError: No module named 'invisible_core.pin'` - because the
# rename carried no version bump and this package declares `invisible-core==`
# exactly, so pip resolves what the INDEX has. The import floor reported it
# correctly; the sequencing was wrong.
#
# The rule, now in CLAUDE.md's pre-push gate: publish the core, THEN move the
# pin, THEN use the name. All three happened in that order, and this line is
# the third step. A local editable core cannot see this class of failure at
# all - it IS the working tree - so the check is a bare venv with the index
# core, which is how it was verified before this landed.
try:  # the floor - see the module docstring for why this is the one local part
    from invisible_core.pin import (  # noqa: F401
        CORE_NAME,
        AUTOFIX_ENV,
        SKIP_ENV,
        PinDeclaration,
        Requirement,
        assert_core_pin as _assert_core_pin,
        canonical_requirement,
        declared_core_pin as _declared_core_pin,
        editable_core_path,
        enforce_core_pin as _enforce_core_pin,
        installed_core_version,
        normalise_name,
        parse_requirement,
        pin_declaration as _pin_declaration,
        pin_from_requirements,
        pin_problem,
        pin_report as _pin_report,
        recorded_core_version,
        repair_core,
    )
except ImportError as _e:
    _missing = getattr(_e, "name", "") or ""
    if _missing and not _missing.startswith("invisible_core"):
        raise ImportError(
            f"invisible-core is installed but cannot be imported: no module named "
            f"{_missing!r}.\n"
            f"That is one of its own dependencies, so it was installed without "
            f"dependency resolution (pip install --no-deps, or a lockfile that "
            f"lists the packages separately).\n"
            f"Run: pip install --force-reinstall {DIST_NAME}"
        ) from _e
    raise ImportError(
        "invisible-core is missing, or too old for this invisible-playwright: it "
        "does not carry the release-pin check (invisible_core.pin), so the engine "
        "it downloads cannot be matched against the configuration this package "
        "ships.\n"
        f"Run: pip install --force-reinstall {DIST_NAME}"
    ) from _e
except Exception as _e:
    raise ImportError(
        f"invisible-core is installed but it cannot even determine its own version: "
        f"importing it raised {type(_e).__name__}: {_e}\n"
        f"That is a partial or damaged installation, not an old one. A missing or "
        f"unreadable invisible_core/seal.json is the usual cause, and it is read "
        f"while invisible_core is still importing, so nothing inside the core can "
        f"report it.\n"
        f"Run: pip install --force-reinstall {DIST_NAME}"
    ) from _e


def declared_core_pin(dist_name: str = DIST_NAME) -> "str | None":
    """The `invisible-core==X` version THIS distribution declares, or None."""
    return _declared_core_pin(dist_name)


def pin_declaration(dist_name: str = DIST_NAME) -> PinDeclaration:
    """The declaration and why it is or is not checkable."""
    return _pin_declaration(dist_name)


def pin_report(dist_name: str = DIST_NAME) -> dict:
    """Non-raising form. Read ["verdict"], not ["ok"], to report health."""
    return _pin_report(dist_name)


def assert_core_pin(dist_name: str = DIST_NAME) -> None:
    """Report-only form: raise ImportError when the core that will run is not the
    one declared. Installs nothing. Used by the CLI and the tests."""
    _assert_core_pin(dist_name)


def enforce_core_pin(dist_name: str = DIST_NAME, *, stream=None) -> None:
    """Import-time form: check, and repair the environment instead of only
    reporting it. Raises ImportError only when it is still wrong afterwards.

    The sys.modules snapshot taken at the top of this module is passed through:
    it is the only evidence that no caller is already holding a core object, and
    it stops being available the moment anything imports the core.
    """
    _enforce_core_pin(dist_name, core_preimported=_CORE_PREIMPORTED, stream=stream)
