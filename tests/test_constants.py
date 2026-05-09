from stealthfox.constants import BINARY_VERSION, BINARY_BASENAME, ARCHIVE_NAME


def test_binary_version_format():
    assert BINARY_VERSION.startswith("firefox-")
    assert BINARY_VERSION.split("-", 1)[1].isdigit()


def test_archive_name_windows():
    name = ARCHIVE_NAME("win32", "AMD64")
    assert name.endswith(".zip")
    assert "win-x86_64" in name


def test_archive_name_linux():
    name = ARCHIVE_NAME("linux", "x86_64")
    assert name.endswith(".tar.gz")
    assert "linux-x86_64" in name


def test_archive_name_unsupported_raises():
    import pytest
    with pytest.raises(NotImplementedError):
        ARCHIVE_NAME("darwin", "arm64")


def test_binary_basename_format():
    assert "firefox" in BINARY_BASENAME.lower()
    assert "stealth" in BINARY_BASENAME.lower()
