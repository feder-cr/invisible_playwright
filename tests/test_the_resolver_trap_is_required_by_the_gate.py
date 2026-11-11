"""With INVPW_REQUIRE_TRAP=1 (set by the gate on its Linux leg) a resolver trap that cannot run, or whose port 53 cannot
be bound, FAILS the DoH tests with the reason; without it the assertions on the machine's resolver are skipped as they
always were (a developer's machine). The fixture and the "no proxy" test of the e2e file are run here for real, with
the trap forced unavailable, so the old code (which yielded None) fails these tests."""
import importlib
import sys
from pathlib import Path

import pytest

import _trap_policy

pytestmark = pytest.mark.unit


def _e2e():
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    return importlib.import_module("test_doh_through_the_proxy_e2e")


def _fixture_body(module):
    fixture = module.trap
    return getattr(fixture, "_get_wrapped_function", lambda: getattr(fixture, "__wrapped__"))()


def test_the_policy_is_off_unless_the_gate_sets_it():
    assert _trap_policy.failure({}, False, "no root") is None
    assert _trap_policy.failure({"INVPW_REQUIRE_TRAP": "0"}, False, "no root") is None
    assert _trap_policy.failure({"INVPW_REQUIRE_TRAP": ""}, True, bind_error=OSError("busy")) is None


def test_the_policy_names_the_reason_when_the_gate_requires_the_trap():
    env = {"INVPW_REQUIRE_TRAP": "1"}
    assert "needs root" in _trap_policy.failure(env, False, "the resolver trap needs root")
    assert "Address already in use" in _trap_policy.failure(env, True, bind_error=OSError("Address already in use"))
    assert _trap_policy.failure(env, True) is None


def test_the_trap_fixture_fails_when_the_gate_requires_it_and_it_cannot_run(monkeypatch):
    module = _e2e()
    monkeypatch.setenv("INVPW_REQUIRE_TRAP", "1")
    monkeypatch.setattr(module.ResolverTrap, "available", False)
    monkeypatch.setattr(module.ResolverTrap, "reason", "no root here")
    with pytest.raises(pytest.fail.Exception, match="no root here"):
        next(_fixture_body(module)())


def test_the_trap_fixture_fails_when_the_gate_requires_it_and_port_53_is_taken(monkeypatch):
    module = _e2e()
    monkeypatch.setenv("INVPW_REQUIRE_TRAP", "1")
    monkeypatch.setattr(module.ResolverTrap, "available", True)

    def busy(self):
        raise OSError("Address already in use")

    monkeypatch.setattr(module.ResolverTrap, "__enter__", busy)
    with pytest.raises(pytest.fail.Exception, match="Address already in use"):
        next(_fixture_body(module)())


def test_the_trap_fixture_yields_none_without_the_variable(monkeypatch):
    module = _e2e()
    monkeypatch.delenv("INVPW_REQUIRE_TRAP", raising=False)
    monkeypatch.setattr(module.ResolverTrap, "available", False)
    assert next(_fixture_body(module)()) is None


def test_the_no_proxy_test_fails_when_the_gate_requires_the_trap_and_it_cannot_run(monkeypatch):
    module = _e2e()
    monkeypatch.setenv("INVPW_REQUIRE_TRAP", "1")
    monkeypatch.setattr(module.ResolverTrap, "available", False)
    monkeypatch.setattr(module.ResolverTrap, "reason", "no root here")
    test = module.test_a_session_without_a_proxy_resolves_with_the_system_resolver
    with pytest.raises(pytest.fail.Exception, match="no root here"):
        test("unused-binary", None, None)
