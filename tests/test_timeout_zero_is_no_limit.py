"""`timeout=0` is no limit, as in Playwright, wherever the server reads a timeout.

⛔ THE KNOWN-BAD INPUT (B278): the server read every timeout as
`params.get("timeout") or 30000`, in four places, so `0`, which the Playwright
client sends when the caller writes `timeout=0`, became 30 s for an action and
60 s for a launch. One reader now, `_caller_timeout`, and None below it is the
core's `Deadline` of no limit.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler._marshal import _caller_timeout
from invisible_playwright._juggler.server import FrameDispatcher


@pytest.mark.unit
@pytest.mark.parametrize("params, default_ms, seconds", [
    ({"timeout": 0}, 30000, None),
    ({"timeout": 1500}, 30000, 1.5),
    ({}, 30000, 30.0),
    ({"timeout": None}, 30000, 30.0),
    ({"timeout": 0}, 60000, None),
    ({}, 60000, 60.0),
])
def test_the_caller_s_timeout_off_the_wire(params, default_ms, seconds):
    assert _caller_timeout(params, default_ms=default_ms) == seconds


@pytest.mark.unit
def test_an_action_s_timeout_of_zero_reaches_the_core_as_no_limit():
    assert FrameDispatcher._timeout(object(), {"timeout": 0}) is None
    assert FrameDispatcher._timeout(object(), {"timeout": 2000}) == 2.0
