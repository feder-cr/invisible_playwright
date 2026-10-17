"""Every route from a chosen path to a spawned Firefox goes through here.

``binary_path=`` (and ``INVPW_BINARY_PATH``, which the test suite and the e2e
scripts turn into ``binary_path=``) never reaches ``ensure_binary()``, so none
of the download-side checks run on that route. The guard therefore sits on the
resolved executable, not inside the fetcher.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Optional, Union

from invisible_core import ensure_binary
from invisible_core.seal import EngineMismatch, active_seal, verify_engine


def resolve_executable(binary_path: Optional[Union[str, Path]]) -> Path:
    """``binary_path=`` skips the download path entirely, so the guard sits on the
    resolved executable rather than inside the fetcher.

    ⛔ This used to say "binary_path= and INVPW_BINARY_PATH", and the second half
    was false: nothing here reads that variable. It is translated into
    ``binary_path=`` by the test scripts and by ``run_e2e.py``, so it is a
    convention of this repo's harness and not a feature of the library. Corrected
    2026-08-14 - a docstring that promises an env var the code never reads sends
    the reader looking for a bug in the wrong function.

    Driving an engine the packaged seal does not describe is done with
    ``binary_path=`` plus ``INVISIBLE_SEAL_FILE`` pointing at a seal generated for
    it; without that, ``verify_engine`` refuses, and refusing is correct - the
    prefs and the spoofed User-Agent describe the sealed build.
    """
    seal = active_seal()
    if binary_path:
        return verify_engine(Path(binary_path), seal, source=f"binary_path={binary_path}")
    return ensure_binary(seal=seal)


def assert_wire_version(browser) -> None:
    """Compare what the engine reports over the protocol with the seal.

    browser.version is a cached property from the connection initializer
    (Juggler Browser.getInfo -> MOZ_APP_VERSION_DISPLAY), so this costs zero
    round trips and no pref can spoof it. It is the only check that also
    catches a hand-edited application.ini. Not available on the persistent
    context path, where Playwright exposes no Browser.
    """
    if browser is None:
        return
    seal = active_seal()
    raw = getattr(browser, "version", "")
    # Playwright types Browser.version as str. Anything else (a stub, a mock,
    # a driver that did not populate the initializer) carries no evidence
    # either way, and inventing a mismatch out of it would be a false alarm.
    if not isinstance(raw, str):
        return
    reported = raw.split("/")[-1].strip()
    if reported and reported != seal.upstream_version:
        raise EngineMismatch(
            "engine/seal mismatch after launch - the running browser is not the sealed build\n"
            f"  protocol says: Firefox {reported}\n"
            f"  seal says    : Firefox {seal.upstream_version} (tag {seal.tag}, "
            f"build {seal.build_id})\n"
            "  why          : application.ini can be edited; this value comes from the "
            "running engine itself.\n"
            "  fix          : python -m invisible_playwright fetch")


def assert_playwright_range() -> None:
    """The Juggler protocol schema is closed-world: a client outside the range
    the binary was tested with dies at context creation, or silently loses a
    feature. The range travels in the seal, so widening it does not need a
    wrapper release."""
    seal = active_seal()
    if not seal.playwright_min or not seal.playwright_max:
        return
    # The version of the VENDORED client, not of an installed package.
    #
    # Before the fork this line did `_pkg_version("playwright")` with an
    # `except PackageNotFoundError: return`. With the dependency removed, that
    # path would have become the ONLY path: the gate would have stopped
    # checking anything while still printing green, which is the exact way
    # nine workflows in this project were green while they were broken.
    #
    # The client now lives inside us and its version is a local fact, so
    # there is no longer any "absent" case to tolerate: if _repo_version
    # fails to import, the package is broken and must say so.
    from invisible_playwright._pw._repo_version import version as have

    def t(v: str):
        out = []
        for part in v.split(".")[:3]:
            digits = "".join(c for c in part if c.isdigit())
            out.append(int(digits or 0))
        return tuple(out + [0] * (3 - len(out)))

    if t(seal.playwright_min) <= t(have) <= t(seal.playwright_max):
        return
    # Spelled by the tool that installed this package (`invisible_core.installer`):
    # a written `pip install` is a command the venv the README makes does not have.
    from invisible_core.installer import install
    from invisible_core.pin import format_command

    fix = format_command(install("invisible-playwright", f"playwright=={seal.playwright_max}"))
    msg = (f"playwright {have} is outside the range this engine was tested with "
           f"({seal.playwright_min} .. {seal.playwright_max}, from the seal for {seal.tag}).\n"
           f"The Juggler protocol schema is closed-world: an untested client can kill every "
           f"session at context creation.\n"
           f"Run: {fix}   "
           f"(or set INVISIBLE_PLAYWRIGHT_SKEW=allow to proceed anyway)")
    if os.environ.get("INVISIBLE_PLAYWRIGHT_SKEW") == "allow":
        warnings.warn(msg, RuntimeWarning, stacklevel=2)
    else:
        raise RuntimeError(msg)
