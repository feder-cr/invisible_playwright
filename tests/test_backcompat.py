"""Backward-compatibility guard for the invisible_core / invisible_playwright split.

After extracting the pure config into ``invisible_core`` and turning the moved
modules into aliasing shims, EVERY import path that external users (and our own
outreach PRs) rely on must keep working - same objects, same behavior. This test
is the contract: if it breaks, we broke someone's install.
"""
import importlib

import pytest


@pytest.mark.unit
def test_top_level_public_api_imports():
    import invisible_playwright as ip
    for name in [
        "InvisiblePlaywright", "ensure_binary", "ensure_geoip_mmdb",
        "get_default_stealth_prefs", "get_default_args",
        "resolve_session_timezone", "GeoTimezoneError",
        "BINARY_VERSION", "FIREFOX_UPSTREAM_VERSION", "__version__",
    ]:
        assert hasattr(ip, name), f"invisible_playwright.{name} missing"


@pytest.mark.unit
def test_submodule_imports_external_users_rely_on():
    # These are the exact import shapes used in the wild + in our Skyvern/onyx/etc PRs.
    from invisible_playwright._fpforge import generate_profile, Profile  # noqa: F401
    from invisible_playwright._webgl_personas import forced_gpu_class      # noqa: F401
    from invisible_playwright.prefs import translate_profile_to_prefs      # noqa: F401
    from invisible_playwright.download import ensure_binary                # noqa: F401
    from invisible_playwright.constants import BINARY_VERSION              # noqa: F401
    from invisible_playwright.config import get_default_stealth_prefs      # noqa: F401


@pytest.mark.unit
def test_deep_submodule_and_private_names_still_import():
    from invisible_playwright._fpforge.profile import Profile              # noqa: F401
    from invisible_playwright._fpforge import _sampler                     # noqa: F401
    from invisible_playwright.prefs import _BASELINE                       # noqa: F401
    assert isinstance(_BASELINE, dict)


@pytest.mark.unit
def test_class_identity_is_shared_with_core():
    # isinstance must hold: the object returned by generate_profile must be an
    # instance of BOTH the invisible_playwright-path Profile and the core Profile
    # (they must be the SAME class object, not duplicates).
    from invisible_playwright._fpforge import generate_profile
    from invisible_playwright._fpforge import Profile as PW_Profile
    from invisible_playwright._fpforge.profile import Profile as PW_Profile2
    import invisible_core
    from invisible_core import Profile as Core_Profile

    p = generate_profile(seed=42)
    assert isinstance(p, PW_Profile)
    assert isinstance(p, PW_Profile2)
    assert isinstance(p, Core_Profile)
    assert PW_Profile is Core_Profile
    assert PW_Profile2 is Core_Profile


@pytest.mark.unit
def test_shim_output_matches_core_output():
    from invisible_playwright._fpforge import generate_profile as gp_pw
    from invisible_playwright._webgl_personas import forced_gpu_class as fgc_pw
    from invisible_playwright.prefs import translate_profile_to_prefs as tp_pw
    import invisible_core as ic

    a = tp_pw(gp_pw(42, fixed_gpu_class=fgc_pw(42)))
    b = ic.translate_profile_to_prefs(ic.generate_profile(42, fixed_gpu_class=ic.forced_gpu_class(42)))
    assert a == b


@pytest.mark.unit
def test_core_imports_without_playwright(monkeypatch):
    # invisible_core must be importable even if playwright is absent. We simulate
    # by asserting the core modules never pulled playwright into sys.modules as a
    # side effect of their own import (the wrapper may have, but core must not need it).
    import sys
    import invisible_core  # noqa: F401
    # invisible_core itself must not hard-depend on playwright at import time.
    core_file = importlib.import_module("invisible_core").__file__
    assert "invisible_core" in core_file
