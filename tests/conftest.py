import os
import random
import sys
from pathlib import Path

import pytest

# GUARDED, and not for tidiness. The user-path e2e files are collected in an
# environment that deliberately does NOT have this package installed - they
# build their own venv and install from the index, because a second copy on the
# runner's path would mean their assertions read the wrong one. A hard import
# here made collection fail outright (exit 4, "No module named
# invisible_playwright") on both CI runners while passing locally, where a
# checkout is always importable.
#
# The fixtures below still need it; they fail loudly at USE time instead, which
# is a clear message about one test rather than a collection error covering the
# whole run.
try:
    from invisible_playwright._fpforge import generate_profile
    from invisible_playwright.constants import BINARY_ENTRY_REL
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only in the e2e-only environment
    generate_profile = None
    BINARY_ENTRY_REL = None
    _IMPORT_ERROR = exc


def _require_package():
    if _IMPORT_ERROR is not None:
        raise pytest.UsageError(
            f"this fixture needs invisible_playwright importable, and it is "
            f"not: {_IMPORT_ERROR}. That is expected only when running the "
            f"user-path e2e files, which install the package into their own "
            f"venv; any other test needs `pip install -e .`")


@pytest.fixture
def deterministic_rng():
    """Seeded RNG for reproducible tests."""
    return random.Random(42)


@pytest.fixture
def sample_profile():
    """A Profile generated from seed=42 for reuse across tests."""
    return generate_profile(seed=42)


@pytest.fixture(scope="session")
def firefox_binary():
    """Locate the patched Firefox binary for E2E tests, or skip cleanly.

    Single source of truth for every E2E test (previously each test file had its
    own copy - and three of them silently ignored INVPW_BINARY_PATH, so they kept
    testing whatever was in the cache even when you pointed the suite at a
    specific build: a false-confidence trap). Lookup order:

      1. ``INVPW_BINARY_PATH`` env var - point the whole suite at a local build
         or a freshly-extracted release (this is how the full-suite gate runs).
      2. Cached binary under ``cache_dir_for_version()`` (post ``fetch``).
      3. Skip - we never trigger an implicit multi-hundred-MB network download
         inside a test run.
    """
    env_path = os.environ.get("INVPW_BINARY_PATH")
    if env_path:
        if Path(env_path).exists():
            return env_path
        pytest.skip(f"INVPW_BINARY_PATH={env_path!r} does not exist")

    if sys.platform not in BINARY_ENTRY_REL:
        pytest.skip(f"unsupported platform: {sys.platform}")
    from invisible_playwright.download import cache_dir_for_version
    entry = cache_dir_for_version() / BINARY_ENTRY_REL[sys.platform]
    if not entry.exists():
        pytest.skip(
            "patched Firefox binary not cached and INVPW_BINARY_PATH unset; "
            "set INVPW_BINARY_PATH=<firefox binary> or run `invisible-playwright fetch`"
        )
    return str(entry)
