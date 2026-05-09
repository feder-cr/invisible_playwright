"""Compile-time constants that pin the wrapper to a specific Firefox build.

BINARY_VERSION is bumped every time new Firefox patches are released. It is
deliberately decoupled from the Python package version so that pure-Python
bugfixes don't force a multi-hour Firefox rebuild.
"""
from __future__ import annotations

# Bump this when a new patched Firefox build is released on GitHub.
BINARY_VERSION: str = "firefox-1"

# Underlying Firefox version (for display only; does not drive downloads).
FIREFOX_UPSTREAM_VERSION: str = "150.0.1"

# The base filename prefix used inside archives.
BINARY_BASENAME: str = f"firefox-{FIREFOX_UPSTREAM_VERSION}-stealth"


def ARCHIVE_NAME(platform_key: str, machine: str) -> str:
    """Return the platform-specific archive filename.

    platform_key: sys.platform ("win32", "linux")
    machine:      platform.machine() ("AMD64", "x86_64", ...)
    """
    pk = platform_key.lower()
    m = machine.lower()
    if m in {"amd64", "x86_64"}:
        arch = "x86_64"
    else:
        raise NotImplementedError(f"unsupported arch: {machine}")

    if pk == "win32":
        return f"{BINARY_BASENAME}-win-{arch}.zip"
    if pk == "linux":
        return f"{BINARY_BASENAME}-linux-{arch}.tar.gz"
    raise NotImplementedError(f"unsupported platform: {platform_key}")


# Binary entry point relative path inside the extracted archive root.
BINARY_ENTRY_REL = {
    "win32": "firefox.exe",
    "linux": "firefox",
}

# GitHub release URL template. The "TODO" owner is resolved at publication time.
RELEASE_URL_TEMPLATE = (
    "https://github.com/P0st3rw-max/stealthfox/releases/download/{tag}/{asset}"
)
