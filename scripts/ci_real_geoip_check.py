"""The three checks on the REAL GeoIP database: the only place a CI run touches it, and only in the release (publish.yml, job `real-geoip-database`).

The legs of the gates and of the pull requests launch with the database made by scripts/ci_lab_geoip.py (a few networks, the same bytes every run, no
download). What it cannot prove is proved here, once per release, against the database a user gets:

  1. THE PRODUCT'S OWN ROAD. With STEALTHFOX_GEOIP_MMDB unset and a cold cache the core downloads the latest database (``ensure_geoip_mmdb``), the file
     opens with the reader the core uses (MODE_MMAP) and answers for an address.
  2. THE LAB DATABASE AGREES. For the exit of each network of the lab database the real one gives the same country and the same IANA zone: if it does
     not, the lab table is stale (the tests ask their truth of the database at test time, so a stale table does not break them; it says the lab no longer
     looks like the world).
  3. EVERY ZONE IS KNOWN. Each distinct ``location.time_zone`` of the real database is a zone this runner's tz database knows (``ip_to_timezone``
     validates with ZoneInfo: a name the database has and the runner has not is a launch that fails for the user).

    python scripts/ci_real_geoip_check.py            # exit 0: all three hold; 1: one does not (said); 2: wrong use; 3: no database came (the network)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_lab_geoip as lab  # noqa: E402

#: The database did not come: not a finding about it (see main).
EXIT_NO_DATABASE = 3


def exit_of(cidr: str) -> str:
    """The address each test declares in a lab network: the .5 of its /24 (tests/_realness_support.py EXITS)."""
    base = cidr.split("/")[0].rsplit(".", 1)[0]
    return "%s.5" % base


def check_open_and_answer(path: Path) -> list:
    import maxminddb

    try:
        with maxminddb.open_database(str(path), mode=maxminddb.MODE_MMAP) as reader:
            record = reader.get("8.8.8.8")
    except (OSError, ValueError, getattr(maxminddb, "InvalidDatabaseError", ValueError)) as exc:
        return ["the database %s does not open with the reader of the core: %s" % (path, exc)]
    code = ((record or {}).get("country") or {}).get("iso_code")
    return [] if code else ["the database %s has no country for 8.8.8.8: %r" % (path, record)]


def check_lab_agrees(real: Path, lab_path: Path) -> list:
    from invisible_core._geo import GeoTimezoneError, ip_to_country, ip_to_timezone

    problems = []
    for cidr in sorted(lab.NETWORKS):
        ip = exit_of(cidr)
        try:
            ours = (ip_to_country(ip, lab_path), ip_to_timezone(ip, lab_path))
            theirs = (ip_to_country(ip, real), ip_to_timezone(ip, real))
        except GeoTimezoneError as exc:
            problems.append("%s (%s): %s" % (cidr, ip, exc))
            continue
        if ours != theirs:
            problems.append("%s (%s): the lab database says %s, the real one %s" % (cidr, ip, ours, theirs))
    return problems


def zones_of(path: Path) -> set:
    import maxminddb

    zones = set()
    with maxminddb.open_database(str(path), mode=maxminddb.MODE_MMAP_EXT if hasattr(maxminddb, "MODE_MMAP_EXT") else maxminddb.MODE_MMAP) as reader:
        for _network, record in reader:
            zone = (record.get("location") or {}).get("time_zone") if isinstance(record, dict) else None
            if zone:
                zones.add(zone)
    return zones


def check_zones(path: Path) -> list:
    problems = []
    zones = zones_of(path)
    if not zones:
        return ["the database %s has no time_zone at all" % path]
    for zone in sorted(zones):
        try:
            ZoneInfo(zone)
        except Exception as exc:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, OSError: all the same finding
            problems.append("the zone %r of the database is not known to the tz database of this runner: %s" % (zone, exc))
    print("%d distinct zones in %s" % (len(zones), path))
    return problems


def product_download(cache: Path) -> Path:
    """The core's own road: no variable, a cold cache."""
    os.environ.pop(lab.ENV, None)
    os.environ["INVISIBLE_PLAYWRIGHT_CACHE_DIR"] = str(cache)
    from invisible_core.download import ensure_geoip_mmdb

    return Path(ensure_geoip_mmdb())


def main(argv=None) -> int:
    if len(argv if argv is not None else sys.argv[1:]):
        print(__doc__)
        return 2
    problems = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        try:
            real = product_download(td / "cache")
        except Exception as exc:  # noqa: BLE001
            # Exit 3: the database did not come (the network, GitHub, the download), which says nothing about the database. The release
            # waits for a rerun; it is not a defect of the product and not one of the three checks.
            print("1. the product's own road: NO DATABASE (%s: %s): exit 3, run it again" % (type(exc).__name__, exc))
            return EXIT_NO_DATABASE
        print("1. the product's road gave %s: tag %s, %d bytes, sha256 %s" % (real, real.parent.name, real.stat().st_size, lab.sha256_of(real)))
        results = []
        for label, found in (
                ("1. it opens with the reader of the core and answers for an address", check_open_and_answer(real)),
                ("2. the lab database agrees with it on the six exits (country, zone)",
                 check_lab_agrees(real, lab.build(lab.load_pin(Path(__file__).resolve().parents[1] / "ci" / "geoip.json"), td / "lab"))),
                ("3. every time_zone of it is known to the tz database of this runner", check_zones(real))):
            print("%s: %s" % (label, "ok" if not found else "FAILED (%d)" % len(found)))
            problems += found
    for p in problems:
        print("FAIL: %s" % p)
    print("real GeoIP database: %s" % ("all three checks hold" if not problems else "%d problem(s)" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
