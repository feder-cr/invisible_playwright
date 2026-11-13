"""tests/geoip_used.py: what the core did about the GeoIP database during a run, for `scripts/ci_lab_geoip.py verify`."""
import json
import sys
import types

import pytest

import geoip_used

pytestmark = pytest.mark.unit


def _fake_core(monkeypatch):
    module = types.ModuleType("invisible_core._geoip_db")
    module.calls = []
    module._resolve_latest_geoip_tag = lambda: module.calls.append("tag") or "2026.11.04"
    module._download_geoip_tag = lambda tag, attempts=1: module.calls.append("download") or "x"
    package = types.ModuleType("invisible_core")
    package._geoip_db = module
    monkeypatch.setitem(sys.modules, "invisible_core", package)
    monkeypatch.setitem(sys.modules, "invisible_core._geoip_db", module)
    return module


def test_the_recorder_counts_the_questions_to_github_and_gives_the_functions_back(tmp_path, monkeypatch):
    core = _fake_core(monkeypatch)
    original = (core._resolve_latest_geoip_tag, core._download_geoip_tag)
    db = tmp_path / "db.mmdb"
    db.write_bytes(b"x")
    monkeypatch.setenv("STEALTHFOX_GEOIP_MMDB", str(db))
    out = tmp_path / "used" / "geoip-used.json"
    recorder = geoip_used.GeoipUsedRecorder(str(out))
    recorder.start()
    core._resolve_latest_geoip_tag()
    core._download_geoip_tag("2026.11.04")
    recorder.pytest_sessionfinish(None, 0)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["asked_github"] == 2 and data["env"] == str(db) and data["exists"] is True and len(data["sha256"]) == 64
    assert core.calls == ["tag", "download"], "the real functions were called through"
    assert (core._resolve_latest_geoip_tag, core._download_geoip_tag) == original, "the functions were given back"


def test_a_run_with_no_variable_is_recorded_as_such(tmp_path, monkeypatch):
    _fake_core(monkeypatch)
    monkeypatch.delenv("STEALTHFOX_GEOIP_MMDB", raising=False)
    out = tmp_path / "geoip-used.json"
    recorder = geoip_used.GeoipUsedRecorder(str(out))
    recorder.start()
    recorder.pytest_sessionfinish(None, 0)
    assert json.loads(out.read_text(encoding="utf-8")) == {"env": "", "exists": False, "sha256": None, "asked_github": 0}


def test_the_conftest_registers_it_only_when_a_file_is_named():
    from pathlib import Path

    text = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ.get("GEOIP_USED_OUT")' in text and "GeoipUsedRecorder" in text
