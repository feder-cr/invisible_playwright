"""The wiki converter, and the view pixel it can append to every page.

WHY THIS FILE EXISTS AT ALL. `scripts/build_wiki.py` had no test. It runs once
per release, unattended, and pushes its output straight into the public wiki, so
the first reader of a bad conversion is a user - which is how nine pages under
`docs/integrations/` were once dropped silently (the comment in the script tells
that story). The view pixel adds a second way to be wrong in public: a URL
appended to 300+ pages, where a mistake is not a broken build but a broken image
on every page, or a counter with one bucket, discovered weeks later.

WHAT IS PINNED HERE. That the pixel is OFF unless asked for and that the pages
are then byte-identical to what they are today - a docs build must not start
calling a third party because someone imported this feature into a fork. That a
misconfigured template fails the BUILD rather than shipping, since both bad
shapes (no `{page}`, plain http) produce output that looks fine and counts
nothing. And that the page name in the URL is the WIKI name, so `Home` is the
bucket for the landing page - `index` would be a bucket nothing ever hits.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "build_wiki.py"

_TEMPLATE = "https://example.invalid/w/{page}.svg"


def _docs(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    """Write a miniature docs/ tree and return it."""
    root = tmp_path / "docs"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _build(tmp_path: pathlib.Path, files: dict[str, str], pixel: str | None = None):
    """Run the converter the way the workflow does, and return (result, out_dir)."""
    docs = _docs(tmp_path, files)
    out = tmp_path / "wiki_build"
    # Inherit the environment (a stripped one does not start python on Windows)
    # but DROP the variable, so a developer who has it exported cannot make the
    # off-by-default tests pass by accident - or fail one.
    env = dict(os.environ)
    env.pop("WIKI_VIEW_PIXEL", None)
    if pixel is not None:
        env["WIKI_VIEW_PIXEL"] = pixel
    r = subprocess.run([sys.executable, str(_SCRIPT), str(docs), str(out)],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    return r, out


_PAGE = '---\ntitle: "A page"\nnav_order: 1\n---\n\n# A page\n\nSee [other](other.md).\n'
_OTHER = '---\ntitle: "Other"\nnav_order: 2\n---\n\n# Other\n'
_INDEX = '---\ntitle: "Home"\n---\n\n# Home\n'

_TREE = {"index.md": _INDEX, "a-page.md": _PAGE, "other.md": _OTHER}


def test_off_by_default(tmp_path):
    """A fork that never heard of this feature must build the wiki it built
    before: no image, and so no request to anyone."""
    r, out = _build(tmp_path, _TREE)
    assert r.returncode == 0, r.stderr
    for name in ("Home", "a-page", "other"):
        assert "![](" not in (out / (name + ".md")).read_text(encoding="utf-8")
    assert "view pixel" not in r.stdout


def test_off_is_byte_identical_to_on_minus_the_pixel_line(tmp_path):
    """The feature may only ADD a line. Anything else it changed would be a
    silent rewrite of every page in the wiki on the next release."""
    off, out_off = _build(tmp_path / "off", _TREE)
    on, out_on = _build(tmp_path / "on", _TREE, _TEMPLATE)
    assert off.returncode == 0 and on.returncode == 0, (off.stderr, on.stderr)
    for f in sorted(p.name for p in out_off.glob("*.md")):
        before = (out_off / f).read_text(encoding="utf-8")
        after = (out_on / f).read_text(encoding="utf-8")
        assert after.startswith(before), f
        extra = after[len(before):]
        assert extra == "" or extra.startswith("\n![]("), (f, extra)


def test_every_page_gets_its_own_url(tmp_path):
    r, out = _build(tmp_path, _TREE, _TEMPLATE)
    assert r.returncode == 0, r.stderr
    assert "(with view pixel)" in r.stdout
    for name in ("Home", "a-page", "other"):
        text = (out / (name + ".md")).read_text(encoding="utf-8")
        assert text.endswith("\n![](https://example.invalid/w/%s.svg)\n" % name), name


def test_the_landing_page_counts_as_Home_not_index(tmp_path):
    """`index.md` is published as `Home`, and `/wiki/Home` is the URL a reader
    actually visits. Counting it as `index` would name a bucket for a page that
    does not exist and leave the most-read page of the wiki uncounted."""
    r, out = _build(tmp_path, _TREE, _TEMPLATE)
    assert r.returncode == 0, r.stderr
    home = (out / "Home.md").read_text(encoding="utf-8")
    assert "/w/Home.svg" in home
    assert "index" not in home


def test_the_sidebar_gets_no_pixel(tmp_path):
    """It renders alongside every page, so one URL there is a single bucket for
    the whole wiki - and the most heavily cached object on it."""
    r, out = _build(tmp_path, _TREE, _TEMPLATE)
    assert r.returncode == 0, r.stderr
    assert "![](" not in (out / "_Sidebar.md").read_text(encoding="utf-8")


def test_the_pixel_url_survives_the_link_rewriter(tmp_path):
    """The rewriter turns `](other.md)` into `](other)`. It must never touch the
    one absolute URL on the page: a mangled pixel is a broken image, published."""
    r, out = _build(tmp_path, _TREE, _TEMPLATE)
    assert r.returncode == 0, r.stderr
    page = (out / "a-page.md").read_text(encoding="utf-8")
    assert "[other](other)" in page          # the rewriter did its job
    assert "https://example.invalid/w/a-page.svg" in page


def test_a_page_name_is_escaped_into_the_url(tmp_path):
    """Page names come from filenames, and nothing forbids a space. Pasted into
    a URL raw it makes an invalid one - a broken image and no count."""
    tree = dict(_TREE)
    tree["odd name.md"] = '---\ntitle: "Odd"\n---\n\n# Odd\n'
    r, out = _build(tmp_path, tree, _TEMPLATE)
    assert r.returncode == 0, r.stderr
    text = (out / "odd name.md").read_text(encoding="utf-8")
    assert "/w/odd%20name.svg" in text
    assert "odd name.svg" not in text


def test_a_template_without_the_placeholder_fails_the_build(tmp_path):
    """Every page would fetch the same URL. The wiki would look right and the
    numbers would be one meaningless total, which is what this feature exists
    to replace - so it has to be a build failure, not a footnote."""
    r, out = _build(tmp_path, _TREE, "https://example.invalid/w/hit.svg")
    assert r.returncode != 0
    assert "{page}" in r.stderr
    assert not out.exists() or not list(out.glob("*.md"))


def test_a_plain_http_template_fails_the_build(tmp_path):
    """GitHub will not load it, so it is 300+ broken images and a counter at
    zero - a failure that only shows up by looking at the published wiki."""
    r, _ = _build(tmp_path, _TREE, "http://example.invalid/w/{page}.svg")
    assert r.returncode != 0
    assert "https://" in r.stderr


def test_an_empty_variable_is_off_not_a_broken_url(tmp_path):
    """An unset repository variable arrives as "" through the workflow env, and
    a whitespace-only value is the same mistake. Neither may publish `![]()`."""
    for value in ("", "   "):
        r, out = _build(tmp_path / ("v%d" % len(value)), _TREE, value)
        assert r.returncode == 0, r.stderr
        assert "![](" not in (out / "Home.md").read_text(encoding="utf-8")
