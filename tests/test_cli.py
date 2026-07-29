import subprocess
import sys
from pathlib import Path

import pytest

from invisible_playwright import cli


@pytest.mark.unit
def test_version_subcommand():
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright", "version"],
        capture_output=True, text=True, check=True,
    )
    assert "firefox-" in r.stdout
    assert "invisible_playwright" in r.stdout.lower()


@pytest.mark.unit
def test_help_subcommand():
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "fetch" in r.stdout
    assert "path" in r.stdout
    assert "clear-cache" in r.stdout


# CL1: clear-cache prints "removed:" once per engine tree it dropped.
# It removes ENGINE TREES, never the cache root itself: the root is shared with
# the profile manager and holds the geoip database, and wiping it took both.
@pytest.mark.unit
def test_clear_cache_with_existing_cache(tmp_path, monkeypatch, capsys):
    engine_dir = tmp_path / "firefox-18_151.0_20260724001949"
    engine_dir.mkdir()
    calls = {}

    def fake_clear_cache(tag=None, *, everything=False):
        calls["tag"], calls["everything"] = tag, everything
        return [engine_dir]

    monkeypatch.setattr("invisible_core.download.clear_cache", fake_clear_cache)

    rc = cli.main(["clear-cache"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("removed:")
    assert str(engine_dir) in captured.out
    assert calls == {"tag": None, "everything": False}


# CL2: clear-cache with nothing to drop prints "nothing to remove"
@pytest.mark.unit
def test_clear_cache_with_no_cache(monkeypatch, capsys):
    monkeypatch.setattr("invisible_core.download.clear_cache",
                        lambda tag=None, *, everything=False: [])

    rc = cli.main(["clear-cache"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("nothing to remove")


# CL2b: --all forwards `everything=True`; --tag forwards the tag. Without these
# the flags would parse and silently do nothing.
@pytest.mark.unit
def test_clear_cache_flags_are_forwarded(monkeypatch, capsys):
    calls = {}

    def fake_clear_cache(tag=None, *, everything=False):
        calls["tag"], calls["everything"] = tag, everything
        return []

    monkeypatch.setattr("invisible_core.download.clear_cache", fake_clear_cache)

    assert cli.main(["clear-cache", "--tag", "firefox-14", "--all"]) == 0
    assert calls == {"tag": "firefox-14", "everything": True}


# CL3: path when binary exists prints path, exit 0
@pytest.mark.unit
def test_path_subcommand_when_binary_exists(tmp_path, monkeypatch, capsys):
    fake_binary = tmp_path / "firefox.exe"
    fake_binary.write_text("x")
    monkeypatch.setattr("invisible_playwright.cli.ensure_binary", lambda: fake_binary)

    rc = cli.main(["path"])

    captured = capsys.readouterr()
    assert rc == 0
    assert str(fake_binary) in captured.out
    assert captured.err == ""


# CL4: path when binary missing prints to stderr, exit 1
@pytest.mark.unit
def test_path_subcommand_when_binary_missing(monkeypatch, capsys):
    def boom():
        raise RuntimeError("download failed")
    monkeypatch.setattr("invisible_playwright.cli.ensure_binary", boom)

    rc = cli.main(["path"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "error:" in captured.err
    assert "download failed" in captured.err
    assert captured.out == ""


# CL5: no subcommand → argparse error, exit != 0
@pytest.mark.unit
def test_no_subcommand_errors():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code != 0


# CL6: unknown subcommand → argparse error
@pytest.mark.unit
def test_unknown_subcommand_errors():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["bogus"])
    assert exc_info.value.code != 0


# Extra: fetch happy path with mocked ensure_binary. `fetch` takes an optional
# positional tag now, so ensure_binary is called with it (None = the sealed tag).
@pytest.mark.unit
def test_fetch_subcommand_prints_path(tmp_path, monkeypatch, capsys):
    fake_binary = tmp_path / "firefox.exe"
    fake_binary.write_text("x")
    seen = []

    def fake_ensure_binary(tag=None):
        seen.append(tag)
        return fake_binary

    monkeypatch.setattr("invisible_playwright.cli.ensure_binary", fake_ensure_binary)

    rc = cli.main(["fetch"])

    captured = capsys.readouterr()
    assert rc == 0
    assert str(fake_binary) in captured.out
    assert seen == [None]


# Extra: `fetch --force` drops the cached engine trees first, then downloads.
# Without the clear_cache call, --force was a no-op on a warm cache.
@pytest.mark.unit
def test_fetch_force_clears_before_download(tmp_path, monkeypatch, capsys):
    fake_binary = tmp_path / "firefox.exe"
    fake_binary.write_text("x")
    dropped = tmp_path / "firefox-18_151.0_20260724001949"
    order = []

    monkeypatch.setattr("invisible_core.download.clear_cache",
                        lambda tag=None, *, everything=False: (order.append("clear"), [dropped])[1])
    monkeypatch.setattr("invisible_playwright.cli.ensure_binary",
                        lambda tag=None: (order.append("fetch"), fake_binary)[1])

    rc = cli.main(["fetch", "--force"])

    captured = capsys.readouterr()
    assert rc == 0
    assert order == ["clear", "fetch"]
    assert f"removed: {dropped}" in captured.out
    assert str(fake_binary) in captured.out
