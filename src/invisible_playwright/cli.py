"""Command-line interface for invisible_playwright.

TWO COMMANDS, and that is the whole surface.

It was six: fetch, fetch --force, path, version, clear-cache, doctor. Every one
of them was a step somebody had to know to take, in a package whose entire
promise is that the browser handles itself. The ones that are gone did not lose
their behaviour, they lost their command:

  doctor       every cached engine is checked against the seal by `fetch`, on
               every run. It was the thing most worth doing and the thing least
               likely to be typed, which is the worst combination a subcommand
               can have.
  --force      unnecessary once `fetch` verifies: a tree that does not match the
               seal is replaced because it does not match, not because a flag was
               passed.
  path         `fetch` prints the resolved path as its last line, so
               `$(invisible-playwright fetch)` is the scripting form and it also
               guarantees the thing it names actually exists. `path` promised a
               path and could hand back one to a tree that was wrong.
  clear-cache  deliberately NOT folded in. The cache root is shared with
               invisible_firefox, so pruning "trees no seal points at" would
               delete the other product's engine on a machine running both.
               `version` prints the cache location; removing a directory is a
               thing a person can do without a subcommand for it.

The `tag` argument went with them. The seal decides which engine this build runs
and `verify_engine` refuses anything else, so a tag on the command line could
only ever name something that would then be rejected.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from invisible_core import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION
from invisible_core.download import cache_root, ensure_binary  # noqa: F401

from ._pin import (
    declared_core_pin as _declared_core_pin,
    installed_core_version as _installed_core_version,
    recorded_core_version as _recorded_core_version,
)


def _cmd_fetch(_args: argparse.Namespace) -> int:
    """Make the engine present and correct, then say where it is.

    The check runs BEFORE the download, not after, and that ordering is the
    point: a cached tree that no longer matches the seal is the case worth
    catching, and it is invisible to a "download if missing" that only looks at
    whether a file exists.
    """
    from invisible_core.download import iter_cached_engines
    from invisible_core.seal import active_seal, engine_problems

    seal = active_seal()
    bad = []
    for directory, ident in iter_cached_engines():
        if ident is None:
            bad.append((directory.name, ["no readable engine (incomplete tree?)"]))
            continue
        problems = engine_problems(ident, seal)
        if problems:
            bad.append((directory.name, list(problems)))

    for name, problems in bad:
        print(f"cached engine does not match the seal: {name}", file=sys.stderr)
        for problem in problems:
            print(f"    {problem}", file=sys.stderr)
    if bad:
        # Not fatal, and not something to ask about. ensure_binary refuses a
        # mismatching tree by construction and fetches the sealed one, so
        # reporting it and carrying on IS the repair. A tree left over from an
        # older seal is the ordinary case here, not a fault.
        print(f"{len(bad)} cached tree(s) will not be used; the sealed engine is "
              f"{seal.tag}", file=sys.stderr)

    try:
        path = ensure_binary()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    # Last line, alone, so `$(invisible-playwright fetch)` is a path.
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
    # Where the engine lives, because `clear-cache` is gone and "how do I get
    # the disk space back" needs an answer that is not a subcommand.
    print(f"cache                {cache_root()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="invisible-playwright", description="invisible_playwright CLI")
    # Top-level `--version` / `-V` flag so `python -m invisible_playwright --version`
    # works (Python convention), in addition to the existing `version` subcommand.
    p.add_argument(
        "-V", "--version", action="version",
        version=f"invisible_playwright {__version__} (BINARY_VERSION={BINARY_VERSION}, Firefox {FIREFOX_UPSTREAM_VERSION})",
    )
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("fetch",
                   help="download the engine if missing, check every cached one "
                        "against the seal, print the path")
    sub.add_parser("version", help="print wrapper, core and engine versions")
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
        parser.error("a subcommand is required (try --help, --version, or one of: fetch, version)")
    dispatch = {
        "fetch": _cmd_fetch,
        "version": _cmd_version,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
