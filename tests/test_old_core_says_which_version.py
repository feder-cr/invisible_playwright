"""An import that fails must say WHY, not name a symbol.

Reported by a user minutes after the change that caused it. `_session` took
`IANA_TO_POSIX_TZ` and `tz_env` from the core with a bare module-level import.
Both had existed for about ten minutes. Any environment holding an older core -
and the pin autofix puts older cores back by itself - died on the browser launch
path with:

    ImportError: cannot import name 'IANA_TO_POSIX_TZ' from 'invisible_core'

which tells the reader nothing they can act on: a symbol name, from a package
whose version they did not choose. The pin machinery exists to explain exactly
this mismatch, and a module-level import walks straight past it.

The lesson is not "do not share code with the core". It is that taking a NEW
symbol makes a version requirement, and a version requirement has to fail like
one.

This drives a real interpreter against a real core with the symbols removed,
because the thing under test is what happens at import time - a mocked
ImportError would only prove that the except branch formats a string.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _core_without(symbols, dest: pathlib.Path) -> pathlib.Path:
    """A copy of the installed core with `symbols` stripped from its __init__."""
    import invisible_core

    src = pathlib.Path(invisible_core.__file__).parent
    pkg = dest / "invisible_core"
    shutil.copytree(src, pkg)
    init = pkg / "__init__.py"
    text = init.read_text(encoding="utf-8")
    text = text.replace("from .launch import tz_env, _IANA_TO_POSIX_TZ as IANA_TO_POSIX_TZ,",
                        "from .launch import")
    for name in symbols:
        text = text.replace(f'    "{name}",\n', "")
    init.write_text(text, encoding="utf-8")
    return dest


def test_an_older_core_names_the_version_it_needs(tmp_path):
    root = _core_without(("tz_env", "IANA_TO_POSIX_TZ"), tmp_path / "old")
    code = ("import invisible_playwright\n"
            "print('IMPORTED')\n")
    env = dict(os.environ, PYTHONPATH=str(root), INVISIBLE_CORE_AUTOFIX="off")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, cwd=str(_REPO), timeout=180)

    assert "IMPORTED" not in proc.stdout, (
        "the probe did not actually reach an older core, so it proves nothing")
    message = proc.stdout + proc.stderr
    final = message.strip().splitlines()[-1]

    # The original ImportError is kept INSIDE the message on purpose - it is the
    # detail somebody debugging wants. What must not happen is that it is the
    # WHOLE message, which is what the user was handed.
    assert "needs invisible-core" in final, (
        f"the failure does not state which core version is required: {final[:160]}")
    assert final.index("needs invisible-core") < final.index("cannot import name"), (
        "the symbol error comes before the version requirement, so the first "
        "thing the reader sees is still the thing they cannot act on")
    assert "pip install" in final, (
        "a failure at import time with no remedy is a support ticket")


def test_the_import_is_guarded_at_all(tmp_path):
    """Cheap structural backstop: the probe above is slow and environment
    dependent, and this is the thing that must not be undone."""
    src = (_REPO / "src" / "invisible_playwright" / "_session.py").read_text(encoding="utf-8")
    assert "except ImportError" in src, (
        "_session takes core symbols unguarded again; an older core then fails "
        "with a symbol name instead of a version")
