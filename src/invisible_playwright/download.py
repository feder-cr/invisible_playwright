"""Download and cache the patched Firefox binary from GitHub Releases."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import platformdirs
import requests

from .constants import (
    ARCHIVE_NAME,
    BINARY_ENTRY_REL,
    BINARY_VERSION,
    RELEASE_URL_TEMPLATE,
)


def _github_token() -> str | None:
    return os.environ.get("STEALTHFOX_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _parse_owner_repo(template: str) -> tuple[str, str]:
    """Extract (owner, repo) from RELEASE_URL_TEMPLATE."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/", template)
    if not m:
        raise RuntimeError(f"cannot parse owner/repo from {template!r}")
    return m.group(1), m.group(2)


def cache_root() -> Path:
    """Directory where all cached binaries live."""
    return Path(platformdirs.user_cache_dir("invisible-playwright"))


def cache_dir_for_version(version: str = BINARY_VERSION) -> Path:
    return cache_root() / version


def _resolve_asset_url(tag: str, asset_name: str) -> str:
    """Return a downloadable URL for the asset.

    For private repos the direct `releases/download/<tag>/<asset>` URL returns
    404 even with a token, so we resolve via the API: list assets for the
    release tag, find the one matching `asset_name`, and use its API URL with
    `Accept: application/octet-stream` (which 302-redirects to a signed URL).
    For public repos the direct URL still works without a token.
    """
    token = _github_token()
    if not token:
        return RELEASE_URL_TEMPLATE.format(tag=tag, asset=asset_name)
    owner, repo = _parse_owner_repo(RELEASE_URL_TEMPLATE)
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    r = requests.get(api, headers={"Authorization": f"token {token}"}, timeout=30)
    r.raise_for_status()
    for a in r.json().get("assets", []):
        if a.get("name") == asset_name:
            return a["url"]
    raise RuntimeError(f"asset {asset_name!r} not found in release {tag!r}")


def _download_file(url: str, dst: Path, chunk_size: int = 1 << 16) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    headers: dict[str, str] = {}
    token = _github_token()
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"token {token}"
        headers["Accept"] = "application/octet-stream"
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size):
                if chunk:
                    f.write(chunk)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_checksums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            # sha256sum uses ' *' or '  ' prefix for binary vs text mode
            key = parts[-1].lstrip("*")
            out[key] = parts[0]
    return out


def _extract(archive: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dst)
    elif archive.name.endswith(".tar.gz") or archive.suffix in {".tgz", ".gz"}:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dst)
    else:
        raise RuntimeError(f"unknown archive format: {archive}")


def ensure_binary(version: str = BINARY_VERSION) -> Path:
    """Return a path to a runnable Firefox executable. Download if needed."""
    plat = sys.platform
    mach = platform.machine()
    asset = ARCHIVE_NAME(plat, mach)
    entry_rel = BINARY_ENTRY_REL.get(plat)
    if entry_rel is None:
        raise NotImplementedError(f"no binary entry for platform {plat}")

    version_dir = cache_dir_for_version(version)
    entry = version_dir / entry_rel
    if entry.exists():
        return entry

    url_archive = _resolve_asset_url(version, asset)
    url_sums = _resolve_asset_url(version, "checksums.txt")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        archive_path = tmp / asset
        _download_file(url_archive, archive_path)
        sums_path = tmp / "checksums.txt"
        _download_file(url_sums, sums_path)
        sums = _parse_checksums(sums_path.read_text())
        expected = sums.get(asset)
        if expected is None:
            raise RuntimeError(f"no SHA256 for {asset} in checksums.txt")
        actual = _sha256_file(archive_path)
        if actual.lower() != expected.lower():
            raise RuntimeError(
                f"SHA256 mismatch for {asset}: got {actual}, expected {expected}"
            )
        _extract(archive_path, version_dir)

    if not entry.exists():
        raise RuntimeError(f"binary not found after extraction: {entry}")
    return entry
