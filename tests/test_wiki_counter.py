"""The seam between the wiki pixel and the endpoint that counts it.

Two halves in two languages, edited months apart. `scripts/build_wiki.py`
writes a URL into 327 published pages; `tools/wiki-counter/` serves it. Nothing
in either one fails if they stop agreeing - the wiki renders, the worker returns
404s, and a page that 404s looks exactly like a page nobody reads. The whole
feature would go quietly to zero and the first symptom would be someone
believing the numbers.

So this file asserts the two halves against ONE written-down URL, the
`WIKI_VIEW_PIXEL` line in `tools/wiki-counter/README.md`:

  - the Python half ACCEPTS it and produces exactly that URL for a given page
    (here, in this process);
  - the JavaScript half ROUTES it and records the page name it carries (in
    `tools/wiki-counter/test/worker.test.js`, which reads the same README).

It also runs the worker's own suite, so `pytest` covers both languages and the
endpoint is not the one part of this repository with untested code in it. Node
ships on every GitHub runner; where it does not, this skips loudly rather than
passing on an assertion it never made.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_BUILD_WIKI = _REPO / "scripts" / "build_wiki.py"
_COUNTER = _REPO / "tools" / "wiki-counter"
_README = _COUNTER / "README.md"
_SCHEMA = _COUNTER / "schema.sql"
_WORKER = _COUNTER / "src" / "worker.js"
_SQL = _COUNTER / "src" / "sql.js"


def worker_sql() -> dict[str, str]:
    """The two statements the worker will actually send, read out of it by node.

    Read from the module rather than pattern-matched out of the source: a regex
    over JavaScript would go on matching a statement that had been edited into
    something else, which is the failure this whole file is about.

    They live in `src/sql.js` and not in `worker.js` because workerd refuses to
    start a worker whose entry module has a non-function named export - see the
    comment at the top of sql.js.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot read the worker's SQL")
    code = (
        "import(%s).then(m => console.log(JSON.stringify("
        "{record: m.SQL_RECORD, rank: m.SQL_RANK})))"
        % json.dumps(_SQL.as_uri())
    )
    r = subprocess.run([node, "--input-type=module", "-e", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sql = json.loads(r.stdout)
    assert sql["record"] and sql["rank"], sql
    return sql


def sqlite_with_schema() -> sqlite3.Connection:
    """D1 is SQLite, so the real schema in real SQLite is a fair stand-in for
    the one thing the JavaScript suite cannot check: whether the SQL parses."""
    db = sqlite3.connect(":memory:")
    db.executescript(_SCHEMA.read_text(encoding="utf-8"))
    return db


def documented_template() -> str:
    """The one URL both halves are checked against."""
    m = re.search(r"WIKI_VIEW_PIXEL\s*=\s*(\S+)", _README.read_text(encoding="utf-8"))
    assert m, "tools/wiki-counter/README.md must document the WIKI_VIEW_PIXEL line"
    return m.group(1)


def render(tmp_path: pathlib.Path, pixel: str) -> dict[str, str]:
    """Build a two-page wiki with `pixel` as the template; return name -> text."""
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "index.md").write_text('---\ntitle: "Home"\n---\n\n# Home\n', encoding="utf-8")
    (docs / "botd-explained.md").write_text(
        '---\ntitle: "BotD"\nnav_order: 1\n---\n\n# BotD\n', encoding="utf-8")
    out = tmp_path / "wiki_build"
    env = dict(os.environ)
    env["WIKI_VIEW_PIXEL"] = pixel
    r = subprocess.run([sys.executable, str(_BUILD_WIKI), str(docs), str(out)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return {p.stem: p.read_text(encoding="utf-8") for p in out.glob("*.md")}


def test_the_documented_template_is_one_build_wiki_accepts(tmp_path):
    """The README is where someone copies the value from into the repository
    variable. If `build_wiki.py` would reject it, the next release fails on a
    line a human was told to use."""
    template = documented_template()
    assert template.startswith("https://"), template
    assert "{page}" in template, template
    render(tmp_path, template)  # asserts returncode 0


def test_the_url_in_the_page_is_the_template_with_the_page_name_in_it(tmp_path):
    """Exact, not approximate: this is the string the worker has to route, and
    the JS suite routes this same template on its side."""
    template = documented_template()
    pages = render(tmp_path, template)
    assert template.replace("{page}", "Home") in pages["Home"]
    assert template.replace("{page}", "botd-explained") in pages["botd-explained"]
    # `index` is the docs name; nothing should ever count a bucket by that name.
    assert template.replace("{page}", "index") not in pages["Home"]


def test_the_counter_has_no_npm_dependencies():
    """It is deployed with `wrangler deploy` and nothing else. A package.json
    appearing here means a lockfile, a supply chain and a build step for what is
    one file of plain ES modules - and CI below runs `node --test` directly,
    with no install step to pick them up."""
    assert not (_COUNTER / "package.json").exists()
    assert not (_COUNTER / "node_modules").exists()
    sources = sorted((_COUNTER / "src").glob("*.js"))
    assert sources, "tools/wiki-counter/src/ has no sources"
    for f in sources:
        for spec in re.findall(r'^\s*import .*? from "([^"]+)";',
                               f.read_text(encoding="utf-8"), re.M):
            # Relative is the worker's own modules; `node:` would only appear in
            # something that is not deployed. A bare specifier is npm.
            assert spec.startswith(("./", "../", "node:")), \
                "%s imports %r - the counter has no npm dependencies" % (f.name, spec)


def test_the_workers_own_suite_passes():
    """Runs `node --test` so `pytest` covers both halves. Without this the
    endpoint is the only untested code in the repository, and it is the half
    that can silently return 404 to 327 pages."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; tools/wiki-counter/ is untested in this run")
    # Enumerate the files rather than passing a glob: shell globbing does not
    # happen here, and node's own glob handling differs across versions.
    tests = sorted(str(p) for p in (_COUNTER / "test").glob("*.test.js"))
    assert tests, "tools/wiki-counter/test/ has no tests"
    r = subprocess.run([node, "--test", *tests], capture_output=True, text=True,
                       cwd=str(_COUNTER))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_schema_is_valid_sqlite():
    """`wrangler d1 execute --file=schema.sql` is run once, by hand, against the
    live database. A syntax error there is found at deploy time by a human
    reading an error - or not found, and the table simply is not there."""
    db = sqlite_with_schema()
    cols = [r[1] for r in db.execute("PRAGMA table_info(hits)")]
    assert cols == ["page", "day", "source", "n"]
    # The upsert names this exact conflict target; without the composite key it
    # is not an upsert, it is an error on the second hit of every page.
    pk = [r[1] for r in db.execute("PRAGMA table_info(hits)") if r[5]]
    assert sorted(pk) == ["day", "page", "source"]


def test_the_recording_statement_upserts_instead_of_erroring():
    """The premise of the whole storage design is one write per hit: the second
    hit on a page must INCREMENT, not raise UNIQUE-constraint and not insert a
    duplicate row. Nothing in the JavaScript suite can see this - its fake D1
    implements the increment itself."""
    db = sqlite_with_schema()
    sql = worker_sql()["record"]
    for _ in range(3):
        db.execute(sql, ("Home", "2026-08-28", "camo"))
    db.execute(sql, ("Home", "2026-08-28", "direct"))
    db.execute(sql, ("Home", "2026-08-29", "camo"))
    rows = sorted(db.execute("SELECT page, day, source, n FROM hits"))
    assert rows == [
        ("Home", "2026-08-28", "camo", 3),
        ("Home", "2026-08-28", "direct", 1),
        ("Home", "2026-08-29", "camo", 1),
    ]


def test_the_ranking_statement_ranks_and_splits_and_windows():
    """What /stats shows. If the GROUP BY or the CASE is wrong the page still
    renders - with the wrong pages at the top, which is unfalsifiable by eye."""
    db = sqlite_with_schema()
    sql = worker_sql()
    for _ in range(5):
        db.execute(sql["record"], ("popular", "2026-08-28", "camo"))
    db.execute(sql["record"], ("quiet", "2026-08-28", "camo"))
    db.execute(sql["record"], ("quiet", "2026-08-28", "direct"))
    db.execute(sql["record"], ("ancient", "2026-01-01", "camo"))

    rows = db.execute(sql["rank"], ("2026-08-01",)).fetchall()
    assert rows == [("popular", 5, 5), ("quiet", 2, 1)]  # ranked, split, windowed
    # The window is a real filter, not decoration.
    assert ("ancient", 1, 1) in db.execute(sql["rank"], ("2026-01-01",)).fetchall()


def test_a_page_name_with_sql_in_it_is_a_page_name():
    """Page names come from the wiki and end up in a bound parameter. They must
    stay data - the statement is parameterised, and this is what says so."""
    db = sqlite_with_schema()
    sql = worker_sql()
    nasty = "'; DROP TABLE hits; --"
    db.execute(sql["record"], (nasty, "2026-08-28", "camo"))
    assert db.execute("SELECT page, n FROM hits").fetchall() == [(nasty, 1)]
