"""The Juggler client lives once, in invisible_core.juggler.

Until 0.30.0 this package carried its own copy - about 13,000 lines - and so
did invisible-selenium and invisible-puppeteer, and a fix reached one client
and not the others: five remedies of this package never reached the other two,
and the one guard the selenium copy had never reached this one (decision D85).
A file here with the name of a core module is that copy coming back, whatever
it contains, so the test is on the CLASS - the name - and not on the content.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

import invisible_playwright

pytestmark = pytest.mark.unit

_PKG = pathlib.Path(invisible_playwright.__file__).resolve().parent
_CORE_JUGGLER = pathlib.Path(importlib.import_module("invisible_core.juggler").__file__).resolve().parent


def _core_modules() -> set:
    return {p.name for p in _CORE_JUGGLER.iterdir()
            if p.suffix in (".py", ".js") and p.name != "__init__.py"}


def test_no_module_of_the_core_juggler_is_copied_here():
    mine = {p.name for p in _PKG.rglob("*") if p.suffix in (".py", ".js")
            and "__pycache__" not in p.parts}
    copied = sorted(mine & _core_modules())
    assert not copied, (
        "these files have the name of a module of invisible_core.juggler, the one "
        "copy of the Juggler client every invisible_ wrapper shares: %s. Import it "
        "from the core instead; a fix made here would not reach the other clients."
        % copied)


def test_the_core_carries_the_modules_this_package_imports():
    """The other half: the names this package needs are where it looks."""
    for name in ("connection", "protocol", "lifecycle", "injected", "actions",
                 "keyboard", "keylayout", "_profile", "_behaviour", "_motion",
                 "_pacing"):
        importlib.import_module("invisible_core.juggler." + name)
    assert (_CORE_JUGGLER / "injected.js").is_file()
