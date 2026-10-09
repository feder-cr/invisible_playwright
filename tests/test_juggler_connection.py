"""The Juggler connection in Python: talks to the binary without Node in
between.

The mirror's own tests, which need no browser, are the core's
(`tests/test_juggler_protocol_mirror.py`) since 0.30.0, with the client.

⛔ It is marked `e2e` because it launches a real browser. There is no point
testing it any other way: what has to be shown is that the PIPE connects
and that the browser responds, and neither of those two things can be
simulated.
"""
from __future__ import annotations

import tempfile

import pytest

from invisible_core.juggler import connection as conn


# ── with a browser ──────────────────────────────────────────────────────────

@pytest.mark.e2e
def test_python_talks_to_juggler_without_node(firefox_binary):
    """The proof the whole split rests on: pipe, readiness, commands,
    events.

    Uses no Playwright, no Node, no driver: only `connection.py` and the
    profile that `invisible_core` knows how to prepare.
    """
    from invisible_core.launch import build_launch_plan

    profile_dir = tempfile.mkdtemp(prefix="juggler_pipe_")
    plan = build_launch_plan(42, profile_dir=profile_dir, binary_path=firefox_binary,
                              timezone="UTC", locale="en-US")

    c = conn.launch(firefox_binary, profile_dir, headless=True,
                     env=plan.env, ready_timeout=60.0)
    events: list = []
    # ⛔ THREE arguments, not two. `_deliver` calls the handler with
    # (method, params, sessionId), and a two-argument lambda raised
    # TypeError on EVERY event: the list below stayed empty and this test
    # could never pass. It went unnoticed because it is marked e2e, so the
    # default selection deselects it, and because `_deliver` used to
    # swallow the exception without a trace.
    c.add_listener(lambda method, params, session: events.append(method))
    try:
        assert c.ready_seen, (
            "the 'Juggler listening to the pipe' line never arrived. "
            "It comes from a dump() call that a MOZILLA_OFFICIAL build "
            "silences: see 30-upstream-playwright-patches.md")

        # Browser.enable declares no `returns`: the response is None,
        # and that is fine.
        c.send("Browser.enable", {"attachToDefaultContext": True},
               timeout=30)

        ctx = c.send("Browser.createBrowserContext",
                     {"removeOnDetach": True})
        assert ctx and ctx.get("browserContextId"), ctx

        page = c.send("Browser.newPage",
                      {"browserContextId": ctx["browserContextId"]})
        assert page and page.get("targetId"), page

        # An event arrived: the pipe also carries unsolicited traffic,
        # not just responses.
        # ⛔ THIS ASSERTION FIRST: an empty event list means one of two
        # completely different things - the browser sent nothing, or our
        # handler is broken - and until `handler_errors` existed they
        # looked identical from here.
        assert not c.handler_errors, (
            "the event handler raised, so the list below says nothing "
            "about the browser: %s" % c.handler_errors)
        assert "Browser.attachedToTarget" in events, events
    finally:
        c.close()


@pytest.mark.e2e
def test_an_invented_command_is_REJECTED_not_ignored(firefox_binary):
    """The known-bad input for the connection.

    `checkScheme` is closed-world: if a nonexistent command came back as
    silence instead of an error, every protocol drift would turn into a
    mute timeout instead of a line that names the problem.
    """
    from invisible_core.launch import build_launch_plan

    profile_dir = tempfile.mkdtemp(prefix="juggler_male_")
    plan = build_launch_plan(7, profile_dir=profile_dir, binary_path=firefox_binary,
                              timezone="UTC", locale="en-US")
    c = conn.launch(firefox_binary, profile_dir, headless=True,
                     env=plan.env, ready_timeout=60.0)
    try:
        c.send("Browser.enable", {"attachToDefaultContext": True},
               timeout=30)
        with pytest.raises(conn.ProtocolError) as error:
            c.send("Browser.madeUpCommand", {}, timeout=10)
        # The message must come from the BROWSER and name the command,
        # not be one of our generic timeouts.
        assert "madeUpCommand" in str(error.value)
        assert "no response" not in str(error.value), (
            "the browser stayed silent instead of rejecting: %s"
            % error.value)
    finally:
        c.close()
