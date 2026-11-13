"""A test that asserts the host ports of a connection are consecutive (N, N+1) runs alone, in the serial pass of the e2e run.

``tests/test_declared_host_network_e2e.py`` reads the ports the browser chose for one connection: the engine draws a pair N, N+1 at random in
49152-65535. With four workers in parallel, another worker's browser or test server can take N+1 between the draw and the bind (the same port space,
the same machine), and the test reads a pair that is not consecutive. The e2e run already has a place for a test that needs the machine alone: the
``wall_clock`` group (``invisible_core.testing.e2e.passes``: everything else first at the width asked, then these on the serial path, after the last
parallel test has ended). The tests that assert consecutive ports carry that marker. Found by reading the file's AST: any test function with an
assertion that something equals another plus one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FILE = Path(__file__).resolve().parent / "test_declared_host_network_e2e.py"


def _marks(fn: ast.FunctionDef) -> set:
    return {d.attr for d in fn.decorator_list if isinstance(d, ast.Attribute) and isinstance(d.value, ast.Attribute) and d.value.attr == "mark"}


def _asserts_a_consecutive_pair(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Add) and isinstance(sub.right, ast.Constant) and sub.right.value == 1:
                    return True
    return False


def unserial(source: str) -> list:
    tree = ast.parse(source)
    return [n.name for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_") and _asserts_a_consecutive_pair(n) and "wall_clock" not in _marks(n)]


def test_every_test_that_asserts_consecutive_ports_runs_in_the_serial_group():
    assert unserial(FILE.read_text(encoding="utf-8")) == []


def test_the_two_known_tests_are_found():
    tree = ast.parse(FILE.read_text(encoding="utf-8"))
    found = sorted(n.name for n in tree.body if isinstance(n, ast.FunctionDef) and _asserts_a_consecutive_pair(n))
    assert found == ["test_the_host_ports_are_those_of_a_windows_machine", "test_two_connections_on_one_page_get_their_own_port_pair"], found


def test_a_consecutive_port_test_without_the_marker_is_refused():
    body = ["def test_x():", "    assert b == a + 1"]
    assert unserial("\n".join(body)) == ["test_x"]
    assert unserial("\n".join(["@pytest.mark.wall_clock"] + body)) == []
