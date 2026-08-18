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

    # ⛔ Il controllo di sanita' chiedeva `.json` nel wheel, con il commento
    # "the Bayesian data files must still be packaged". E' FALSO dal 2026-07-03:
    # il commit 76e41e2 che ha creato invisible_core ha spostato quei dati nel
    # core, e questo pacchetto non ne ha piu' nessuno. L'asserzione ha
    # continuato a chiedere una cosa che non esiste per sei settimane senza che
    # niente lo segnalasse, perche' il caso e' marcato `slow` e la selezione di
    # default lo DESELEZIONA: in CI non ha mai girato, e un gate che non gira e'
    # indistinguibile da uno che passa.
    #
    # Il controllo giusto per QUESTO pacchetto e' che il modulo ci sia davvero:
    # e' cio' che si rompe se `packages` smette di puntare al posto giusto, che
    # e' la stessa classe di guasto per cui il caso e' stato scritto.
    moduli = [n for n in names if n.startswith("invisible_playwright/")
              and n.endswith(".py")]
    assert moduli, f"nessun modulo del pacchetto nel wheel: {sorted(names)[:10]}"
    assert "invisible_playwright/__init__.py" in names, (
        "il wheel non contiene __init__.py: `packages` non punta al sorgente")
