"""Every package path pyproject.toml names as present is a path this tree has.

⛔ KNOWN-BAD. The dependency comment said the Node driver lives in
src/invisible_playwright/_driver/: the folder does not exist, and a reader
deciding whether `playwright` could come back as a dependency was told about
a driver that is not there. The server is _juggler/.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_src_path_named_in_pyproject_exists():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    named = set(re.findall(r"\bsrc/invisible_playwright/([A-Za-z0-9_]+)/", text))
    missing = sorted(n for n in named if not (ROOT / "src" / "invisible_playwright" / n).is_dir())
    assert missing == [], "pyproject.toml names folders this tree does not have: %r" % missing
