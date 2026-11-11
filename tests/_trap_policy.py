"""When the resolver trap is mandatory (the gate's Linux leg of the DoH suite) a trap that cannot run is a failure.

`INVPW_REQUIRE_TRAP=1` is set by the gate on the leg where the trap can run (Linux, root). With it, an unavailable
trap or a port 53 that cannot be bound fails the test with the reason, instead of letting the assertions on the
machine's resolver be skipped in silence. Without it (a developer's machine) the old behaviour stays.
"""
from __future__ import annotations

import os

ENV = "INVPW_REQUIRE_TRAP"


def required(environ=None) -> bool:
    return (os.environ if environ is None else environ).get(ENV) == "1"


def failure(environ, available: bool, reason: str = "", bind_error=None):
    """-> the message to fail with, or None when the test may go on (with or without a trap)."""
    if not required(environ):
        return None
    if not available:
        return "%s=1 but the resolver trap cannot run here: %s" % (ENV, reason or "no reason given")
    if bind_error is not None:
        return "%s=1 but the resolver trap could not bind its port: %s" % (ENV, bind_error)
    return None
