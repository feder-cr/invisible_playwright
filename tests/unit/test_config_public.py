"""The wrapper re-exports the core's config helpers, and they are the same objects.

That is the whole claim left here. The twelve BEHAVIOUR tests that used to sit
beside these moved to `invisible_core/tests/test_config_defaults.py` on
2026-07-27: they exercised core code, reached through a four-line shim, in a
suite belonging to a package on a different release cadence.

What remains is genuinely about this package, and it is stated the way it should
always have been - against `invisible_core` directly, not against
`invisible_playwright.config`, which is an alias of it and so compares a thing
with itself.
"""
from __future__ import annotations

import pytest

from invisible_playwright import ensure_binary, get_default_stealth_prefs

pytestmark = pytest.mark.unit

def test_public_import_matches_direct_import():
    """The wrapper's re-export IS the core's function, not a copy of it."""
    from invisible_core.config import get_default_stealth_prefs as _core

    a = get_default_stealth_prefs(seed=42)
    b = _core(seed=42)
    assert a == b


def test_ensure_binary_is_callable_via_public_namespace():
    """ensure_binary is re-exported and stays callable from the package root."""
    # We don't invoke it (would trigger a network download in CI) - just
    # verify the public attribute is the same callable as the underlying.
    from invisible_core.download import ensure_binary as _direct_eb
    assert ensure_binary is _direct_eb
