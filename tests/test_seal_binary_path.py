"""binary_path= is the one silent cell that does not need a warm cache. D1.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md sections 4.12,
4.13 (rows 4 and 5) and 7. Audit: 16-version-divergence-matrix.md D1 and
section 3 ("Also worth stating plainly about D1").

`launcher.py:215` and `async_api.py:101` are `self._binary_path or ensure_binary()`,
so an arbitrary path skips the download path entirely: no tag, no checksum, no
cache, and the prefs plus the spoofed User-Agent still come from the installed
core. Any Juggler-carrying tree launches cleanly under our claim, and this
machine has several sitting in ms-playwright.

No browser is launched here and nothing is downloaded: resolve_executable does
its whole job before a process exists.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from invisible_core.seal import (
    DEFAULT_ENTRY_REL,
    JUGGLER_ENTRIES,
    EngineMismatch,
    _reset_active_seal_for_tests,
    read_engine_identity,
)
from invisible_playwright import _engine

SEALED_VERSION = "151.0"
SEALED_BUILD = "20260724001949"
FOREIGN_BUILD = "20260611193205"
OLD_VERSION = "150.0.1"
OLD_BUILD = "20260624073725"

APP_INI = "[App]\nVendor=Mozilla\nName=Firefox\nVersion={v}\nBuildID={b}\n\n[Gecko]\nMinVersion={v}\nMaxVersion={v}\n"
PLAT_INI = "[Build]\nBuildID={b}\nMilestone={v}\n"

ENTRY_REL = DEFAULT_ENTRY_REL.get(sys.platform, "firefox")


def resources_of(root: Path) -> Path:
    if sys.platform == "darwin":
        return root / "Firefox.app" / "Contents" / "Resources"
    return root


def build_tree(root: Path, *, version: str = SEALED_VERSION, build_id: str = SEALED_BUILD,
               marked: int = 4) -> Path:
    res = resources_of(root)
    res.mkdir(parents=True, exist_ok=True)
    (res / "application.ini").write_text(APP_INI.format(v=version, b=build_id), encoding="utf-8")
    (res / "platform.ini").write_text(PLAT_INI.format(v=version, b=build_id), encoding="utf-8")
    with zipfile.ZipFile(res / "omni.ja", "w") as zf:
        for i, name in enumerate(JUGGLER_ENTRIES):
            zf.writestr(name, b"'zoom.stealth.hw_seed'" if i < marked else b"// vanilla")
    entry = root / ENTRY_REL
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"MZ\x90\x00")
    return entry


@pytest.fixture
def active(tmp_path, monkeypatch):
    """Install a known seal as the process's active seal, and take it back out.

    Deliberately NOT a monkeypatch of BINARY_VERSION: the audit's S7 showed that
    patching a constant which was bound as a default argument at import leaves
    __defaults__ untouched, so the gate printed PASS while exercising the
    unpatched path.
    """
    p = tmp_path / "firefox-18.seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": "firefox-18", "upstream_version": SEALED_VERSION,
        "build_id": SEALED_BUILD, "source_commit": "0123456789abcdef",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": {},
    }, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", str(p))
    _reset_active_seal_for_tests()
    yield p
    _reset_active_seal_for_tests()


def test_sealed_tree_is_accepted(tmp_path, active):
    entry = build_tree(tmp_path / "sealed")
    assert _engine.resolve_executable(entry) == entry
    assert _engine.resolve_executable(str(entry)) == entry


def test_unpatched_engine_via_binary_path_is_refused(tmp_path, active):
    """D1: a stock tree, same base version and same BuildID as the seal. Only
    the provenance marker separates them."""
    entry = build_tree(tmp_path / "stock", marked=0)
    with pytest.raises(EngineMismatch) as exc:
        _engine.resolve_executable(entry)
    msg = str(exc.value)
    assert "binary_path=" in msg, msg
    assert str(entry) in msg, msg


def test_old_engine_via_binary_path_is_refused(tmp_path, active):
    """D1 with the engine the audit measured: a 150.0.1 tree under a 151.0
    claim. The canvas PNG signature and four prototype members give a page a
    free discriminator, so this must never launch."""
    entry = build_tree(tmp_path / "old", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        _engine.resolve_executable(entry)
    assert OLD_VERSION in str(exc.value) and SEALED_VERSION in str(exc.value)


def test_foreign_build_of_the_same_version_via_binary_path_is_refused(tmp_path, active):
    entry = build_tree(tmp_path / "foreign", build_id=FOREIGN_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        _engine.resolve_executable(entry)
    assert FOREIGN_BUILD in str(exc.value) and SEALED_BUILD in str(exc.value)


def test_refusal_names_the_remedy(tmp_path, active):
    """The message is the whole product here: a refusal nobody can act on just
    moves the silence somewhere else.

    The remedy is the CORE's own command on purpose. It used to be
    `python -m invisible_playwright fetch --force`, written inside
    invisible_core.seal - a module the profile manager also imports, while not
    depending on this package at all. Half the readers of that refusal were
    handed a command they could not run. `doctor --fix` clears the cached tree
    and re-fetches the sealed one, and it exists wherever this message can be
    printed, so it is the only remedy that is true for both consumers.
    """
    entry = build_tree(tmp_path / "old2", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        _engine.resolve_executable(entry)
    msg = str(exc.value)
    assert "invisible_core doctor --fix" in msg, msg
    assert "invisible_playwright" not in msg, (
        "the shared core is naming a consumer's CLI again: this message also "
        "reaches invisible-firefox users, who do not have that package.\n" + msg)
    assert "INVISIBLE_SEAL_FILE" in msg, msg


def test_no_binary_path_goes_through_ensure_binary(tmp_path, active, monkeypatch):
    """The other arm of the same call site: with no path, the sealed download
    path runs (and is itself verified). Nothing is downloaded here."""
    sentinel = tmp_path / "from-ensure" / ENTRY_REL
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"MZ")
    calls = []

    def fake_ensure(*a, **kw):
        calls.append(kw.get("seal"))
        return sentinel

    monkeypatch.setattr(_engine, "ensure_binary", fake_ensure)
    assert _engine.resolve_executable(None) == sentinel
    assert calls and calls[0] is not None, "the resolved seal must be passed down"


def test_real_stock_firefox_via_binary_path_is_refused(active):
    """The cell as it exists on this machine, not a synthetic stand-in. Read
    only; skips cleanly when no bundled Playwright Firefox is present."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    roots = [Path(local) / "ms-playwright", Path.home() / ".cache" / "ms-playwright"]
    entries = [d / "firefox" / ENTRY_REL for root in roots if root.is_dir()
               for d in sorted(root.glob("firefox-*"))]
    entries = [e for e in entries if e.exists()]
    if not entries:
        pytest.skip("no bundled stock Firefox on this machine")
    for entry in entries:
        ident = read_engine_identity(entry)
        with pytest.raises(EngineMismatch):
            _engine.resolve_executable(entry)
        assert ident.marked_entries == 0, f"{entry} unexpectedly carries our marker"
