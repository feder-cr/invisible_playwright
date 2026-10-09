"""Every Juggler protocol name this package writes is one the engine declares.

The mirror (`invisible_core.juggler.protocol`) is regenerated from the shipped
engine, and the core checks its own client against it. The Playwright server
here writes protocol names of its own - `server.py` sends dozens of commands
and listens for events - and nothing compared those with the mirror, while
invisible-selenium and invisible-puppeteer each had this test for their code.
"""
from __future__ import annotations

import pathlib
import re

import pytest

import invisible_playwright
from invisible_core.juggler.protocol import COMMANDS, EVENTS

pytestmark = pytest.mark.unit

_PATTERN = re.compile(r"""["']((?:Browser|Page|Network|Runtime|Heap)\.[a-z]\w*)["']""")


def _unknown_names(root: pathlib.Path) -> dict:
    unknown: dict = {}
    for source in sorted(root.rglob("*.py")):
        # `_pw/` is the vendored Playwright client: it speaks Playwright's
        # protocol, whose method names share these domain prefixes.
        if "_pw" in source.relative_to(root).parts:
            continue
        for name in _PATTERN.findall(source.read_text(encoding="utf-8")):
            if name not in COMMANDS and name not in EVENTS:
                unknown.setdefault(name, []).append(source.name)
    return unknown


def test_every_protocol_name_this_package_writes_is_declared():
    """⛔ A COMMAND THE ENGINE NO LONGER HAS IS FOUND HERE, NOT IN A BROWSER.

    The engine enforces its schema as a closed world: an undeclared command is
    rejected at runtime, on the first call that sends it. When
    `Page.dispatchTrustedInputEvents` left the engine, the mirror lost it and a
    client still sent it, and nothing in the default selection noticed.
    """
    unknown = _unknown_names(pathlib.Path(invisible_playwright.__file__).parent)
    assert not unknown, "not in the protocol mirror: %r" % unknown


def test_the_scan_finds_a_name_the_engine_does_not_have(tmp_path):
    """Its known-bad input: the old command, written back into a send."""
    (tmp_path / "probe.py").write_text(
        'c.send("Page.dispatchTrustedInputEvents", {})\n'
        'c.send("Page.navigate", {})\n', encoding="utf-8")
    assert _unknown_names(tmp_path) == {"Page.dispatchTrustedInputEvents": ["probe.py"]}
