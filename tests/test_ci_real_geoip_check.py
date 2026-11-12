"""scripts/ci_real_geoip_check.py, the three checks on the REAL database that only the release runs: here against databases made on the spot."""
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load("ci_real_geoip_check")
lab = check.lab


def _database(tmp_path, name, networks=None):
    """A database made like the lab one, from `networks` (the table of the script when None)."""
    old = dict(lab.NETWORKS)
    try:
        if networks is not None:
            lab.NETWORKS.clear()
            lab.NETWORKS.update(networks)
        path = tmp_path / name
        path.write_bytes(lab.build_bytes())
        return path
    finally:
        lab.NETWORKS.clear()
        lab.NETWORKS.update(old)


def test_the_exit_of_a_network_is_the_dot_five_of_its_slash_24():
    assert check.exit_of("1.178.10.0/24") == "1.178.10.5"


def test_a_database_that_agrees_passes_the_comparison_and_the_zones(tmp_path, capsys):
    real = _database(tmp_path, "real.mmdb")
    ours = _database(tmp_path, "lab.mmdb")
    assert check.check_lab_agrees(real, ours) == []
    assert check.check_zones(real) == []
    assert "distinct zones" in capsys.readouterr().out


def test_a_zone_that_differs_is_said_with_the_network(tmp_path):
    changed = dict(lab.NETWORKS)
    changed["1.0.16.0/24"] = ("JP", "Asia/Seoul", 35.0, 139.0)
    real = _database(tmp_path, "real.mmdb", changed)
    ours = _database(tmp_path, "lab.mmdb")
    problems = check.check_lab_agrees(real, ours)
    assert len(problems) == 1 and "1.0.16.0/24" in problems[0] and "Asia/Seoul" in problems[0]


def test_a_network_the_real_database_does_not_have_is_said(tmp_path):
    real = _database(tmp_path, "real.mmdb", {"1.178.10.0/24": lab.NETWORKS["1.178.10.0/24"]})
    ours = _database(tmp_path, "lab.mmdb")
    problems = check.check_lab_agrees(real, ours)
    assert len(problems) == len(lab.NETWORKS) - 1


def test_a_zone_the_runner_does_not_know_is_a_finding(tmp_path):
    odd = dict(lab.NETWORKS)
    odd["1.6.0.0/24"] = ("IN", "Mars/Olympus_Mons", 1.0, 1.0)
    real = _database(tmp_path, "real.mmdb", odd)
    problems = check.check_zones(real)
    assert len(problems) == 1 and "Mars/Olympus_Mons" in problems[0]


def test_a_database_that_does_not_open_or_has_no_country_is_a_finding(tmp_path):
    broken = tmp_path / "broken.mmdb"
    broken.write_bytes(b"not a database")
    assert check.check_open_and_answer(broken)
    assert check.check_open_and_answer(_database(tmp_path, "lab.mmdb"))  # no 8.8.8.8 in the lab database: no country


def test_the_command_takes_no_argument():
    assert check.main(["--something"]) == 2


def test_the_product_road_is_the_one_without_the_variable_and_with_a_cold_cache(tmp_path, monkeypatch):
    seen = {}

    def fake():
        seen["variable"] = os.environ.get("STEALTHFOX_GEOIP_MMDB")
        seen["cache"] = os.environ.get("INVISIBLE_PLAYWRIGHT_CACHE_DIR")
        return tmp_path / "x.mmdb"

    monkeypatch.setenv("STEALTHFOX_GEOIP_MMDB", "/somewhere")
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", "/before")
    import invisible_core.download as download
    monkeypatch.setattr(download, "ensure_geoip_mmdb", fake)
    assert check.product_download(tmp_path / "cold") == tmp_path / "x.mmdb"
    assert seen == {"variable": None, "cache": str(tmp_path / "cold")}
