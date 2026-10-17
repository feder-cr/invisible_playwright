"""The floor's one remedy is spelled by the tool that installed this package.

`_pin.reinstall_command` is the reinstall line the import floor prints when
invisible-core cannot be imported at all, which is why it cannot ask the core's
`installer` module and reads the INSTALLER record itself. Measured 2026-10-17:
the venv the README makes (`uv init` + `uv add`) has no pip in it, and the line
was `pip install --force-reinstall` for everybody.
"""
from __future__ import annotations

import importlib.metadata
import sys

import pytest

from invisible_playwright import _pin


class _Dist:
    def __init__(self, record):
        self._record = record

    def read_text(self, name):
        assert name == "INSTALLER", name
        return self._record


@pytest.mark.parametrize("record", ["uv\n", "UV", " uv "])
def test_an_environment_uv_built_is_told_uv(monkeypatch, record):
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: _Dist(record))
    line = _pin.reinstall_command()
    assert line.startswith("uv pip install --python "), line
    assert line.endswith("--force-reinstall invisible-playwright"), line
    assert "-m pip" not in line, line


@pytest.mark.parametrize("record", ["pip\n", "poetry\n", None])
def test_any_other_environment_is_told_its_own_pip(monkeypatch, record):
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: _Dist(record))
    line = _pin.reinstall_command()
    assert " -m pip install --force-reinstall invisible-playwright" in line, line
    assert not line.startswith("uv "), line


def test_a_package_with_no_record_at_all_is_told_pip(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", missing)
    assert " -m pip install --force-reinstall invisible-playwright" in _pin.reinstall_command()


def test_the_command_names_this_interpreter(monkeypatch):
    """A bare `pip` or `python` is whatever a shell finds first; the line has to
    land in the environment whose import just failed."""
    for record in ("uv", "pip"):
        monkeypatch.setattr(importlib.metadata, "distribution",
                            lambda name, r=record: _Dist(r))
        line = _pin.reinstall_command()
        assert sys.executable in line or f'"{sys.executable}"' in line, line
