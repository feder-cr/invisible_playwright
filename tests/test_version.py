"""Regression tests for issue #24: CLI version reporting.

Two distinct symptoms reported by `i43-j`:
  1. `python -m invisible_playwright --version` errored out (only the
     `version` subcommand worked).
  2. `python -m invisible_playwright version` printed the literal string
     "0.1.0" regardless of the installed version (a stale hardcoded
     `__version__` in __init__.py that nobody had remembered to bump).

These tests pin down both behaviours so the regressions don't sneak back
in via a future copy/paste.
"""
import io
import re
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

import invisible_playwright
from invisible_playwright import __version__, cli


pytestmark = pytest.mark.unit


def test_version_matches_installed_package_metadata():
    """__version__ must come from importlib.metadata, not a hardcoded literal,
    so it can never drift from the pyproject.toml `version` field."""
    from importlib.metadata import version as pkg_version
    assert __version__ == pkg_version("invisible-playwright")


def test_version_is_not_the_stale_010_string():
    """Issue #24 regression: __version__ used to be hardcoded as '0.1.0'
    and never updated. If this ever returns to a literal '0.1.0' the
    package has been published or shipped with stale metadata."""
    assert __version__ != "0.1.0", (
        "__version__ is the stale hardcoded '0.1.0' string - issue #24 has "
        "regressed. Use importlib.metadata to derive it from pyproject.toml."
    )


def test_version_subcommand_prints_real_version():
    """`invisible-playwright version` must print the actual installed version,
    not the old hardcoded '0.1.0'."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["version"])
    assert rc == 0
    out = buf.getvalue()
    assert f"invisible_playwright {__version__}" in out
    # Safety, scoped to the wrapper's own line: other lines legitimately carry
    # version strings we do not control (the core's pip-visible version, which
    # is exactly the number this command exists to expose when it lags).
    wrapper_line = next(l for l in out.splitlines() if l.startswith("invisible_playwright"))
    assert "0.1.0" not in wrapper_line or __version__ == "0.1.0"
    # The engine identity is printed as tag + base version + BuildID + seal
    # digest. That is what makes a core lagging behind the newest binary
    # release visible on any machine (it used to print a bare BINARY_VERSION=,
    # which could not distinguish two builds carrying the same tag).
    assert "invisible_core" in out
    assert "engine" in out and "firefox-" in out
    assert "Firefox " in out
    assert "build " in out
    assert "seal" in out


def test_dash_dash_version_flag_works():
    """Issue #24 reporter: `python -m invisible_playwright --version` used to
    error with 'the following arguments are required: cmd' because there was
    no top-level --version flag, only the `version` subcommand. Now the
    Python convention works too."""
    # argparse's --version action calls sys.exit(0) directly, so use subprocess.
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright", "--version"],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"--version returned {r.returncode}, stderr={r.stderr!r}"
    # argparse may emit on stdout or stderr depending on version
    combined = r.stdout + r.stderr
    assert "invisible_playwright" in combined
    assert __version__ in combined


def test_no_args_prints_help_not_traceback():
    """`python -m invisible_playwright` with no args should be graceful
    (print help, exit non-zero) rather than crashing with a traceback."""
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright"],
        capture_output=True, text=True, timeout=15,
    )
    # Either prints help (rc=2) or shows usage. Must NOT contain a traceback.
    assert "Traceback" not in (r.stdout + r.stderr)
    assert "usage:" in (r.stdout + r.stderr).lower()


def test_dash_V_short_flag_works():
    """Alias `-V` for `--version` (Python convention)."""
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright", "-V"],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0
    assert __version__ in (r.stdout + r.stderr)


def test_version_matches_semver_shape():
    """Sanity: version should look like a semver (digits.digits.digits)
    or a PEP-440 dev marker, not a placeholder string."""
    assert re.match(r"^\d+\.\d+\.\d+", __version__), (
        f"__version__ {__version__!r} doesn't look like a real version"
    )
