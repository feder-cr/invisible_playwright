import subprocess
import sys


def test_version_subcommand():
    r = subprocess.run(
        [sys.executable, "-m", "stealthfox", "version"],
        capture_output=True, text=True, check=True,
    )
    assert "firefox-" in r.stdout
    assert "stealthfox" in r.stdout.lower()


def test_help_subcommand():
    r = subprocess.run(
        [sys.executable, "-m", "stealthfox", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "fetch" in r.stdout
    assert "path" in r.stdout
    assert "clear-cache" in r.stdout
