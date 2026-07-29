"""Command-line interface for invisible_playwright."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from invisible_core import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION
# cache_root is unused here since clear-cache stopped wiping the shared root,
# but it stays imported: it has been part of this module's surface since 0.1.
from invisible_core.download import cache_root, ensure_binary  # noqa: F401

from ._pin import (
    declared_core_pin as _declared_core_pin,
    installed_core_version as _installed_core_version,
    recorded_core_version as _recorded_core_version,
)


def _cmd_fetch(args: argparse.Namespace) -> int:
    # --force drops the cached engine tree(s) for the tag and lets ensure_binary
    # fetch it fresh. It removes engine directories only, never the cache root,
    # so the other product's engine and the shared geoip database survive.
    from invisible_core.download import clear_cache
    if getattr(args, "force", False):
        for d in clear_cache(args.tag):
            print(f"removed: {d}")
    print(ensure_binary(args.tag))
    return 0


def _cmd_path(_args: argparse.Namespace) -> int:
    try:
        path = ensure_binary()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(path)
    return 0


def _cmd_version(_args: argparse.Namespace) -> int:
    # Printing tag + base version + BuildID + seal digest is what makes a core
    # that lags behind the newest binary release visible on any machine.
    from invisible_core.seal import active_seal
    s = active_seal()
    # The core's version is read live from its seal, not from the installer's
    # record: an editable install freezes its dist-info at install time, so the
    # record is the one number guaranteed to be wrong on a developer machine.
    # This is the command users are asked to paste into a bug report, so the
    # declared pin is printed beside it and a disagreeing record is called out.
    core_v = _installed_core_version() or "unknown"
    recorded = _recorded_core_version()
    want = _declared_core_pin()
    print(f"invisible_playwright {__version__}")
    print(f"invisible_core       {core_v}" + (f"   (declared: =={want})" if want else ""))
    if recorded and recorded != core_v:
        print(f"                     install record says {recorded}  (STALE RECORD)")
    print(f"engine               {s.tag}  Firefox {s.upstream_version}  build {s.build_id}")
    print(f"seal                 {s.digest[:12]}  [{s.origin}]")
    return 0


def _cmd_clear_cache(args: argparse.Namespace) -> int:
    from invisible_core.download import clear_cache
    removed = clear_cache(args.tag, everything=args.all)
    for d in removed:
        print(f"removed: {d}")
    if not removed:
        print("nothing to remove")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from invisible_core.__main__ import main as core_main
    return core_main(["doctor"] + (["--deep"] if args.deep else []))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="invisible-playwright", description="invisible_playwright CLI")
    # Top-level `--version` / `-V` flag so `python -m invisible_playwright --version`
    # works (Python convention), in addition to the existing `version` subcommand.
    p.add_argument(
        "-V", "--version", action="version",
        version=f"invisible_playwright {__version__} (BINARY_VERSION={BINARY_VERSION}, Firefox {FIREFOX_UPSTREAM_VERSION})",
    )
    sub = p.add_subparsers(dest="cmd")

    fetch_p = sub.add_parser("fetch", help="download the patched Firefox binary")
    fetch_p.add_argument("tag", nargs="?", default=None,
                         help="engine tag (defaults to the tag this core is sealed to)")
    fetch_p.add_argument("--force", action="store_true",
                         help="drop the cached engine tree and re-download")
    sub.add_parser("path", help="print the absolute path to the cached binary")
    sub.add_parser("version", help="print wrapper, core and engine versions")
    clear_p = sub.add_parser("clear-cache", help="remove cached engine trees")
    clear_p.add_argument("--tag", default=None,
                         help="only this tag (defaults to the sealed tag)")
    clear_p.add_argument("--all", action="store_true",
                         help="every cached engine tree, not just the sealed one")
    doctor_p = sub.add_parser("doctor", help="report every cached engine against the seal")
    doctor_p.add_argument("--deep", action="store_true", help="also hash omni.ja")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        # argparse-conventional: print usage + error message to stderr, exit 2.
        # We can't keep `required=True` on the subparsers because that breaks
        # the top-level `--version` flag (argparse demands a subcommand even
        # when --version is the only token). parser.error() preserves the
        # original "no subcommand" exit semantics tests expect.
        parser.error("a subcommand is required (try --help, --version, or one of: fetch, path, version, clear-cache, doctor)")
    dispatch = {
        "fetch": _cmd_fetch,
        "path": _cmd_path,
        "version": _cmd_version,
        "clear-cache": _cmd_clear_cache,
        "doctor": _cmd_doctor,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
