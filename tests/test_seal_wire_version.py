"""The free post-launch check: what the engine says over the wire vs the seal.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md sections 3.3 (15),
4.12 and 4.13 (rows 7 and 8). Audit: 16-version-divergence-matrix.md section 3,
last row of the observables table.

`browser.version` is a cached property from the connection initializer (Juggler
`Browser.getInfo` returns `MOZ_APP_VERSION_DISPLAY`), so it costs zero round
trips and no pref can spoof it. It is the only check that also catches a
hand-edited application.ini, because it comes from the running engine rather
than from a text file next to it.

No browser is launched: the check reads one attribute, so a stub object with a
`version` is the whole input. The manager arm has no equivalent, which section 8
of the spec records as an accepted asymmetry.
"""
from __future__ import annotations

import json

import pytest

from invisible_core.seal import EngineMismatch, _reset_active_seal_for_tests
from invisible_playwright import _engine


class FakeBrowser:
    """What Playwright's Browser exposes here: a `version` string."""

    def __init__(self, version: str):
        self.version = version


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    p = tmp_path / "wire.seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": "firefox-18", "upstream_version": "151.0",
        "build_id": "20260724001949", "source_commit": "",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": {},
    }, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", str(p))
    _reset_active_seal_for_tests()
    yield
    _reset_active_seal_for_tests()


def test_the_sealed_engine_reports_the_sealed_version(sealed):
    _engine.assert_wire_version(FakeBrowser("Firefox/151.0"))
    _engine.assert_wire_version(FakeBrowser("151.0"))


def test_an_engine_reporting_another_version_is_refused_after_launch(sealed):
    """The doctored-ini residue: both ini files can be edited, this value cannot.
    D1/D3 from the other side of the process boundary."""
    with pytest.raises(EngineMismatch) as exc:
        _engine.assert_wire_version(FakeBrowser("Firefox/150.0.1"))
    msg = str(exc.value)
    assert "150.0.1" in msg and "151.0" in msg, msg
    assert "fetch --force" in msg, msg


def test_no_browser_is_not_an_error(sealed):
    """The persistent-context path exposes no Browser, so the check has to be a
    no-op there rather than a crash."""
    _engine.assert_wire_version(None)


def test_an_empty_version_is_not_treated_as_a_match(sealed):
    """A missing value must not be read as agreement; it is simply no evidence,
    and the ini plus provenance checks already ran before launch."""
    _engine.assert_wire_version(FakeBrowser(""))
