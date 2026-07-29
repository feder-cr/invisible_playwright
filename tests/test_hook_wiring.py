"""The pre-push hook has to be a hook, not a file that looks like one.

Measured 2026-07-27: tracked `100644` in all three repos, so git ignored it on
every POSIX clone - it prints "the '.githooks/pre-push' hook was ignored because
it's not set as executable" and exits 0, and the push goes out with none of the
gates behind it having run. It survived because `core.fileMode` is false on
Windows, where this is developed, and because the mode belongs to the INDEX:
chmod-ing the working tree never changed it and never could.

The full write-up lives once, in `invisible_core.testing.assert_hook_is_executable`.
The four-line query is repeated here on purpose rather than imported: importing
it would make this test depend on a core version that is not published yet, and
a cross-package import is not worth paying for four lines. The reasoning is what
must not be duplicated, and it is not.

Asking git rather than the filesystem is the point - `os.access(X_OK)` is
meaningless on Windows and returned True throughout the bug.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_HOOK = ".githooks/pre-push"


def test_the_pre_push_hook_is_tracked_as_executable():
    out = subprocess.run(["git", "-C", str(_REPO), "ls-files", "-s", _HOOK],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return          # an installed copy has no hook; nothing to check
    mode = out.stdout.split()[0]
    assert mode == "100755", (
        f"{_HOOK} is tracked as {mode}, so git silently ignores it on every "
        f"clone where core.fileMode is honoured - every POSIX clone, including "
        f"CI and WSL. Fix with:\n  git update-index --chmod=+x {_HOOK}\n"
        f"and commit. Chmod-ing the working tree does not change it.")
