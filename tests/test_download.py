import hashlib
from pathlib import Path

import pytest
import responses

from stealthfox.download import ensure_binary
from stealthfox.constants import BINARY_VERSION


def _make_zip(path: Path, inner_name: str, payload: bytes) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_name, payload)
    data = buf.getvalue()
    path.write_bytes(data)
    return data


@responses.activate
def test_ensure_binary_downloads_and_verifies(tmp_path, monkeypatch):
    """Full path: cache miss -> HTTP GET -> SHA256 check -> extract -> return path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("stealthfox.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.zip"
    archive_bytes = _make_zip(archive_path, "firefox.exe", b"PEX!")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from stealthfox.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("win32", "AMD64")

    url_archive = f"https://github.com/P0st3rw-max/stealthfox/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/P0st3rw-max/stealthfox/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200,
                  content_type="application/zip")
    responses.add(responses.GET, url_sums,
                  body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "win32")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    path = ensure_binary()
    assert Path(path).exists()
    assert Path(path).name == "firefox.exe"


@responses.activate
def test_ensure_binary_rejects_sha_mismatch(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr("stealthfox.download.cache_root", lambda: cache)
    archive_path = tmp_path / "archive.zip"
    archive_bytes = _make_zip(archive_path, "firefox.exe", b"PEX!")
    wrong_sha = "0" * 64
    from stealthfox.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("win32", "AMD64")

    url_archive = f"https://github.com/P0st3rw-max/stealthfox/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/P0st3rw-max/stealthfox/releases/download/{BINARY_VERSION}/checksums.txt"
    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{wrong_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "win32")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    with pytest.raises(RuntimeError, match="SHA256"):
        ensure_binary()
