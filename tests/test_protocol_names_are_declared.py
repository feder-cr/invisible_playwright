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

#: Names this package writes for an engine that is not in the mirror yet, each behind a fallback that
#: asks for the older command when the engine says it has none (navgc.py: `Heap.minimizeMemory`, from the
#: engine commit 2423b7c242 of the engine's juggler/minimize branch). The mirror is the published core's, which
#: follows the engine: the name leaves this list when the mirror has it, and the last test here says so.
AHEAD_OF_THE_MIRROR = frozenset({"Heap.minimizeMemory"})

_PATTERN = re.compile(r"""["']((?:Browser|Page|Network|Runtime|Heap)\.[a-z]\w*)["']""")


def _unknown_names(root: pathlib.Path, ahead=frozenset()) -> dict:
    unknown: dict = {}
    for source in sorted(root.rglob("*.py")):
        # `_pw/` is the vendored Playwright client: it speaks Playwright's
        # protocol, whose method names share these domain prefixes.
        if "_pw" in source.relative_to(root).parts:
            continue
        for name in _PATTERN.findall(source.read_text(encoding="utf-8")):
            if name not in COMMANDS and name not in EVENTS and name not in ahead:
                unknown.setdefault(name, []).append(source.name)
    return unknown


def test_every_protocol_name_this_package_writes_is_declared():
    """⛔ A COMMAND THE ENGINE NO LONGER HAS IS FOUND HERE, NOT IN A BROWSER.

    The engine enforces its schema as a closed world: an undeclared command is
    rejected at runtime, on the first call that sends it. When
    `Page.dispatchTrustedInputEvents` left the engine, the mirror lost it and a
    client still sent it, and nothing in the default selection noticed.
    """
    unknown = _unknown_names(pathlib.Path(invisible_playwright.__file__).parent, AHEAD_OF_THE_MIRROR)
    assert not unknown, "not in the protocol mirror: %r" % unknown


def test_the_scan_finds_a_name_the_engine_does_not_have(tmp_path):
    """Its known-bad input: the old command, written back into a send."""
    (tmp_path / "probe.py").write_text(
        'c.send("Page.dispatchTrustedInputEvents", {})\n'
        'c.send("Page.navigate", {})\n', encoding="utf-8")
    assert _unknown_names(tmp_path) == {"Page.dispatchTrustedInputEvents": ["probe.py"]}


def test_a_name_ahead_of_the_mirror_is_still_checked_against_every_other_name(tmp_path):
    """Its known-bad input: the list lets ONE name through, not the scan."""
    (tmp_path / "probe.py").write_text(
        'c.send("Heap.minimizeMemory", {})\n'
        'c.send("Page.dispatchTrustedInputEvents", {})\n', encoding="utf-8")
    assert _unknown_names(tmp_path, AHEAD_OF_THE_MIRROR) == {"Page.dispatchTrustedInputEvents": ["probe.py"]}
    assert _unknown_names(tmp_path) == {"Heap.minimizeMemory": ["probe.py"],
                                        "Page.dispatchTrustedInputEvents": ["probe.py"]}


def test_a_name_the_mirror_has_leaves_the_list_ahead_of_it():
    """The list is for names the mirror lacks. When the core that follows the engine has the command, this fails
    and says to delete the name from AHEAD_OF_THE_MIRROR: nothing stays listed for good."""
    declared = AHEAD_OF_THE_MIRROR & (set(COMMANDS) | set(EVENTS))
    assert not declared, "the mirror has %s now: take it out of AHEAD_OF_THE_MIRROR" % sorted(declared)
