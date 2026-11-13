"""scripts/ci_lab_geoip.py verify: the leg ran with the pinned database and the core never went past it to GitHub; and `build` says where the record goes."""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("ci_lab_geoip_verify_under_test", ROOT / "scripts" / "ci_lab_geoip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = _load()
PIN = ROOT / "ci" / "geoip.json"


def _proof(tmp_path, name, **fields):
    data = {"env": "/db/geoip-aio-all.mmdb", "exists": True, "sha256": lab.load_pin(PIN)["mmdb_sha256"]}
    data.update(fields)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_build_exports_the_variable_and_the_place_of_the_record_of_use(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    assert lab.main(["build", "--pin", str(PIN), "--dest", str(tmp_path / "dest")]) == 0
    path = (tmp_path / "dest" / lab.FILE_NAME).resolve()
    used = path.parent / "geoip-used.json"
    assert env_file.read_text(encoding="utf-8") == "STEALTHFOX_GEOIP_MMDB=%s\nGEOIP_USED_OUT=%s\n" % (path, used)
    assert "GEOIP_USED_OUT=%s" % used in capsys.readouterr().out


def test_a_leg_with_the_pinned_database_and_no_question_to_github_verifies(tmp_path):
    evidence = _proof(tmp_path, "evidence.json")
    used = _proof(tmp_path, "used.json", asked_github=0)
    assert lab.verify(lab.load_pin(PIN), evidence, used) == []
    assert lab.main(["verify", "--pin", str(PIN), "--evidence", str(evidence), "--used", str(used)]) == 0


@pytest.mark.parametrize("fields, said", [({"env": "", "exists": False, "sha256": None}, "was not set"),
                                          ({"exists": False, "sha256": None}, "is not a file"),
                                          ({"sha256": "d" * 64}, "the pin says")])
def test_evidence_of_a_leg_without_the_database_does_not_verify(tmp_path, fields, said):
    problems = lab.verify(lab.load_pin(PIN), _proof(tmp_path, "evidence.json", **fields))
    assert len(problems) == 1 and said in problems[0], problems


def test_a_core_that_went_past_the_variable_to_github_does_not_verify(tmp_path):
    problems = lab.verify(lab.load_pin(PIN), _proof(tmp_path, "evidence.json"), _proof(tmp_path, "used.json", asked_github=3))
    assert len(problems) == 1 and "asked GitHub" in problems[0] and "3 time" in problems[0]


def test_a_variable_gone_by_the_end_of_the_run_does_not_verify(tmp_path):
    used = _proof(tmp_path, "used.json", env="", exists=False, sha256=None, asked_github=0)
    problems = lab.verify(lab.load_pin(PIN), _proof(tmp_path, "evidence.json"), used)
    assert len(problems) == 1 and "record of use" in problems[0]


def test_a_record_that_is_not_there_does_not_verify(tmp_path):
    problems = lab.verify(lab.load_pin(PIN), _proof(tmp_path, "evidence.json"), tmp_path / "none.json")
    assert len(problems) == 1 and "cannot be read" in problems[0]
    assert lab.main(["verify", "--pin", str(PIN), "--evidence", str(tmp_path / "none.json")]) == 1
