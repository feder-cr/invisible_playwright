"""Public API surface - what users actually import."""
import importlib

import pytest


def test_top_level_import():
    import invisible_playwright as ip
    assert hasattr(ip, "InvisiblePlaywright")
    assert hasattr(ip, "BINARY_VERSION")
    assert hasattr(ip, "FIREFOX_UPSTREAM_VERSION")
    assert hasattr(ip, "__version__")


def test_version_string():
    from invisible_playwright import __version__
    parts = __version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() or p.replace("-", "").replace("rc", "").isdigit()
               or any(c.isdigit() for c in p) for p in parts)


def test_sync_api_module():
    from invisible_playwright.sync_api import InvisiblePlaywright as SyncCls
    from invisible_playwright import InvisiblePlaywright as TopCls
    assert SyncCls is TopCls


def test_async_api_module_importable():
    mod = importlib.import_module("invisible_playwright.async_api")
    assert hasattr(mod, "InvisiblePlaywright")


def test_async_class_is_distinct_from_sync():
    from invisible_playwright import InvisiblePlaywright as Sync
    from invisible_playwright.async_api import InvisiblePlaywright as Async
    assert Sync is not Async


@pytest.mark.parametrize("name", [
    "constants",
    "download",
    "prefs",
    "launcher",
    "cli",
    "_proxy",
    "_fpforge",
])
def test_submodule_importable(name):
    importlib.import_module(f"invisible_playwright.{name}")


def test_dunder_all_is_complete():
    import invisible_playwright as ip
    for name in ip.__all__:
        assert hasattr(ip, name), f"{name} declared in __all__ but missing"
