"""The wire form of a protocol message: JSON, and nothing a socket cannot carry.

Today the client and the server of this package pass each other Python dicts in the
same process (`transport.InProcessTransport`). That is why nothing has ever checked that
a message is JSON: a `bytes`, a tuple of objects, a NaN or an impl object can sit in one
and travel without a word. A message that goes through a WebSocket (`remote.py`) is a
string, so the first such value is a failure that shows up far from the line that put it
there.

`dumps` therefore refuses what JSON would change or lose, and names WHERE it is
(`$.params.payloads[0].buffer`), which is the one thing a bare `TypeError: Object of type
bytes is not JSON serializable` does not say:

  - `bytes`, `bytearray`, sets and any object that is not a plain value;
  - a key that is not a string (`json` would turn 1 into "1" and the reader would look
    up the wrong key);
  - NaN and the infinities (not JSON; Playwright's own protocol sends them as
    `{"v": "NaN"}` inside serialised values, never as a bare number).

A tuple is accepted: it is a JSON array and reads back as a list, which is what a client
on the other end of a socket would see anyway.

Playwright's own protocol carries binary as a base64 string, so a server that wants to
send bytes encodes them where it builds the message; it is never this module's job to
guess that.
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional


class WireError(TypeError):
    """A value in a message that JSON would change or lose. `path` says where."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__("%s: %s" % (path, reason))
        self.path = path
        self.reason = reason


def first_problem(value: Any, path: str = "$") -> Optional[WireError]:
    """The first value under `value` that is not plain JSON, or None."""
    if value is None or isinstance(value, (bool, str)):
        return None
    if isinstance(value, int):
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return WireError(path, "%r is not JSON" % value)
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return WireError("%s[%r]" % (path, key),
                                 "a key must be a string, not %s" % type(key).__name__)
            problem = first_problem(item, "%s.%s" % (path, key))
            if problem:
                return problem
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            problem = first_problem(item, "%s[%d]" % (path, index))
            if problem:
                return problem
        return None
    return WireError(path, "%s is not a JSON value" % type(value).__name__)


def dumps(message: Any) -> str:
    """The message as the text that crosses the wire. Raises `WireError`."""
    problem = first_problem(message)
    if problem:
        raise problem
    return json.dumps(message, separators=(",", ":"), allow_nan=False)


def loads(text: "str | bytes") -> Any:
    """The message a text carries."""
    return json.loads(text)


def round_trip(message: Any) -> Any:
    """What the other end of a socket would read: `loads(dumps(message))`."""
    return loads(dumps(message))
