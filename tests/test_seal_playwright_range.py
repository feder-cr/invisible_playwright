"""The Juggler client range travels in the seal and is enforced at import. L1.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md sections 3.3 (16),
4.12 and 7. Audit: 16-version-divergence-matrix.md L1.

L1 is the default state of a fresh install: the published floor `>=1.40` lets pip
pick 1.61.0, launch and page.goto succeed, and then every context creation dies
on a field an older Juggler's closed-world checkScheme rejects. Enforcing the
tested range at import turns a dead session into one message, and moves the
range into the seal so widening it does not need a wrapper release.

No browser, no network.
"""
from __future__ import annotations

import json

import pytest

from invisible_core.seal import _reset_active_seal_for_tests, load_seal
from invisible_playwright import _engine


@pytest.fixture
def sealed_range(tmp_path, monkeypatch):
    p = tmp_path / "range.seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": "firefox-18", "upstream_version": "151.0",
        "build_id": "20260724001949", "source_commit": "",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": {},
    }, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", str(p))
    _reset_active_seal_for_tests()
    yield load_seal(p)
    _reset_active_seal_for_tests()


def set_reported_version(monkeypatch, value: str) -> None:
    monkeypatch.setattr(_engine, "_pkg_version", lambda _name: value)


def test_client_inside_the_range_is_accepted(sealed_range, monkeypatch):
    for v in ("1.55.0", "1.58.2", "1.61.0"):
        set_reported_version(monkeypatch, v)
        _engine.assert_playwright_range()


def test_client_above_the_range_is_refused(sealed_range, monkeypatch):
    set_reported_version(monkeypatch, "1.62.0")
    with pytest.raises(RuntimeError) as exc:
        _engine.assert_playwright_range()
    msg = str(exc.value)
    assert "1.62.0" in msg and "1.61.0" in msg, msg
    assert "playwright==" in msg.replace(" ", ""), msg


def test_client_below_the_range_is_refused(sealed_range, monkeypatch):
    """The published 0.3.4 floor of >=1.40 is 15 minor releases nobody ever ran
    against any of our binaries."""
    set_reported_version(monkeypatch, "1.40.0")
    with pytest.raises(RuntimeError):
        _engine.assert_playwright_range()


def test_skew_can_be_allowed_explicitly_and_still_warns(sealed_range, monkeypatch):
    set_reported_version(monkeypatch, "1.62.0")
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_SKEW", "allow")
    with pytest.warns(RuntimeWarning):
        _engine.assert_playwright_range()


def test_the_installed_client_satisfies_the_packaged_seal():
    """The range the package ships must accept the client the package pins, or
    every fresh install is dead on arrival, which is L1."""
    _reset_active_seal_for_tests()
    try:
        _engine.assert_playwright_range()
    finally:
        _reset_active_seal_for_tests()
