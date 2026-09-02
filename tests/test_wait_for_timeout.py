"""`wait_for_timeout` has to actually wait.

Measured 2026-09-02: it did not. A request for 2000ms returned in 1ms, in both
the sync and the async API, and in every caller of either, for as long as this
package has had its own driver. Nothing raised, so nothing showed.

WHERE the defect was is the interesting part, because the obvious answer is the
wrong one. The client sends `{"waitTimeout": timeout}`, which looks like a
typo and is not: upstream Playwright's protocol declares this one parameter
under its own name, and the vendored client is byte-identical to upstream on
that line. `timeout` is the RESERVED key on this channel - every other call
carries a per-call action timeout under it - which is exactly why the wait got
a name of its own.

The defect was in `_juggler/server.py`, the driver this project wrote to replace
the Node one: `op_wait_for_timeout` read `params.get("timeout")`, a key the
client never sends for this call, so the sleep was always zero.

The first attempt at this fix patched the vendored client instead, to make it
send the key the server happened to read. That is a patch at the symptom: it
leaves the defect in the code this project owns, it would be undone by the next
re-vendor of upstream, and it committed a false claim about the protocol into a
comment, a changelog and a test. It was found by review before it shipped.

What it cost, before it was found. A corpus run against real sites reported
three of them as blocked by three different anti-bot products, one with the
challenge markup sitting in the document; all three serve their real page once
the wait exists, so those verdicts were artefacts of the tool. A fourth site was
reported as never loading and loads in twenty seconds. In the MCP package a
polling loop of sixty one-second waits ran to completion instantly, so a test
with a sixty-second budget failed in fourteen for no reason anybody could see.

Two tests, and they are not the same test. The first pins the key the SERVER
reads, without a browser, so it runs on every push. The second measures elapsed
time against a real engine, because a name that looks right and a wait that
happens are still two different claims.
"""
from __future__ import annotations

import inspect
import re
import time

import pytest


def test_the_server_reads_the_key_the_client_actually_sends():
    """The two halves of one call, checked against each other rather than
    against anybody's memory of the protocol.

    Comments are stripped first: the comment beside the fix names the wrong key
    on purpose, to say why it must not come back, and a check that read it would
    go red over its own documentation.
    """
    from invisible_playwright._juggler.server import FrameDispatcher
    from invisible_playwright._pw._impl._frame import Frame

    sent = re.sub(r"#[^\n]*", "", inspect.getsource(Frame.wait_for_timeout))
    read = re.sub(r"#[^\n]*", "", inspect.getsource(FrameDispatcher.op_wait_for_timeout))

    assert '"waitTimeout"' in sent, (
        "the vendored client no longer sends waitTimeout; it is upstream "
        "Playwright's own parameter name and should not have been changed")
    assert '"waitTimeout"' in read, (
        "the driver is reading a key the client does not send for this call, "
        "so the sleep is always zero and reports success")
    assert '"timeout"' not in read, (
        "the driver is reading `timeout`, which is the reserved per-call action "
        "timeout on this channel and not the wait duration")


@pytest.mark.e2e
@pytest.mark.parametrize("requested", [1000, 3000])
def test_the_wait_takes_the_time_it_was_asked_for(requested):
    """A key that matches and a wait that happens are two claims.

    The floor is generous on purpose: this asserts that time passed at all,
    which is what was broken, not that the timer is precise. Before the fix a
    request for 2000ms returned in 1ms, so anything above a small fraction of
    the request separates working from not.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=1, headless=True) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("data:text/html,<p>wait</p>")
        started = time.time()
        page.wait_for_timeout(requested)
        elapsed_ms = (time.time() - started) * 1000
        ctx.close()

    assert elapsed_ms >= requested * 0.8, (
        f"asked to wait {requested}ms and returned after {elapsed_ms:.0f}ms")
