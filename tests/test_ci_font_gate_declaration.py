"""The gate's copy of the generics map must not diverge from the core.

`scripts/ci_font_gate.py` carries a literal copy of
`zoom.stealth.fonts.generics`, and it is the only copy of that data in the
project. It exists for a narrow reason: the CI gate job installs only
Playwright, so `invisible_core` is not importable there and cannot become so
without changing the source repo's workflow, i.e. without rebuilding the five
archives.

Rule 16 forbids second sources. A copy nobody can see drifting would be
exactly that. This test binds it: if the core changes the declaration and the
gate falls behind, the suite goes red and the message says which of the two
moved.

Why it is REALLY needed, and is not a formality: without that declaration an
engine launched raw does not map the generics, because ever since the map is
declared instead of compiled the engine does not invent it (engine rule 7).
Measured 2026-08-11 on the same binary, raw launch on Linux: serif,
sans-serif, monospace, cursive and fantasy ALL collapse onto Arial. With the
declaration delivered they map onto Times New Roman, Arial, Consolas and Comic
Sans, with the exact same numbers as Windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[1] / "scripts" / "ci_font_gate.py"


def _generics_decl_from_gate() -> str:
    """The literal inside the gate, read without importing Playwright.

    The file imports playwright inside `main()`, so only the part preceding
    the definition compiles and executes: the constants are there, the driver
    is not.
    """
    src = _GATE.read_text(encoding="utf-8")
    head = src.split("def main")[0]
    ns: dict = {}
    exec(compile(head, str(_GATE), "exec"), ns)  # noqa: S102 - it's our own file
    return ns["GENERICS_DECL"]


@pytest.mark.unit
def test_ci_font_gate_generics_match_the_core():
    from invisible_core._fpforge import generate_profile
    from invisible_core.prefs import translate_profile_to_prefs

    prefs = translate_profile_to_prefs(generate_profile(970411), timezone="America/New_York")
    from_core = prefs["zoom.stealth.fonts.generics"]
    from_gate = _generics_decl_from_gate()

    assert from_gate == from_core, (
        "the generics map inside scripts/ci_font_gate.py is no longer the "
        "one that invisible_core declares.\n"
        "  core: %r\n  gate: %r\n"
        "Update GENERICS_DECL in the gate. If you don't, the gate measures a "
        "browser with a different persona than the one we ship, and its "
        "green no longer means anything."
        % (from_core[-70:], from_gate[-70:]))


@pytest.mark.unit
def test_the_generics_declaration_does_not_depend_on_the_seed():
    """If it depended on the seed, a fixed copy would be wrong by construction.

    This is the assumption that makes the copy legitimate: it must be
    verified, not assumed.
    """
    from invisible_core._fpforge import generate_profile
    from invisible_core.prefs import translate_profile_to_prefs

    values = {
        translate_profile_to_prefs(generate_profile(s), timezone="America/New_York")["zoom.stealth.fonts.generics"]
        for s in (1, 42, 970411, 20260811, 999999)
    }
    assert len(values) == 1, (
        "zoom.stealth.fonts.generics changes with the seed (%d distinct values "
        "across 5 seeds): the copy inside the gate can no longer be a fixed "
        "literal and must be read from the core." % len(values))
def _expected_from_gate() -> list:
    """The EXPECTED list inside the gate, read without importing Playwright."""
    src = _GATE.read_text(encoding="utf-8")
    head = src.split("def main")[0]
    ns: dict = {}
    exec(compile(head, str(_GATE), "exec"), ns)  # noqa: S102 - it's our own file
    return ns["EXPECTED"]


@pytest.mark.unit
def test_the_family_list_in_the_gate_matches_the_core_manifest():
    """The gate's literal against the `F|` records the core declares.

    ⛔ This test is the ONLY thing that keeps that literal honest, and for one
    day it existed only in the docs: `18-gate-inventory.md` named it as active
    while the tree had no function with this name. In that window the manifest
    moved to 71 families and the gate's list stayed at 68.

    ⛔ And what the drift produces is a GREEN, not a red, which explains why
    nobody noticed: the gate probes `EXPECTED + HOST_MUST_BE_ABSENT` and
    nothing else, so a family removed from the list also stops being SEARCHED
    FOR, the count balances on its own, and the gate prints
    `detected 68 families (expected 68)` and OK. Measured on the real binary on
    2026-08-17, exit 0, on a build that exposes 71 of them.

    The literal cannot be removed: the `release.yml` job that runs the gate
    installs only Playwright, so `invisible_core` is not importable there. The
    reason in full is in the comment above EXPECTED.
    """
    from invisible_core._fpforge.profile import FONT_MANIFEST

    from_core = [r.split("|")[1] for r in FONT_MANIFEST.splitlines()
                 if r.startswith("F|")]
    from_gate = _expected_from_gate()

    # ⛔ NO `%` in this message, and it is not a style choice: the first draft
    # used it after a `+` concatenation, so the trailing `%` bound ONLY to the
    # last group of adjacent literals - the one with no placeholders - and
    # raised `TypeError: not all arguments converted`. An assertion message is
    # evaluated only WHEN the assertion fails, so the defect manifests exactly
    # at the moment the explanation is needed: in CI a TypeError showed up
    # instead of the difference between the two lists. Concatenating values
    # already rendered with `repr` does not have this way of failing.
    if from_gate != from_core:
        in_core = sorted(set(from_core) - set(from_gate))
        in_gate = sorted(set(from_gate) - set(from_core))
        raise AssertionError(
            "the family list inside scripts/ci_font_gate.py is not the one "
            "that invisible_core declares." + chr(10)
            + "  in the core and not in the gate: " + repr(in_core) + chr(10)
            + "  in the gate and not in the core: " + repr(in_gate) + chr(10)
            + "  families: gate " + str(len(from_gate))
            + ", core " + str(len(from_core)) + chr(10)
            + "The gate would NOT go red on its own: families missing from "
            "EXPECTED also stop being probed, so it would print OK while "
            "staying blind to them." + chr(10)
            + "If this fires in CI during a release, look at the ORDER: CI "
            "installs the PUBLISHED core, not the tree's, so a gate updated "
            "before the core is on the index sees the old manifest. Publish "
            "the core, then move the pin, then use the new list.")
