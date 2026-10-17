"""The firefox-N release body points at the README's install block and restates
no install command of its own.

Known bad: until this the body carried `pip install invisible-playwright`, a
second copy of the README's command that nothing kept in step with it.
`scripts/gen_release_notes.py` writes the body of every firefox-N release (the
engine's publish workflow checks this repository out to run it).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location("gen_release_notes",
                                                  ROOT / "scripts" / "gen_release_notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_body_points_at_the_readme_install_block_and_restates_no_command():
    mod = _module()
    body = mod.build_body("firefox-50", "0123456789abcdef", ["A change"], "151.0")
    assert mod.INSTALL_DOC in body
    assert not re.search(r"(?m)^\s*(pip|uv|uvx|pipx)\s", body), body


def test_the_readme_has_the_install_section_the_body_points_at():
    mod = _module()
    anchor = mod.INSTALL_DOC.rsplit("#", 1)[1]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"(?m)^## %s\s*$" % re.escape(anchor.capitalize()), readme), (
        "the README has no section the release body's link lands on")
