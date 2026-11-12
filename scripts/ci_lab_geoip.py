"""The GeoIP database of a CI leg: written by this file, the same bytes on every run, and the proof that the leg launched with it.

    python scripts/ci_lab_geoip.py build    [--pin ci/geoip.json] [--dest DIR]     # writes DIR/geoip-aio-all.mmdb, exports STEALTHFOX_GEOIP_MMDB
    python scripts/ci_lab_geoip.py evidence [--out result/geoip.json]               # what the environment of THIS command says
    python scripts/ci_lab_geoip.py verify   [--pin ci/geoip.json] --evidence FILE [--used FILE]   # exit 1: the leg did not run with the pinned database

Why this exists (run 43211837926, the root legs of the realness suite): without ``STEALTHFOX_GEOIP_MMDB`` the core asked GitHub which GeoIP
database is the latest on every call, from the python of the test, and a resolver trap counted those names (github.com, api.github.com) as the
browser's. A CI leg reaches no site for its data and uses no licensed one: the database is made here, from the table below, with nothing but the
standard library. It holds the networks the tests look up (the six exits of tests/_realness_support.py EXITS) and the fields the core reads
(``country.iso_code``, ``location.latitude`` / ``longitude`` / ``time_zone``); an address that is not in it (the documentation ranges the lab uses,
loopback, any IPv6) is "not in the database", as in the real one. ``ci/geoip.json`` holds the sha256 of the file: ``build`` refuses to go on when
what it wrote is not that, ``evidence`` says which file a launch ran with, and ``scripts/ci_verdict.py judge --geoip`` turns a leg that ran with
anything else red. The real database is looked at only by the release (scripts/ci_real_geoip_check.py).

The format is MaxMind DB 2.0 (https://maxmind.github.io/MaxMind-DB/): a binary search tree of node_count nodes of two records of 24 bits, 16 zero
bytes, the data section, the marker and the metadata map. The tree is an ip_version 6 tree with the IPv4 space under ::/96, as in the real database,
so that a lookup of an IPv6 address answers "not found" instead of raising. The metadata integers have the types libmaxminddb checks.

``build`` writes ``STEALTHFOX_GEOIP_MMDB=<path>`` to ``$GITHUB_ENV`` (every later step of the job sees it, ``sudo -E`` carries it to a root leg) and to
stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import struct
import sys
from pathlib import Path

ENV = "STEALTHFOX_GEOIP_MMDB"
FILE_NAME = "geoip-aio-all.mmdb"
MARKER = b"\xab\xcd\xefMaxMind.com"
RECORD_BITS = 24
BUILD_EPOCH = 1793790075  # fixed: the same bytes on every run

#: The six exits of tests/_realness_support.py EXITS (each test declares the .5 of its /24) and what the core reads of them: country, IANA zone,
#: and the coordinates of the city centre (public geography). The tests ask the database for their truth at test time, so any coherent values do.
NETWORKS = {
    "1.178.10.0/24": ("DE", "Europe/Berlin", 50.1109, 8.6821),          # germany
    "1.0.16.0/24": ("JP", "Asia/Tokyo", 35.6895, 139.6917),             # japan
    "1.6.0.0/24": ("IN", "Asia/Kolkata", 12.9716, 77.5946),             # india
    "1.0.4.0/24": ("AU", "Australia/Melbourne", -37.8136, 144.9631),    # australia
    "1.178.32.0/24": ("BR", "America/Sao_Paulo", -23.5505, -46.6333),   # brazil
    "1.32.239.0/24": ("US", "America/Los_Angeles", 34.0522, -118.2437), # united_states
}


class Refused(Exception):
    pass


class U16(int):
    """A uint16 of the metadata: not the smallest type that holds it, libmaxminddb checks the type of each key."""


class U32(int):
    pass


class U64(int):
    pass


def _control(kind: int, size: int) -> bytes:
    """The control byte(s) of a value of type `kind` (1..15) and `size`."""
    if kind <= 7:
        first, ext = kind << 5, b""
    else:
        first, ext = 0, bytes([kind - 7])
    if size < 29:
        return bytes([first | size]) + ext
    if size < 285:
        return bytes([first | 29]) + ext + bytes([size - 29])
    if size < 65821:
        return bytes([first | 30]) + ext + struct.pack(">H", size - 285)
    return bytes([first | 31]) + ext + struct.pack(">I", size - 65821)[1:]


def _unsigned(kind: int, value: int, width: int) -> bytes:
    raw = value.to_bytes(width, "big").lstrip(bytes([0]))  # MaxMind DB integers drop their leading zero bytes
    return _control(kind, len(raw)) + raw


def encode(value) -> bytes:
    if isinstance(value, U16):
        return _unsigned(5, int(value), 2)
    if isinstance(value, U32):
        return _unsigned(6, int(value), 4)
    if isinstance(value, U64):
        return _unsigned(9, int(value), 8)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _control(2, len(raw)) + raw
    if isinstance(value, float):
        return _control(3, 8) + struct.pack(">d", value)
    if isinstance(value, dict):
        out = _control(7, len(value))
        for key in sorted(value):  # sorted: the same bytes whatever the order of the table
            out += encode(key) + encode(value[key])
        return out
    if isinstance(value, (list, tuple)):
        return _control(11, len(value)) + b"".join(encode(v) for v in value)
    raise TypeError("cannot encode %r" % (value,))


def records() -> dict:
    return {cidr: {"country": {"iso_code": country}, "location": {"latitude": lat, "longitude": lon, "time_zone": zone}}
            for cidr, (country, zone, lat, lon) in NETWORKS.items()}


def build_bytes() -> bytes:
    """The database: the same bytes on every call."""
    data = bytearray()
    offsets: dict = {}
    leaves = []
    table = records()
    for cidr in sorted(table, key=lambda c: (int(ipaddress.ip_network(c).network_address), ipaddress.ip_network(c).prefixlen)):
        blob = encode(table[cidr])
        if blob not in offsets:
            offsets[blob] = len(data)
            data += blob
        leaves.append((ipaddress.ip_network(cidr), offsets[blob]))
    # the trie: nodes are [left, right]; a child is None (no data), ("node", index) or ("data", offset)
    nodes = [[None, None]]
    for net, offset in leaves:
        if net.version != 4:
            raise ValueError("this writer takes IPv4 networks: %s" % net)
        bits = int(net.network_address)
        prefix = 96 + net.prefixlen  # the IPv4 space sits under ::/96
        index = 0
        for depth in range(prefix):
            bit = (bits >> (127 - depth)) & 1
            if depth == prefix - 1:
                nodes[index][bit] = ("data", offset)
            else:
                child = nodes[index][bit]
                if child is None:
                    nodes.append([None, None])
                    child = ("node", len(nodes) - 1)
                    nodes[index][bit] = child
                elif child[0] != "node":
                    raise ValueError("%s sits inside a network that already has data" % net)
                index = child[1]
    count = len(nodes)

    def record(child) -> int:
        if child is None:
            return count
        return child[1] if child[0] == "node" else count + 16 + child[1]

    tree = bytearray()
    for left, right in nodes:
        for child in (left, right):
            value = record(child)
            if value >= 1 << RECORD_BITS:
                raise ValueError("a record does not fit in %d bits" % RECORD_BITS)
            tree += value.to_bytes(RECORD_BITS // 8, "big")
    metadata = {
        "binary_format_major_version": U16(2),
        "binary_format_minor_version": U16(0),
        "build_epoch": U64(BUILD_EPOCH),
        "database_type": "GeoIP2-City",
        "description": {"en": "invisible CI lab database: the networks the tests look up"},
        "ip_version": U16(6),
        "languages": ["en"],
        "node_count": U32(count),
        "record_size": U16(RECORD_BITS),
    }
    return bytes(tree) + bytes(16) + bytes(data) + MARKER + encode(metadata)


def sha256_of_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_pin(path) -> dict:
    try:
        pin = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("the pin %s cannot be read: %s" % (path, exc))
    sha = pin.get("mmdb_sha256") if isinstance(pin, dict) else None
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise Refused("the pin %s has no mmdb_sha256 that is a sha256" % path)
    return pin


def build(pin: dict, dest: Path) -> Path:
    """Write the database to DEST and check it against the pin: a writer that no longer makes the pinned bytes is red."""
    blob = build_bytes()
    got = sha256_of_bytes(blob)
    if got != pin["mmdb_sha256"]:
        raise Refused("the lab database made by scripts/ci_lab_geoip.py has sha256 %s, the pin (ci/geoip.json) says %s: change both in one commit"
                      % (got, pin["mmdb_sha256"]))
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / FILE_NAME
    target.write_bytes(blob)
    return target


def evidence(environ=None) -> dict:
    """What the environment of this command says about the database: the value of the variable, whether it is a file, its sha256."""
    environ = os.environ if environ is None else environ
    value = environ.get(ENV, "")
    out = {"env": value, "exists": False, "sha256": None}
    if value and os.path.isfile(value):
        out["exists"] = True
        out["sha256"] = sha256_of(value)
    return out


def verify(pin: dict, evidence_file, used_file=None) -> list:
    """The reasons a leg did not run with the pinned database ([] = it did). The same reading as the judge's (scripts/ci_verdict.py), and
    independent of it: a step of the workflow runs this, so that a judge that does not know the evidence yet (the first merge to main ends that)
    is not the only thing standing between a leg with no database and a green run.

    ``evidence_file``: what the environment said BEFORE pytest was launched (the ``evidence`` command). ``used_file``: what the process of the
    tests used (tests/geoip_used.py, written when pytest ends): the variable at the end, the sha256 of that file, and how many times the core went
    to GitHub for a database, which has to be none."""
    problems = []

    def read(path, what):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            return data
        except (OSError, ValueError) as exc:
            problems.append("the %s %s cannot be read: %s" % (what, path, exc))
            return None

    for what, path in (("evidence", evidence_file), ("record of use", used_file)):
        if path is None:
            continue
        data = read(path, what)
        if data is None:
            continue
        if not data.get("env"):
            problems.append("the %s says STEALTHFOX_GEOIP_MMDB was not set" % what)
        elif not data.get("exists") or not data.get("sha256"):
            problems.append("the %s says STEALTHFOX_GEOIP_MMDB=%s is not a file" % (what, data.get("env")))
        elif data["sha256"] != pin["mmdb_sha256"]:
            problems.append("the %s says the database has sha256 %s, the pin says %s" % (what, data["sha256"], pin["mmdb_sha256"]))
        if what == "record of use" and data.get("asked_github"):
            problems.append("the core asked GitHub for a database %s time(s) during the run: it went past STEALTHFOX_GEOIP_MMDB" % data["asked_github"])
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--pin", default="ci/geoip.json")
    b.add_argument("--dest", default=None, help="default: $RUNNER_TEMP/geoip, or ./.geoip")
    e = sub.add_parser("evidence")
    e.add_argument("--out", default=None)
    v = sub.add_parser("verify")
    v.add_argument("--pin", default="ci/geoip.json")
    v.add_argument("--evidence", required=True)
    v.add_argument("--used", default=None)
    ns = ap.parse_args(argv)
    if ns.cmd == "build":
        try:
            dest = Path(ns.dest or os.path.join(os.environ.get("RUNNER_TEMP", "."), "geoip" if os.environ.get("RUNNER_TEMP") else ".geoip"))
            path = build(load_pin(ns.pin), dest.resolve())
        except Refused as exc:
            print("ci_lab_geoip: %s" % exc, file=sys.stderr)
            return 1
        # Where the process of the tests writes what the core did about the database (tests/geoip_used.py, read by `verify`).
        used = path.parent / "geoip-used.json"
        print("%s=%s" % (ENV, path))
        print("GEOIP_USED_OUT=%s" % used)
        env_file = os.environ.get("GITHUB_ENV")
        if env_file:
            with open(env_file, "a", encoding="utf-8") as fh:
                fh.write("%s=%s\n" % (ENV, path))
                fh.write("GEOIP_USED_OUT=%s\n" % used)
        return 0
    if ns.cmd == "verify":
        try:
            problems = verify(load_pin(ns.pin), ns.evidence, ns.used)
        except Refused as exc:
            print("ci_lab_geoip: %s" % exc, file=sys.stderr)
            return 1
        for problem in problems:
            print("ci_lab_geoip: FAIL: %s" % problem, file=sys.stderr)
        print("the leg ran with the pinned GeoIP database" if not problems else "%d problem(s)" % len(problems))
        return 1 if problems else 0
    data = evidence()
    text = json.dumps(data, indent=1)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
