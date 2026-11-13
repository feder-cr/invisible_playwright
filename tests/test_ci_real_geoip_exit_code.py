"""scripts/ci_real_geoip_check.py exits 3 when no database came (the network): that says nothing about the database, and the release is run again."""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("ci_real_geoip_exit_under_test", ROOT / "scripts" / "ci_real_geoip_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def test_a_database_that_did_not_come_is_exit_3_and_not_a_finding(monkeypatch, capsys):
    def no_network(cache):
        raise OSError("name resolution failed")

    monkeypatch.setattr(check, "product_download", no_network)
    assert check.main([]) == check.EXIT_NO_DATABASE == 3
    assert "NO DATABASE" in capsys.readouterr().out


def test_a_database_that_came_and_fails_a_check_is_exit_1_and_names_the_check(tmp_path, monkeypatch, capsys):
    lab = check.lab
    broken = tmp_path / "geoip-aio-all.mmdb"
    with monkeypatch.context() as m:
        m.setattr(lab, "FALLBACK", None)           # no record for 8.8.8.8 in the lab database: the first check fails
        broken.write_bytes(lab.build_bytes())
    monkeypatch.setattr(check, "product_download", lambda cache: broken)
    assert check.main([]) == 1
    out = capsys.readouterr().out
    assert "sha256 %s" % lab.sha256_of(broken) in out and "1. it opens with the reader of the core and answers for an address: FAILED" in out
    assert "2. the lab database agrees" in out and "3. every time_zone" in out
