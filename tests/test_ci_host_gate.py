"""The host gate's comparison (scripts/ci_host_gate.py), on synthetic dumps.

Each property is shown failing on the input it exists for: a value that moves
with the host, a value that does not depend on the identity but is not
retail's, an exception that hides nothing any more. And a value that depends on
the identity is held to host-independence only, which is what lets the gate run
without a list of keys.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci_host_gate as gate  # noqa: E402

LEGS = ("linux-x86_64", "win-x86_64")


def write(tmp_path, items_by_leg_seed, retail):
    dumps = tmp_path / "dumps"
    ref = tmp_path / "ref"
    dumps.mkdir(parents=True)
    ref.mkdir(parents=True)
    for dpr in gate.DPRS:
        (ref / ("dpr%s.json" % dpr)).write_text(json.dumps({"items": retail}), encoding="utf-8")
        for leg in LEGS:
            for seed in gate.SEEDS:
                items = items_by_leg_seed(leg, seed)
                (dumps / ("%s_dpr%s_seed%d.json" % (leg, dpr, seed))).write_text(json.dumps(
                    {"leg": leg, "dpr": dpr, "seed": seed,
                     "result": {"format": gate.FORMAT, "items": items, "noise": {}}}), encoding="utf-8")
    return dumps, ref


def run(tmp_path, items, retail, exceptions=None):
    dumps, ref = write(tmp_path, items, retail)
    return gate.compare(dumps, ref, exceptions or {})


def test_identical_hosts_and_retail_pass(tmp_path):
    assert run(tmp_path, lambda leg, seed: {"surface.gpu.limits": 8}, {"surface.gpu.limits": 8}) == []


def test_a_value_that_moves_with_the_host_fails(tmp_path):
    items = lambda leg, seed: {"surface.gpu.isFallbackAdapter": leg.startswith("linux")}
    failures = run(tmp_path, items, {"surface.gpu.isFallbackAdapter": False})
    assert any(f.startswith("host  surface.gpu.isFallbackAdapter") for f in failures)


def test_an_identity_value_needs_only_host_independence(tmp_path):
    # the screen width changes with the seed; retail has its own, and that is fine
    items = lambda leg, seed: {"surface.screen.width": 1000 + seed}
    assert run(tmp_path, items, {"surface.screen.width": 2560}) == []


def test_a_value_that_does_not_depend_on_the_identity_must_be_retails(tmp_path):
    items = lambda leg, seed: {"surface.media.webrtc.vp9": [True, True, False]}
    failures = run(tmp_path, items, {"surface.media.webrtc.vp9": [True, True, True]})
    assert len([f for f in failures if f.startswith("retail surface.media.webrtc.vp9")]) == len(LEGS) * len(gate.DPRS)


def test_noise_and_network_are_excused_known_defects_must_still_be_seen(tmp_path):
    items = lambda leg, seed: {"surface.timing.load": seed * 7 + len(leg), "surface.intl.zone": "UTC",
                               "text.canvas.H": "linux" if leg.startswith("linux") else "win"}
    exceptions = {
        "noise": [{"keys": ["surface.timing.*"], "why": "varies between two retail launches"}],
        "network": [{"keys": ["surface.intl.zone"], "why": "from the egress IP"}],
        "known_defects": [
            {"keys": ["text.*"], "legs": ["linux-x86_64"], "why": "phase 4"},
            {"keys": ["surface.gone.*"], "why": "fixed already"},
        ],
    }
    failures = run(tmp_path, items, {"surface.intl.zone": "Asia/Bangkok", "text.canvas.H": "win"}, exceptions)
    assert failures == ["known defect no longer seen, remove it from exceptions.json: "
                        "['surface.gone.*'] (fixed already)"]


def test_a_known_defect_is_scoped_to_its_legs(tmp_path):
    items = lambda leg, seed: {"text.canvas.H": "x" if leg == "win-x86_64" else "y"}
    exceptions = {"known_defects": [{"keys": ["text.*"], "legs": ["linux-arm64"], "why": "phase 4"}]}
    failures = run(tmp_path, items, {"text.canvas.H": "x"}, exceptions)
    assert any(f.startswith("host  text.canvas.H") for f in failures)


def test_an_image_difference_is_told_in_bytes():
    img = lambda data: {"w": 2, "h": 1, "enc": "alpha8", "data": base64.b64encode(bytes(data)).decode()}
    assert gate.describe(img([0, 10]), img([0, 13])) == "image: 1 of 2 bytes differ, max delta 3"


def test_an_exception_without_a_reason_is_refused(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"noise": [{"keys": ["a.*"]}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        gate.load_exceptions(path)


def test_an_object_is_compared_field_by_field(tmp_path):
    # hardwareConcurrency is the identity's, appName is Firefox's: one wrong
    # field fails alone, and the identity's field does not drag the object down
    items = lambda leg, seed: {"surface.navigator": {"hardwareConcurrency": 4 + seed, "appName": "Gecko"}}
    failures = run(tmp_path, items, {"surface.navigator": {"hardwareConcurrency": 24, "appName": "Netscape"}})
    assert [f.split()[1] for f in failures] == ["surface.navigator.appName"] * (len(LEGS) * len(gate.DPRS))


def test_a_declared_identity_value_that_happens_to_repeat_is_not_held_to_retail(tmp_path):
    # 16 cores for every seed visited, 24 on the retail machine
    items = lambda leg, seed: {"surface.worker": {"hc": 16}}
    assert run(tmp_path / "plain", items, {"surface.worker": {"hc": 24}}) != []
    exceptions = {"identity": [{"keys": ["surface.worker.hc"], "why": "the declared core count"}]}
    assert run(tmp_path / "declared", items, {"surface.worker": {"hc": 24}}, exceptions) == []


def test_a_declared_identity_value_is_still_held_to_host_independence(tmp_path):
    items = lambda leg, seed: {"surface.worker": {"hc": 16 if leg.startswith("win") else 8}}
    exceptions = {"identity": [{"keys": ["surface.worker.hc"], "why": "the declared core count"}]}
    failures = run(tmp_path, items, {"surface.worker": {"hc": 16}}, exceptions)
    assert any(f.startswith("host  surface.worker.hc") for f in failures)


def test_an_unknown_kind_is_refused(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"whatever": [{"keys": ["a"], "why": "b"}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        gate.load_exceptions(path)
