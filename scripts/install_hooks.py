#!/usr/bin/env python3
"""Point this clone's git hooks at .githooks/, so the pre-push gates are armed.

Back-compat shim. The implementation is `invisible_core.hooks.install_main`,
moved there on 2026-07-27 because it existed here and therefore existed for ONE
of the three repositories: the other two shipped a hook that nothing armed, and
nothing checked was armed, so a fresh clone of either had no gates at all and no
way to notice. Same asymmetry, and the same fix, as the publish gate.

  python scripts/install_hooks.py            # arm this clone
  python scripts/install_hooks.py --check    # exit 1 if it is not armed

Nobody has to remember: a test in each repo fails in a checkout whose hooksPath
is not set, and prints this command. The suite is the reminder, and it runs on
its own.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# No src insert: this repo pins invisible-core exactly, so the shared
# implementation is simply importable.

from invisible_core.hooks import install_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(install_main(sys.argv[1:], root=ROOT))
