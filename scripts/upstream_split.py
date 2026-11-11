"""Run only some of the tests of a file (loaded with `-p`).

A file whose tests wait for timeouts can take longer than a whole shard, so the
weights split it into parts and each part goes to a different shard.
`upstream_baseline.py` gives every shard that holds a part the table in
$INVPW_SPLIT: {file: {"parts": M, "keep": [parts to run], "map": {test: part}}}.
Every shard runs the whole file; this hook deselects the tests of the other
parts. A test the table does not know (a new one upstream) goes to the part
its name hashes to, so each test runs in exactly one shard and none is lost.
Without the variable it does nothing.
"""
from __future__ import annotations

import json
import os
import zlib


def pytest_collection_modifyitems(config, items) -> None:
    raw = os.environ.get("INVPW_SPLIT")
    if not raw:
        return
    spec = json.loads(raw)
    keep, dropped = [], []
    for item in items:
        file, _, key = item.nodeid.replace("\\", "/").partition("::")
        entry = spec.get(file)
        if entry is None:
            keep.append(item)
            continue
        part = entry["map"].get(key)
        if part is None:
            part = zlib.crc32(key.encode("utf-8")) % entry["parts"]
        (keep if part in entry["keep"] else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep
