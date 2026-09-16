"""`docs/pinning.md` is a list of knobs. Every one of them has to turn.

The page documented a top-level `fonts` key - "complete font allowlist, the
sampler usually picks 14-24 system fonts" - and passing it raises
`ValueError: pin key 'fonts' is not valid`. It had stopped being an axis when
the engine moved to a bundled font list: the exposed set is the same 72 families
on every install and every OS by construction, and varying it per profile would
put back the entropy the bundle exists to remove. The row outlived the feature by
several releases because nothing read the page.

These read the page mechanically. No prose is interpreted: a documented key is
handed to the real validator, and the real validator's own table is compared
against the profile model it is supposed to describe.

The reverse direction is here too, and it is the one that catches the next
occurrence rather than this one: a knob that exists and is undocumented is a knob
nobody uses, and it will be removed by someone who believes it is dead.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from invisible_core import generate_profile
from invisible_core._fpforge.profile import _PIN_GROUPS, _PIN_TOP

pytestmark = pytest.mark.unit

_DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "pinning.md"

#: Fields that exist on the profile but are deliberately NOT pinnable, with the
#: reason. Listed so the reverse-direction test can tell "not pinnable on
#: purpose" from "somebody forgot".
_NOT_PINNABLE = {
    "seed": "the pin's own input",
    "browsing_history": "derived from the seed, so fixing the seed fixes it",
    "_raw": "the pre-profile sample dict, not an axis",
}

#: Individual group FIELDS that exist on the profile and are deliberately not
#: pinnable. Third level of the same idea as the scalar list above and the group
#: list below: a field with no pin entry is either a decision or an omission, and
#: only one of those should pass.
_NOT_PINNABLE_FIELDS = {
    "screen.tier": ("The sampler's own label for the screen it drew ('1440p'). "
                    "Pins are applied after the draw, so pinning the tier could "
                    "not condition the screen it names, and it emitted no "
                    "preference either. Removed from the pin table 2026-09-15; "
                    "the field stays because the label is honest."),
    "screen.avail_width": ("Derived, not chosen. The engine computes the "
                           "available rect from width, height and taskbar_px, "
                           "and no avail value is emitted, so a pin moved the "
                           "label and nothing a page reads. Removed 2026-09-15."),
    "screen.avail_height": ("Same as avail_width, and removing it also removed "
                            "the special case that let a pin beat the taskbar "
                            "re-derivation: there is no pin left to lose to."),
}

#: Profile GROUPS that exist but are deliberately not pinnable, with the reason.
#: Same idea as _NOT_PINNABLE one level up: a group with no pin table is either a
#: decision or an omission, and only one of those should pass.
_NOT_PINNABLE_GROUPS = {
    "webgl": ("msaa_samples is the group's only field and the emitted sample count "
              "is now the same constant on both builds, so a pin has nothing to "
              "move. It was pinnable until 2026-09-15, honoured on Linux and "
              "overridden on Windows, which is what made the two builds emit a "
              "different gl.SAMPLES for the same seed."),
}


def _doc() -> str:
    return _DOC.read_text(encoding="utf-8")


def _documented_keys() -> set[str]:
    """Keys from the leading `| \\`key\\` |` cell of every table row.

    Digits are part of the character class on purpose: an earlier pass wrote it
    as `[a-z_]+` and silently skipped `codec.av1_enabled` and
    `codec.mediasource_mp4`, then reported them as undocumented.
    """
    return set(re.findall(r"^\|\s*`([a-z_][a-z0-9_]*(?:\.[a-z0-9_]+)?)`\s*\|",
                          _doc(), re.M))


def _valid_keys() -> set[str]:
    return set(_PIN_TOP) | {
        f"{group}.{field}" for group, fields in _PIN_GROUPS.items() for field in fields
    }


# ── every documented key has to be accepted ────────────────────────────────

def test_every_key_the_page_documents_is_accepted_by_the_real_validator():
    """Through `generate_profile`, not against a copy of the key table: the
    thing in doubt is what a reader's call does, and a check against the table
    would pass even if the caller stopped consulting it."""
    rejected = {}
    for key in sorted(_documented_keys()):
        try:
            generate_profile(seed=42, pin={key: _sample_for(key)})
        except ValueError as exc:
            if "is not valid" in str(exc) or "unknown group" in str(exc) \
                    or "unknown field" in str(exc):
                rejected[key] = str(exc).split(".")[0]
        except Exception:                       # a bad VALUE is not this test's business
            continue
    assert not rejected, (
        f"docs/pinning.md documents pin keys the API refuses: {rejected}. A "
        f"reader who copies the page gets a ValueError and has no way to know "
        f"the page is the wrong one")


def _sample_for(key: str):
    """A value of roughly the right shape. Only the KEY is under test - a wrong
    type raises something other than the not-valid ValueError, which is filtered
    out above rather than asserted on."""
    if key in ("dark_theme",) or key.startswith("codec."):
        return True
    if key.endswith((".width", ".height", ".avail_width", ".avail_height",
                     ".concurrency", ".storage_quota_mb", ".sample_rate",
                     ".max_channel_count", ".msaa_samples")):
        return 8
    if key.endswith((".dpr", ".output_latency_ms")):
        return 1.0
    return "x"


# ── and every accepted key has to be documented ────────────────────────────

def test_every_pinnable_key_appears_on_the_page():
    missing = sorted(_valid_keys() - _documented_keys())
    assert not missing, (
        f"these keys can be pinned and are documented nowhere: {missing}. An "
        f"undocumented knob is one nobody uses, and it gets deleted by whoever "
        f"next reads the code as dead")


def test_fonts_is_gone_and_stays_gone():
    """Named explicitly because the row survived several releases, and because
    the remedy for a reader who remembers it is not obvious from a ValueError."""
    assert "fonts" not in _valid_keys()
    assert not re.search(r"^\|\s*`fonts`", _doc(), re.M), (
        "the `fonts` row is back in the key table. It is not an axis: the "
        "bundled font list exposes the same families on every install by "
        "construction")
    assert "fonts` is not one of them" in _doc() or "no pin" in _doc(), (
        "the page must still explain what happened to fonts - a reader who "
        "remembers the key needs to be told it went away and why, not just "
        "find it absent")


# ── the validator's table has to describe the model it validates ───────────

def test_the_validators_key_table_matches_the_profile_it_pins():
    """`_PIN_GROUPS` is hand-written beside the dataclasses it describes.

    A field added to a profile group and not added here is unpinnable with no
    error anywhere - the knob simply does not exist, and the only symptom is a
    user asking why their pin did nothing. Cheap to check, and neither list is
    generated from the other.
    """
    import dataclasses

    profile = generate_profile(seed=42)
    model = {
        field.name: {f.name for f in dataclasses.fields(getattr(profile, field.name))}
        for field in dataclasses.fields(profile)
        if dataclasses.is_dataclass(getattr(profile, field.name))
    }
    unexplained = sorted(set(model) - set(_PIN_GROUPS) - set(_NOT_PINNABLE_GROUPS))
    assert not unexplained and not (set(_PIN_GROUPS) - set(model)), (
        f"pin groups and profile groups disagree: only in the validator "
        f"{sorted(set(_PIN_GROUPS) - set(model))}, only on the profile and not "
        f"declared in _NOT_PINNABLE_GROUPS {unexplained}")
    stale = sorted(set(_NOT_PINNABLE_GROUPS) & set(_PIN_GROUPS))
    assert not stale, (
        f"_NOT_PINNABLE_GROUPS still excuses {stale}, which is pinnable again. "
        f"A stale excuse reads as a decision that still holds.")
    for group, fields in sorted(model.items()):
        if group in _NOT_PINNABLE_GROUPS:
            continue
        excused = {f for f in fields if f"{group}.{f}" in _NOT_PINNABLE_FIELDS}
        assert _PIN_GROUPS[group] == fields - excused, (
            f"group {group!r}: the validator accepts {sorted(_PIN_GROUPS[group])}, "
            f"the profile has {sorted(fields)}, excused {sorted(excused)}")
    stale_fields = sorted(k for k in _NOT_PINNABLE_FIELDS if k in _valid_keys())
    assert not stale_fields, (
        f"_NOT_PINNABLE_FIELDS still excuses {stale_fields}, which is pinnable "
        f"again. A stale excuse reads as a decision that still holds.")
    unknown_fields = sorted(
        k for k in _NOT_PINNABLE_FIELDS
        if k.split(".", 1)[0] not in model or k.split(".", 1)[1] not in model[k.split(".", 1)[0]])
    assert not unknown_fields, (
        f"_NOT_PINNABLE_FIELDS names fields the profile no longer has: {unknown_fields}")


def test_top_level_fields_are_either_pinnable_or_listed_as_deliberately_not():
    """So that a new scalar on the profile cannot quietly become a knob nobody
    can turn, or a knob nobody knows about."""
    import dataclasses

    profile = generate_profile(seed=42)
    scalars = {
        field.name for field in dataclasses.fields(profile)
        if not dataclasses.is_dataclass(getattr(profile, field.name))
    }
    unaccounted = sorted(scalars - set(_PIN_TOP) - set(_NOT_PINNABLE))
    assert not unaccounted, (
        f"top-level profile fields that are neither pinnable nor recorded as "
        f"deliberately unpinnable: {unaccounted}")


# ── the page's own examples have to run ────────────────────────────────────

#: How many worked examples the page carried when the executing test below was
#: written. A FLOOR, not an equality: the page is meant to grow.
#:
#: It is a floor rather than `assert checked`, and that distinction is the whole
#: reason it exists. Until 2026-09-16 the extraction saw only `pin = {...}` and
#: was blind to the opening example, which passes `pin={...}` as a keyword
#: argument - and `assert checked` stayed green throughout, because the two
#: other examples kept the count off zero. A count that only has to be non-zero
#: cannot notice that it stopped seeing a third of the page.
_EXAMPLES_FLOOR = 3


def _example_pins() -> list[dict]:
    """Every `pin` dict on the page, in BOTH of the shapes the page writes.

    `pin = {...}` is an `ast.Assign`. The page's opening example hands
    `pin={...}` straight to the constructor, which is an `ast.keyword` and was
    invisible to the first version of this extraction - so the block a reader
    meets first was the one block nothing checked, and it was one of the two
    that raised.

    `literal_eval` rather than a walk over the key nodes, because the executing
    test needs the VALUES. A pin assembled out of names or calls raises here
    rather than being skipped, which is right: it is not something a reader can
    copy either.
    """
    pins: list[dict] = []
    for block in re.findall(r"```python\n(.*?)```", _doc(), re.S):
        try:
            tree = ast.parse(block)
        except SyntaxError:                     # fragments with `...` in them
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                    and any(getattr(t, "id", "") == "pin" for t in node.targets):
                value = node.value
            elif isinstance(node, ast.keyword) and node.arg == "pin" \
                    and isinstance(node.value, ast.Dict):
                value = node.value
            else:
                continue
            pins.append(ast.literal_eval(value))
    return pins


def _examples() -> list[dict]:
    """The page's examples, with the count asserted in ONE place.

    Both tests below need the same floor, and a number written twice is a
    number that drifts.
    """
    pins = _example_pins()
    assert len(pins) >= _EXAMPLES_FLOOR, (
        f"found {len(pins)} `pin` example(s) on the page, expected at least "
        f"{_EXAMPLES_FLOOR}. Either the page lost an example, or the extraction "
        f"stopped matching the way they are written - and an extraction that "
        f"finds nothing reads exactly like a page with nothing wrong on it")
    return pins


def _label(pin: dict) -> str:
    """Enough of an example to find it on the page."""
    return ", ".join(sorted(pin))[:120]


def test_the_pin_dicts_in_the_pages_examples_would_be_accepted():
    """The key tables and the worked examples drift apart independently: the
    examples are what people copy.

    KEYS only. Kept beside the executing test below rather than replaced by it:
    it is the cheaper half, it names the offending key directly instead of
    reporting whatever the validator happened to raise first, and it keeps
    saying something about a key whose value is awkward to produce.
    """
    bad = {}
    for pin in _examples():
        for key in pin:
            if key not in _valid_keys():
                bad[key] = "rejected by the validator"
    assert not bad, f"the page's worked examples use keys the API refuses: {bad}"


def test_the_pages_examples_run_exactly_as_written():
    """The VALUES too, through the real validator. A key table cannot see one.

    Measured 2026-09-16: two of the page's three examples named GPU renderer
    strings that no persona in the pool presents - an RTX 4090 in the opening
    block, an Iris Xe in "mimic a specific real device" - and both raised
    `ValueError: pin gpu.renderer/gpu.vendor (...) names no validated GPU
    persona` on the first lines a reader would copy. Every key in them was
    valid, so the test above was green the whole time: the GPU refusal is on
    the VALUE, which is the half nothing here was reading.
    """
    raised = {}
    for pin in _examples():
        try:
            generate_profile(seed=42, pin=pin)
        except Exception as exc:
            raised[_label(pin)] = f"{type(exc).__name__}: {exc}"
    assert not raised, (
        f"these worked examples raise when run exactly as the page writes "
        f"them: {raised}. A reader copies the block, gets a traceback, and has "
        f"no way to know that it is the page that is wrong")
