"""The README and the docs name the MCP server's repository and command as they are now.

After the September renames, `github.com/feder-cr/AIHawk` stopped being the MCP
server: GitHub sends a renamed name to whatever object carries it, and on
2026-11-06 that is an unrelated repository. Thirteen links answered 200 with a
page that was not the one promised, which no link checker reports. Seven places
also said `uvx aihawk`, a command that does not exist; the command is
`invisible-playwright-mcp`.

CHANGELOG.md keeps the old name on purpose (an entry says what was true then),
and so does the fixture in scripts/sync_article_pixels.py, which is the deliberate
wrong case. Neither is read here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
OLD = ("github.com/feder-cr/AIHawk", "uvx aihawk")


def pages():
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_no_page_links_the_old_repository_or_runs_the_old_command():
    found = ["%s:%d: %s" % (p.relative_to(ROOT), n, old)
             for p in pages()
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
             for old in OLD if old.lower() in line.lower()]
    assert not found, ("these name the MCP server by its old name, which now leads elsewhere: use "
                       "github.com/oss-invisible/playwright_mcp and `uvx invisible-playwright-mcp`:\n"
                       + "\n".join(found))
