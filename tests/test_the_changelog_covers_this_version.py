"""The version this tree declares must have a heading in the changelog.

⛔ MEASURED 2026-09-08, AND THE ONLY THING THAT SAW IT WAS A GATE NOBODY WAS
LOOKING AT. `0.14.0` was bumped, tagged and published while CHANGELOG.md still
ended at `## [0.13.2]`. The release went out green: `tests` passed, `gate`
passed, the wheel and the sdist reached the index. The e2e went red seventeen
hours later for a reader who happened to open it.

The convention is not new and is not in dispute. It is written into
`test_release_e2e.py::test_the_changelog_documents_every_version_it_claims_to_cover`,
whose `in_flight` exception exists precisely because "the changelog entry has to
sit in the commit that bumps the version". What was missing is anything that
ASKS for it at the moment the bump is written.

WHY THIS FILE RATHER THAN A LINE IN THAT ONE. The e2e test asks the index what
was published, so it can only speak AFTER the upload, when the omission is
already public and the only repair is a second commit. It is also marked `e2e`,
which puts it out of the default selection and therefore out of `gate`, the one
context that protects main - and the workflow that does run it ignores every
push touching only `**.md`, so the very file it asserts on cannot start it. A
gate that cannot see its own subject change is not a gate on that subject.

This one compares two files in the tree and asks the network nothing, which is
what lets it live in the default selection: it runs in `pytest -q`, so it runs
in the `unit` job that `gate` requires, and it runs in the pre-push hook on the
machine of whoever writes the bump. The defect is refused where it is made
instead of being reported where it has already shipped.

WHAT IT DOES NOT DO, said plainly so nobody trusts it wider than it is. It does
not check the DATE against the upload, because at bump time there is no upload
to check against - that stays with the e2e test, which has the index in hand.
It does not check that the entry describes anything true. A heading with an
empty body satisfies it. It answers one question: does the version this tree
ships have a place in the file people read to find out what changed.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The same shape `test_release_e2e.py` reads, deliberately: two gates that
#: disagree about what a heading looks like would let a release through the
#: crack between them.
HEADING = re.compile(r"(?m)^##\s*\[(\d+\.\d+\.\d+)\]\s*-\s*(\d{4}-\d{2}-\d{2})\s*$")


def declared_version(root: pathlib.Path = ROOT) -> str:
    """What `pyproject.toml` says this tree ships."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def documented_versions(root: pathlib.Path = ROOT) -> dict[str, str]:
    """Every `## [X.Y.Z] - YYYY-MM-DD` heading in the changelog, version to date."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in HEADING.finditer(text)}


def test_the_changelog_has_a_heading_for_the_version_this_tree_ships():
    documented = documented_versions()
    assert documented, (
        "CHANGELOG.md has no `## [X.Y.Z] - YYYY-MM-DD` heading at all, so either "
        "the file or this gate's idea of it is wrong")

    version = declared_version()
    assert version in documented, (
        f"pyproject.toml ships {version} and CHANGELOG.md does not document it. "
        f"The newest heading is {max(documented, key=lambda v: tuple(int(p) for p in v.split('.')))}. "
        f"The entry belongs in the commit that bumps the version, not in a "
        f"repair after the upload: 0.14.0 went out without one on 2026-09-08 "
        f"and the omission was public for seventeen hours")


def test_the_gate_refuses_a_bump_that_left_the_changelog_behind(tmp_path):
    """The known-bad, which is the state main was actually in yesterday.

    A gate that has only ever printed PASS is not a gate, and this one is cheap
    to fool: every assertion above is satisfied by a file that happens to hold
    the right string. So the mutation is the real thing, reconstructed - a
    pyproject one version ahead of the newest heading - and the helpers must
    disagree about it.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "invisible-playwright"\nversion = "0.14.0"\n',
        encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.13.2] - 2026-09-07\n\n"
        "### Fixed\n- something\n",
        encoding="utf-8")

    assert declared_version(tmp_path) == "0.14.0"
    assert "0.14.0" not in documented_versions(tmp_path)
    assert "0.13.2" in documented_versions(tmp_path), (
        "the mutation has to keep a valid heading, or it would prove only that "
        "the parser can fail to match anything at all")


def test_the_gate_accepts_the_repaired_tree(tmp_path):
    """The other half: a mutation that survives a check which is simply blind.

    Without this, a `documented_versions` that returned the empty dict for every
    input would pass the known-bad above and be called a working gate.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "invisible-playwright"\nversion = "0.14.0"\n',
        encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.14.0] - 2026-09-08\n\n"
        "### Added\n- something\n\n## [0.13.2] - 2026-09-07\n",
        encoding="utf-8")

    assert declared_version(tmp_path) in documented_versions(tmp_path)
