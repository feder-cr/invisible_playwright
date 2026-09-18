"""What version the code being imported actually is.

⛔ AN INSTALL RECORD IS NOT A DESCRIPTION OF THE CODE, AND FOR AN EDITABLE
INSTALL IT STOPS BEING ONE THE MOMENT SOMEBODY PULLS. `importlib.metadata`
answers about the DISTRIBUTION the installer put there. For a wheel that is the
same artifact as the code, so the number is right and there is nothing truer to
read. For `pip install -e` the metadata is written once and the code keeps
moving, and nothing says the two have parted.

Measured on the machine this is developed on, minutes after the checkout was
brought to zero commits behind `origin/main`: the record said 0.16.2 while the
tree said 0.22.1, six releases apart. Pulling does not touch the record, which
is what makes this a defect in the code rather than a stale checkout.

⛔ AND THE NUMBER IS WHAT A MEASUREMENT NAMES. This package is the thing under
test in every bench that drives a browser, so "measured against
invisible-playwright X" is the sentence that carries the result. Read from the
program, X was the moment somebody ran `pip install -e`, not the code being
measured. A finding was about to be written with a version six releases wrong;
it survived because its author had read the number off the TREE instead, which
is luck rather than method. The rule those benches already carry - a red against
an old dependency is not evidence about the product - keeps being broken because
nothing answered "which version am I measuring?" truthfully.

The version of the code is:

  - a normal install: the install record, because the metadata and the code
    came out of the same build;
  - an editable install: the version the SOURCE TREE declares, because that is
    the code that will run, plus a `+editable` local segment (PEP 440) so it
    can never be read as the published release of the same number. An editable
    tree can carry uncommitted work, so a bare `0.22.1` would invite a report
    against a release that does not contain the code being run.

Which of the two an install is comes from what the installer WROTE, not from a
guess about `__file__`: PEP 610 puts `direct_url.json` beside the metadata,
carrying `dir_info.editable` and the source directory. A wheel install has no
such file at all.

The install record stays, under a name that cannot be mistaken for the version.
`invisible_core` made this split first and names it the same way, deriving its
own `__version__` from the seal it ships; it is not a shared implementation and
must not become one by copying, because the core derives from an artifact it
PACKAGES while this reads the tree an install points at. `aihawk` carries the
same reading as this module for the same reason, so a correction here is worth
looking at there.
"""
from __future__ import annotations

import json
import tomllib
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

#: What this package is called on the index.
DISTRIBUTION = "invisible-playwright"

#: What a version says when there is nothing at all to read.
UNKNOWN = "0.0.0+unknown"

#: PEP 440 local segment marking a tree that is not a published artifact.
EDITABLE = "+editable"


def source_tree(dist: Distribution) -> Path | None:
    """The directory an EDITABLE install points at, or None for a normal one.

    Everything here is read from the record the installer wrote. The absence of
    `direct_url.json` is how a wheel install says it is one.
    """
    written = dist.read_text("direct_url.json")
    if not written:
        return None
    try:
        record = json.loads(written)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    if not (record.get("dir_info") or {}).get("editable"):
        return None
    url = record.get("url") or ""
    if not url.startswith("file:"):
        # An editable install of something fetched over the network leaves no
        # directory here to read, so the record is still the best there is.
        return None
    return Path(url2pathname(urlparse(url).path))


def declared_by(tree: Path) -> str | None:
    """The version that source tree declares, or None when it does not say one.

    `pyproject.toml` is where the version lives and the metadata is a copy of it
    taken at build time, so this goes back to the source of the copy rather than
    adding a second source. A tree declaring `dynamic = ["version"]` says
    nothing readable without running its build backend, and answering None there
    falls back to the record, which is the same answer as before this module.
    """
    try:
        parsed = tomllib.loads(
            (tree / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    declared = (parsed.get("project") or {}).get("version")
    return declared if isinstance(declared, str) and declared else None


def versions(name: str = DISTRIBUTION) -> tuple[str, str]:
    """(the version of the CODE, the version in the install record).

    The two differ exactly when the install is editable and the tree has moved
    since it was installed, which on a machine where this is developed is most
    of the time. `name` is a parameter so the behaviour can be tested against a
    real editable install of a real distribution rather than against a double.
    """
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        # A checkout on `sys.path` with nothing installed: no record to be
        # stale, and no recorded tree to read.
        return UNKNOWN, ""
    record = dist.version
    tree = source_tree(dist)
    if tree is None:
        return record, record
    declared = declared_by(tree)
    if declared is None:
        return record, record
    # Marked even when the two numbers agree: an editable tree is not the
    # published artifact whatever it declares, and that is what the marker says.
    return declared + EDITABLE, record


__version__, __install_record_version__ = versions()

__all__ = ["__version__", "__install_record_version__", "versions",
           "source_tree", "declared_by", "DISTRIBUTION", "EDITABLE", "UNKNOWN"]
