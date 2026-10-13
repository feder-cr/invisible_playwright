#!/usr/bin/env python3
"""Run this repository's FULL e2e suite (every test that opens the browser)
against a binary: the mandatory pre-release e2e gate of a firefox-N.

The run itself - which tests, how many browsers at once, the rerun rules, the
deadline, and why each is what it is - lives ONCE, in
`invisible_core.testing.e2e`, for the three clients of the core. This file is
the entry the workflows and the documents call.

Usage:
    python scripts/run_e2e.py <firefox-binary>
    python scripts/run_e2e.py            # uses $INVPW_BINARY_PATH
    python scripts/run_e2e.py <binary> -n 8    # eight browsers at once
    python scripts/run_e2e.py <binary> -n 1    # the serial path, no xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

from invisible_core.testing.e2e import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], repo=Path(__file__).resolve().parent.parent))
