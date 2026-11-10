"""NaN, the infinities and -0 are not JSON numbers, so they cross the wire as a tag.

Found by the e2e run with INVPW_WIRE=json: `page.evaluate_handle("() => NaN").json_value()`
passed in process and failed on the strict wire with "$.result.value.n: nan is not JSON". The
server answered `{"n": nan}`, which Python's json writes as the bare word NaN and no other
JSON reader accepts. Playwright's own serializer sends `{"v": "NaN"}`; so must the server.
"""
from __future__ import annotations

import math

import pytest

from invisible_playwright._juggler import wire
from invisible_playwright._juggler._marshal import _serialize

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value, tag", [
    (float("nan"), "NaN"),
    (float("inf"), "Infinity"),
    (float("-inf"), "-Infinity"),
    (-0.0, "-0"),
])
def test_a_non_finite_number_is_a_tag(value, tag):
    assert _serialize(value) == {"v": tag}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -0.0,
                                   [float("nan"), 1.5], {"a": float("inf")}])
def test_what_the_server_serializes_survives_the_strict_wire(value):
    wire.loads(wire.dumps({"result": {"value": _serialize(value)}}))


def test_a_finite_number_stays_a_number():
    assert _serialize(1.5) == {"n": 1.5}
    assert _serialize(0.0) == {"n": 0.0}
    assert _serialize(-3) == {"n": -3}
    assert not math.isnan(_serialize(2.0)["n"])
