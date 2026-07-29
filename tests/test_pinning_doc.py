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
    assert set(_PIN_GROUPS) == set(model), (
        f"pin groups and profile groups disagree: only in the validator "
        f"{sorted(set(_PIN_GROUPS) - set(model))}, only on the profile "
        f"{sorted(set(model) - set(_PIN_GROUPS))}")
    for group, fields in sorted(model.items()):
        assert _PIN_GROUPS[group] == fields, (
            f"group {group!r}: the validator accepts {sorted(_PIN_GROUPS[group])}, "
            f"the profile has {sorted(fields)}")


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

def test_the_pin_dicts_in_the_pages_examples_would_be_accepted():
    """The key tables and the worked examples drift apart independently: the
    examples are what people copy."""
    blocks = re.findall(r"```python\n(.*?)```", _doc(), re.S)
    checked = 0
    bad = {}
    for block in blocks:
        try:
            tree = ast.parse(block)
        except SyntaxError:                     # fragments with `...` in them
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
                continue
            if not any(getattr(t, "id", "") == "pin" for t in node.targets):
                continue
            for key_node in node.value.keys:
                if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                    continue
                checked += 1
                if key_node.value not in _valid_keys():
                    bad[key_node.value] = "rejected by the validator"
    assert checked, "no `pin = {...}` example left on the page to check"
    assert not bad, f"the page's worked examples use keys the API refuses: {bad}"
