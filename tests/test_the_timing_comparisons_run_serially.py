"""A test that compares two measured durations (a gather next to a gateway and the same gather behind one 250 ms away) runs alone, in the serial pass.

Run 43353896238 of PR 486 (Linux e2e, four workers): 765 passed and one failed, ``test_a_gather_after_the_page_loaded_through_the_gateway_costs_only_the_road_to_the_server``:
``assert (350 - 84) <= 100``, the host candidate of the far gather came 266 ms after the one of the near gather. Nothing in the engine waited; two browsers
launched one after the other on a runner that was also running three other workers took different times. A verdict on the difference of two wall-clock
measures belongs to the group the e2e run already has for it, ``wall_clock`` (``invisible_core.testing.e2e.passes``: everything else first at the width asked,
then these alone on the serial path). Found by reading the file's AST: a test whose assertions name both ``near`` and ``far`` timings.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from test_the_consecutive_port_tests_run_serially import _marks

pytestmark = pytest.mark.unit

FILE = Path(__file__).resolve().parent / "test_webrtc_realness.py"


def _compares_near_and_far(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            if {"near", "far"} <= names:
                return True
    return False


def unserial(source: str) -> list:
    tree = ast.parse(source)
    return [n.name for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_") and _compares_near_and_far(n) and "wall_clock" not in _marks(n)]


def test_every_test_that_compares_a_near_and_a_far_duration_runs_in_the_serial_group():
    assert unserial(FILE.read_text(encoding="utf-8")) == []


def test_the_two_known_tests_are_found():
    tree = ast.parse(FILE.read_text(encoding="utf-8"))
    found = sorted(n.name for n in tree.body if isinstance(n, ast.FunctionDef) and _compares_near_and_far(n))
    assert found == ["test_a_gather_after_the_page_loaded_through_the_gateway_costs_only_the_road_to_the_server",
                     "test_the_first_candidate_of_a_gather_does_not_wait_for_a_gateway_that_is_far_away"], found


def test_a_comparison_without_the_marker_is_refused():
    body = ["def test_x():", "    assert far['host'] - near['host'] <= 100"]
    assert unserial("\n".join(body)) == ["test_x"]
    assert unserial("\n".join(["@pytest.mark.wall_clock"] + body)) == []
