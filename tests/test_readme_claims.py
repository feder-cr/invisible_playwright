"""The README is the first thing a stranger runs. It has to be runnable.

Four of the five lines in its CLI block were `command not found`: they used
`invisible_playwright` with an underscore, while the installed console script is
`invisible-playwright` with a hyphen. The Install section two hundred lines
above was correct (`python -m invisible_playwright ...`), which is how the CLI
block survived - anybody testing the quickstart never reached it.

Nothing here reads prose. These check the claims that are mechanically
checkable: that a documented subcommand exists, that a documented environment
variable is one the code reads, and that the download size is not the one from
two engines ago.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_README = _REPO / "README.md"


def _readme() -> str:
    return _README.read_text(encoding="utf-8")


def _declared_script() -> str:
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"].get("scripts") or {}
    assert scripts, "the package declares no console script"
    return sorted(scripts)[0]


def test_the_readme_never_invokes_a_command_that_is_not_installed():
    """The exact failure: `invisible_playwright fetch` at a shell prompt.

    Only the module form takes an underscore. Any bare invocation must use the
    name pip actually puts on PATH.
    """
    script = _declared_script()
    bad = [
        line.strip()
        for line in _readme().splitlines()
        # a shell line starting with the underscored name, but NOT `python -m`
        if re.match(r"^\s*invisible_playwright\s+\w", line)
    ]
    assert not bad, (
        f"these README lines invoke a command that is not installed - the "
        f"console script is {script!r}:\n  " + "\n  ".join(bad))


def test_every_subcommand_the_readme_shows_actually_exists():
    """A documented subcommand that argparse rejects sends a reader looking for
    their own mistake."""
    from invisible_playwright import cli

    parser = cli.build_parser()
    known = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            known |= set(action.choices)
    shown = set(re.findall(r"^\s*invisible-playwright\s+([a-z-]+)", _readme(), re.M))
    unknown = sorted(shown - known)
    assert not unknown, (
        f"the README documents subcommands the CLI does not have: {unknown}. "
        f"It accepts {sorted(known)}")


def test_the_readme_does_not_hide_a_subcommand_the_cli_offers():
    """`doctor` existed and appeared nowhere. It is the command whose output a
    bug report needs, so leaving it undocumented costs twice."""
    from invisible_playwright import cli

    parser = cli.build_parser()
    known = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            known |= set(action.choices)
    text = _readme()
    missing = sorted(name for name in known if name not in text)
    assert not missing, f"these CLI subcommands are undocumented: {missing}"


def test_the_stated_download_size_matches_the_sealed_assets():
    """It said ~100 MB. The largest sealed asset is 238 MB, and unpacking is
    544 MB - a 2.4x understatement on the one number a user checks against
    their disk and their patience before starting.
    """
    import json

    import invisible_core

    seal = json.loads(
        (pathlib.Path(invisible_core.__file__).with_name("seal.json"))
        .read_text(encoding="utf-8"))
    largest_mb = max(a["size"] for a in seal["assets"].values()) / (1024 * 1024)
    text = _readme()
    stated = [int(n) for n in re.findall(r"~?(\d{2,4})\s*MB", text)]
    assert stated, "the README no longer states a download size at all"
    # EVERY figure must be plausible, not merely one of them. The first version
    # of this test accepted the set if ANY value was close, so changing one of
    # the two occurrences back to the old ~100 MB left it green - a check that
    # tolerates a wrong number beside a right one.
    #
    # The band spans the smallest sealed asset to the unpacked size, because the
    # page legitimately quotes both.
    smallest_mb = min(a["size"] for a in seal["assets"].values()) / (1024 * 1024)
    lo, hi = smallest_mb * 0.85, largest_mb * 2.6
    wrong = sorted(n for n in stated if not (lo <= n <= hi))
    assert not wrong, (
        f"the README quotes {wrong} MB, outside the {lo:.0f}-{hi:.0f} MB the "
        f"seal supports (assets {smallest_mb:.0f}-{largest_mb:.0f} MB, ~544 MB "
        f"unpacked). A size wrong by more than a rounding error is worse than "
        f"no size")


#: Where the environment variables are documented for a reader. NOT the README:
#: the "Environment variables" table was removed from it on 2026-07-28, because
#: every row described something the package does by itself - it downloads,
#: verifies a sha256, caches, picks a cursor engine - and none of it is a step
#: anybody takes. The table was also a THIRD copy, after docs/configuration.md and
#: docs/installation.md.
_ENV_DOC = "docs/configuration.md"

#: Prefixes that make a name ours. A knob belonging to Playwright or to the OS is
#: not this project's to document or to read.
_ENV_PREFIXES = ("INVISIBLE_PLAYWRIGHT_", "INVPW_", "STEALTHFOX_", "INVISIBLE_CORE_")


def _documented_env_names() -> list:
    """Read the list out of the doc page instead of hardcoding it.

    The hardcoded version named four, and there are more than four - so a fifth
    knob could be documented and read by nothing without this noticing. Deriving
    it also means the test follows the page rather than having to be edited
    alongside it, which is how the four came to be about the README after the
    README stopped mentioning them.
    """
    import re

    path = _REPO / _ENV_DOC
    assert path.is_file(), f"{_ENV_DOC} is gone; this test has lost its subject"
    text = path.read_text(encoding="utf-8")
    names = sorted({m for m in re.findall("[A-Z][A-Z0-9_]{4,}", text)
                    if m.startswith(_ENV_PREFIXES)})
    assert len(names) >= 5, (
        f"only {len(names)} environment variables found in {_ENV_DOC}: {names}. "
        f"Either the page stopped documenting them - in which case this test is "
        f"passing over an empty set - or the prefixes above no longer match.")
    return names


@pytest.mark.parametrize("name", _documented_env_names())
def test_documented_environment_variables_are_read_somewhere(name):
    """A documented knob that nothing reads is worse than an undocumented one:
    the reader sets it, sees no effect, and distrusts the rest of the page."""
    # Some of these are read by the CORE, not by this package. The first version
    # looked for them in `_REPO.parent / "invisible_core" / "src"` - a SIBLING
    # CHECKOUT, which exists on the workbench and nowhere else. On CI, where only
    # this repo is checked out and the core arrives as a wheel, that path is
    # missing, the comprehension yields nothing, and the test reported that a
    # documented variable is read by no module. It was reading the developer's
    # directory layout.
    #
    # The INSTALLED package is the right thing to ask either way: it is what a
    # user's environment actually contains, editable checkout or wheel.
    import invisible_core

    roots = [_REPO / "src", pathlib.Path(invisible_core.__file__).parent]
    hits = [
        path
        for root in roots
        for path in root.rglob("*.py")
        if "__pycache__" not in path.as_posix()
        and name in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits, (
        f"{_ENV_DOC} documents {name}, which no shipped module reads. Either wire "
        f"it up or take the row out - a knob with no effect makes a reader "
        f"distrust the whole page.")


# ---------------- the claims nothing was checking (audited 2026-07-28)

def _seal() -> dict:
    import json

    import invisible_core
    return json.loads(
        pathlib.Path(invisible_core.__file__).with_name("seal.json")
        .read_text(encoding="utf-8"))


def test_the_engine_version_on_the_page_is_the_one_in_the_seal():
    """The same defect that shipped `firefox-15` in the manager's status bar.

    A version written into a page is a second copy of a number the seal already
    owns, and it goes stale the next time the engine rolls. Two places carry it
    here: the badge's `alt` text in the README, and the badge SVG itself, which is
    a generated file nobody re-reads.

    Found by auditing which README claims had a gate: sizes did, subcommands did,
    the engine version did not. It said 151.0 and the seal said 151.0, which is
    exactly how a stale number looks the day before it goes wrong.
    """
    upstream = _seal().get("upstream_version")
    assert upstream, "the seal carries no upstream_version to compare against"
    text = _readme()

    versions = set(re.findall(r"Firefox\s+(\d+(?:\.\d+)*)", text))
    assert versions, "the README no longer states a Firefox version at all"
    wrong = sorted(v for v in versions
                   if not (v == upstream or upstream.startswith(v + ".")
                           or v.split(".")[0] == upstream.split(".")[0]))
    assert not wrong, (
        f"the README says Firefox {wrong} and the packaged seal says {upstream}. "
        f"The seal owns this number; a copy on the page survives the next engine "
        f"roll and tells a user they are getting something they are not.")

    badge = _REPO / "docs" / "badges" / "firefox.svg"
    if badge.is_file():
        svg = badge.read_text(encoding="utf-8", errors="ignore")
        assert upstream in svg, (
            f"docs/badges/firefox.svg does not mention {upstream}; it is generated "
            f"and nobody re-reads it, so a stale badge outlives every other copy")


def test_the_supported_platforms_are_the_ones_the_seal_actually_ships():
    """Five archives in the seal, five platforms on the page, and nothing tied
    them together.

    A platform claimed but not built is a download that 404s for whoever believed
    the page - which is issue #14's shape, from the other end. A platform built
    but not claimed is quieter and still wrong: somebody concludes it will not
    work and uses something else.

    Derived from the asset basenames rather than a written list, so a leg added or
    dropped in the seal shows up here rather than in a bug report.
    """
    assets = " ".join(_seal()["assets"].keys()).lower()
    text = _readme().lower()

    #: seal token -> the SPECIFIC phrase the page has to carry for it.
    #:
    #: The first version accepted a bare family name, so "macos" alone satisfied
    #: `macos-arm64` AND `macos-x86_64`. Deleting the whole "macOS arm64 / x86_64"
    #: claim left the gate green, because the word macOS still appears in the
    #: Gatekeeper note two lines down. A gate that a passing mention satisfies is
    #: not a gate - measured by deleting exactly that phrase, 2026-07-28.
    expected = {
        "win-x86_64": ["windows x86_64"],
        "linux-x86_64": ["linux x86_64"],
        "linux-arm64": ["arm64"],
        "macos-arm64": ["macos arm64"],
        "macos-x86_64": ["macos arm64 / x86_64", "macos x86_64"],
    }
    missing = []
    for token, spellings in expected.items():
        if token not in assets:
            continue                      # the seal does not ship it; nothing to claim
        if not any(sp in text for sp in spellings):
            missing.append(
                f"{token} is in the seal and the page never says {spellings[0]!r}")
    assert not missing, "\n  ".join(
        ["the page and the seal disagree about what runs:"] + missing)

    # And the other direction: nothing claimed that is not built.
    for token in expected:
        if token in assets:
            continue
        family = token.split("-")[0]
        assert family not in text, (
            f"the page mentions {family} but the seal ships no {token} archive, so "
            f"that download does not exist")


def test_every_python_example_on_the_page_parses():
    """Seven fenced python blocks, and nothing had ever compiled one.

    A README example is the first code anybody runs. A stray comma in it costs a
    reader their first five minutes and reads as "this project does not work".
    Parsed, not executed - executing would launch a browser.
    """
    import ast

    blocks = re.findall(r"```python\n(.*?)```", _readme(), re.DOTALL)
    assert len(blocks) >= 5, f"only {len(blocks)} python blocks found on the page"
    broken = []
    for i, block in enumerate(blocks, 1):
        try:
            ast.parse(block)
        except SyntaxError as exc:
            broken.append(f"block {i} line {exc.lineno}: {exc.msg}")
    assert not broken, (
        "these examples do not parse:\n  " + "\n  ".join(broken))


def test_the_stated_field_count_is_in_the_right_order_of_magnitude():
    """"~400 fields" was written once and never compared to a profile.

    Not pinned to an exact number - the generator's surface moves for good reasons
    and a test demanding 400 exactly would be edited to whatever it produces,
    which is not a check. A factor-of-two band is what makes "~400" honest: 200
    would be an overstatement a reader could measure, and 900 an understatement
    that undersells the thing.
    """
    stated = re.findall(r"~?(\d{2,4})\s*fields", _readme())
    if not stated:
        pytest.skip("the page no longer states a field count")
    import dataclasses

    from invisible_core import generate_profile

    def leaves(obj):
        """Every scalar in the profile, nested lists and dicts included.

        This is the GENEROUS reading of "fields", and deliberately so: it counts
        each bundled font name as a field. The stricter readings are smaller -
        155 prefs emitted, 157 including the stealth baseline - so if the claim
        fails against this one it fails against all of them.
        """
        if dataclasses.is_dataclass(obj):
            return sum(leaves(getattr(obj, f.name)) for f in dataclasses.fields(obj))
        if isinstance(obj, dict):
            return sum(leaves(v) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return sum(leaves(v) for v in obj)
        return 1

    real = leaves(generate_profile(4242))
    for claim in (int(s) for s in stated):
        assert real / 2 <= claim <= real * 2, (
            f"the page says ~{claim} fields and a profile carries {real} of them "
            f"on the most generous count. MEASURED 2026-07-28: the page said ~400 "
            f"against 197 leaves, 155 emitted prefs and 157 including the stealth "
            f"baseline - an overstatement of about 2x on every reading, written "
            f"once and never compared to a profile.")
