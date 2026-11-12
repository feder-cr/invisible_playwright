"""scripts/ci_lab_geoip.py: the GeoIP database of a CI leg is made by the repository, the same bytes on every run, and a leg says what it ran with."""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("ci_lab_geoip_under_test", ROOT / "scripts" / "ci_lab_geoip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = _load()
EXITS = {"germany": "1.178.10.5", "japan": "1.0.16.5", "india": "1.6.0.5", "australia": "1.0.4.5", "brazil": "1.178.32.5",
         "united_states": "1.32.239.5"}


@pytest.fixture
def database(tmp_path):
    path = tmp_path / lab.FILE_NAME
    path.write_bytes(lab.build_bytes())
    return path


def test_the_same_bytes_on_every_run_and_the_pin_is_their_hash():
    first, second = lab.build_bytes(), lab.build_bytes()
    assert first == second
    pin = lab.load_pin(ROOT / "ci" / "geoip.json")
    assert lab.sha256_of_bytes(first) == pin["mmdb_sha256"], "the table of scripts/ci_lab_geoip.py and ci/geoip.json changed apart"
    assert len(first) == pin["mmdb_bytes"]


def test_the_exits_of_the_realness_suite_are_the_networks_of_the_database():
    support = (ROOT / "tests" / "_realness_support.py").read_text(encoding="utf-8")
    for label, address in EXITS.items():
        assert '"%s": _address(%s)' % (label, ", ".join(address.split("."))) in support, "%s moved in tests/_realness_support.py" % label
        net = ".".join(address.split(".")[:3]) + ".0/24"
        assert net in lab.NETWORKS, "%s (%s) is not a network of the lab database" % (label, address)


@pytest.mark.parametrize("mode", ["MODE_MMAP_EXT", "MODE_MMAP", "MODE_FILE", "MODE_MEMORY"])
def test_every_reader_of_maxminddb_opens_it(database, mode):
    maxminddb = pytest.importorskip("maxminddb")
    if not hasattr(maxminddb, mode):
        pytest.skip("no %s in this maxminddb" % mode)
    with maxminddb.open_database(str(database), mode=getattr(maxminddb, mode)) as reader:
        record = reader.get(EXITS["germany"])
    assert record["country"]["iso_code"] == "DE" and record["location"]["time_zone"] == "Europe/Berlin"


def test_the_core_reads_what_it_reads_from_it(database):
    from invisible_core._geo import ip_to_coordinates, ip_to_country, ip_to_timezone

    for cidr, (country, zone, lat, lon) in lab.NETWORKS.items():
        ip = cidr.split("/")[0].rsplit(".", 1)[0] + ".5"
        assert ip_to_country(ip, database) == country
        assert ip_to_timezone(ip, database) == zone
        assert ip_to_coordinates(ip, database) == (lat, lon)


@pytest.mark.parametrize("address", ["198.51.100.4", "198.51.100.5", "198.51.100.6", "198.51.100.9", "203.0.113.7", "127.0.0.1", "1.178.11.5",
                                     "2001:db8::1", "::1"])
def test_an_address_that_is_not_in_it_is_not_in_the_database_and_never_an_error(database, address):
    from invisible_core._geo import GeoTimezoneError, ip_to_country, ip_to_timezone

    assert ip_to_country(address, database) is None
    with pytest.raises(GeoTimezoneError) as caught:
        ip_to_timezone(address, database)
    assert caught.value.kind == "ip_not_in_db"


def test_a_writer_that_no_longer_makes_the_pinned_bytes_is_refused_and_exports_nothing(tmp_path, monkeypatch, capsys):
    pin = tmp_path / "geoip.json"
    pin.write_text(json.dumps({"mmdb_sha256": "0" * 64}), encoding="utf-8")
    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    code = lab.main(["build", "--pin", str(pin), "--dest", str(tmp_path / "dest")])
    assert code == 1 and not env_file.exists() and not (tmp_path / "dest" / lab.FILE_NAME).exists()
    assert "change both in one commit" in capsys.readouterr().err


def test_a_pin_that_is_not_a_pin_is_refused(tmp_path):
    bad = tmp_path / "geoip.json"
    for text in ("not json", "{}", json.dumps({"mmdb_sha256": "xyz"})):
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(lab.Refused):
            lab.load_pin(bad)


def test_the_evidence_is_what_the_environment_says(tmp_path):
    db = tmp_path / "db.mmdb"
    db.write_bytes(b"x")
    assert lab.evidence({lab.ENV: str(db)})["exists"] is True
    assert lab.evidence({}) == {"env": "", "exists": False, "sha256": None}
    assert lab.evidence({lab.ENV: str(tmp_path / "no")}) == {"env": str(tmp_path / "no"), "exists": False, "sha256": None}


def test_the_evidence_command_writes_the_file_the_judge_reads(tmp_path, monkeypatch):
    out = tmp_path / "result" / "geoip.json"
    monkeypatch.delenv(lab.ENV, raising=False)
    assert lab.main(["evidence", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"env": "", "exists": False, "sha256": None}
