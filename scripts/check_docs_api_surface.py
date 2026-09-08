#!/usr/bin/env python3
"""The guides must not teach an API this package does not have.

WHY THIS EXISTS. The guides in `docs/` are the surface a stranger copies first,
and nothing was reading them. Measured 2026-09-08 on 454 pages: one of them
published `tree = page.accessibility.snapshot()`, an attribute this fork does
not have and does not even refuse by name, so anybody who ran that block got an
AttributeError. It had been there long enough that nobody knows when it arrived,
which is the point: a broken test is seen by whoever runs the suite, a broken
guide is seen once by a reader who then leaves.

The same class has bitten this project before from the other side: removing the
`path` subcommand left thirteen wiki pages teaching a command that no longer
existed, and `test_readme_claims.py` exists because four of five lines in the
README's CLI block were `command not found`. This is that check, widened from
the README to the guides.

WHAT IT DOES NOT DO, AND WHY. Two designs were measured first and rejected.

* **Parsing every block as Python.** 82 of 1169 blocks fail on `unexpected
  indent`, and they are all deliberate fragments written to sit inside a `with`
  - a legitimate house style. A tolerance wide enough to accept them (dedent,
  wrap, dict-body) accepts nearly everything, so the gate would be born red on
  correct pages and then be widened until it saw nothing.
* **Checking attributes on a returned value.** This would NOT have caught the
  defect that prompted it. In [B200] `page.goto()` answered None while the
  guides read `.status`; `.status` is a real attribute of Response, so no
  static check of names could see it. That one is a runtime contract and is
  asserted where it belongs, in the server's own tests.

So this gate checks NAMES against the real objects, and nothing else. It is a
regression guard for renames and removals, plus the one live defect above.

WHAT IT CHECKS. For a small set of variable names that this package's own
examples bind to its own objects (`page`, `browser`, `context`, `response`,
`request`), every `name.attr` written inside a ```python block must be a real
attribute of the corresponding class in the vendored API. The classes are
imported, never listed here: a hand-written list of methods is a second source
of truth that drifts from the first.

PROSE IS NOT CODE, which is most of the noise. Only fenced ```python blocks are
read, so links (`browser.md`), file names (`page.png`) and sentences about other
tools (`page.authenticate()` attributed to Puppeteer) are outside the perimeter
by construction rather than by a list of exceptions.

FOREIGN OBJECTS THAT SHARE A NAME are the one place a list is unavoidable, and
it is kept as small as the corpus requires: a guide that combines this package
with `httpx` binds `response` to an httpx response, one that uses pdfplumber
binds `page` to a PDF page, and pytest's `request` fixture is not ours. A block
that imports or otherwise names such a library is skipped for that variable, and
the reason is recorded next to the name.

Exit codes: 0 clean, 1 something is named that does not exist, 2 the gate could
not run (the API did not import, no docs).
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

#: A block that mentions one of these is not talking about our object of that
#: name. Kept per-variable and as short as the corpus makes necessary; every
#: entry was put here by a measured false positive, not by anticipation.
FOREIGN = {
    "response": ("httpx", "requests"),
    "request": ("pytest", "httpx", "requests"),
    "page": ("pdfplumber", "PyPDF", "pypdf", "fitz", "hrequests"),
    "context": ("ssl", "decimal"),
}

BLOCK = re.compile(r"```python\n(.*?)```", re.S)


def api_classes() -> dict:
    """The real objects, imported. Never a hand-written list of methods."""
    sys.path.insert(0, str(REPO / "src"))
    from invisible_playwright._pw.sync_api import (
        Browser, BrowserContext, Page, Request, Response)
    return {"page": Page, "browser": Browser, "context": BrowserContext,
            "response": Response, "request": Request}


def attributes(cls) -> set:
    return {a for a in dir(cls) if not a.startswith("_")}


def blocks(text: str):
    return BLOCK.findall(text)


def names_used(src: str):
    """Every `name.attr` in a block, from the AST when it parses.

    ⛔ FALLS BACK TO A REGEX ON PURPOSE, because 82 blocks of this corpus are
    deliberate fragments that do not parse on their own. Skipping them would
    silently exempt 7% of the surface, which is how a gate ends up measuring
    less than it claims. The regex is only reached for those.
    """
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        for var, attr in re.findall(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)", src):
            out.add((var, attr))
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            out.add((node.value.id, node.attr))
    return out


def check(docs: pathlib.Path, verbose: bool = False):
    api = api_classes()
    known = {k: attributes(v) for k, v in api.items()}
    problems = []
    pages = seen = 0
    for path in sorted(docs.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        found = blocks(text)
        if found:
            pages += 1
        for i, src in enumerate(found):
            seen += 1
            for var, attr in names_used(src):
                if var not in known:
                    continue
                if any(f in src for f in FOREIGN.get(var, ())):
                    continue
                if attr not in known[var]:
                    problems.append((path.name, i, var, attr))
    if verbose:
        print("[docs-api] %d page(s) with python, %d block(s), against %s"
              % (pages, seen, ", ".join("%s(%d)" % (k, len(v))
                                        for k, v in sorted(known.items()))))
    return problems, pages, seen


def selftest() -> int:
    """Known-bad inputs, and the cases that must NOT fire.

    A gate that has only ever printed PASS is not a gate; one that refuses
    everything is just as useless, so the clean cases are counted too.
    """
    import tempfile

    bad = [
        ("an attribute the fork does not have",
         "```python\ntree = page.accessibility.snapshot()\n```"),
        ("a renamed method",
         "```python\npage.goto_url('https://example.com')\n```"),
        ("a removed one on the browser",
         "```python\nbrowser.new_page_please()\n```"),
        ("inside a fragment that does not parse",
         "```python\n    page.invented_thing()\n```"),
        ("on a response",
         "```python\nresponse.stat_us\n```"),
    ]
    clean = [
        ("a real method", "```python\npage.goto('https://example.com')\n```"),
        ("a real property", "```python\nprint(response.status, response.url)\n```"),
        ("prose, not a block", "See `page.accessibility` in another tool.\n"),
        ("a link that looks like an attribute", "[the browser](browser.md)\n"),
        ("an httpx response sharing the name",
         "```python\nimport httpx\nresponse.raise_for_status()\n```"),
        ("a pdfplumber page sharing the name",
         "```python\nimport pdfplumber\npage.extract_text()\n```"),
        ("the pytest request fixture",
         "```python\nimport pytest\nrequest.node.name\n```"),
    ]
    killed = passed = 0
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        for label, body in bad:
            for f in d.glob("*.md"):
                f.unlink()
            (d / "case.md").write_text(body, encoding="utf-8")
            found, _, _ = check(d)
            if found:
                killed += 1
            else:
                print("  SURVIVED (the gate is blind here): %s" % label)
        for label, body in clean:
            for f in d.glob("*.md"):
                f.unlink()
            (d / "case.md").write_text(body, encoding="utf-8")
            found, _, _ = check(d)
            if not found:
                passed += 1
            else:
                print("  FALSE POSITIVE on %s: %s" % (label, found))
    print("selftest: %d/%d mutations caught, %d/%d clean cases pass"
          % (killed, len(bad), passed, len(clean)))
    return 0 if (killed == len(bad) and passed == len(clean)) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--docs", default=str(REPO / "docs"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    docs = pathlib.Path(args.docs)
    if not docs.is_dir():
        print("[docs-api] no docs directory at %s" % docs, file=sys.stderr)
        return 2
    try:
        problems, pages, seen = check(docs, verbose=True)
    except ImportError as exc:
        print("[docs-api] the API did not import, so nothing was checked: %s"
              % exc, file=sys.stderr)
        return 2

    if not problems:
        print("[docs-api] clean")
        return 0
    print("[docs-api] the guides name %d thing(s) this package does not have:"
          % len(problems))
    for name, i, var, attr in problems:
        print("  docs/%s block #%d: %s.%s" % (name, i, var, attr))
    print("A reader who runs that block gets an AttributeError. Either the name "
          "is wrong, or the API was removed and the guide was left behind.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
