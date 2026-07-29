"""Regression: the produced wheel must not contain duplicate zip entries.

The old pyproject.toml had a ``[tool.hatch.build.targets.wheel.force-include]``
section that re-included `data/` and `_fpforge/data/` already covered by
``packages = ["src/invisible_playwright"]``. Hatchling wrote every JSON twice
into the zip; PyPI rejects wheels with duplicate names.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest


@pytest.mark.slow
def test_built_wheel_has_no_duplicate_entries(tmp_path):
    """Build the wheel in a clean dir and assert no duplicate zip names."""
    root = Path(__file__).resolve().parent.parent
    out = tmp_path / "dist"
    r = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"build failed:\n{r.stderr}"

    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
        dupes = {n: c for n, c in Counter(names).items() if c > 1}

    assert not dupes, f"wheel has duplicate entries (PyPI will reject): {dupes}"
    # Sanity: the Bayesian data files must still be packaged.
    json_files = [n for n in names if n.endswith(".json")]
    assert json_files, "no .json data files in wheel - packaging broken"
