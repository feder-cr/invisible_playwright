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

import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_BUILD_WIKI = _REPO / "scripts" / "build_wiki.py"
_COUNTER = _REPO / "tools" / "wiki-counter"
_README = _COUNTER / "README.md"


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
    worker = (_COUNTER / "src" / "worker.js").read_text(encoding="utf-8")
    assert 'from "' not in worker.replace('from "node:', 'from "IGNORED:'), \
        "the worker must import nothing"


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
