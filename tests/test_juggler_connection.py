"""The Juggler connection in Python: talks to the binary without Node in
between.

⛔ It is marked `e2e` because it launches a real browser. There is no point
testing it any other way: what has to be shown is that the PIPE connects
and that the browser responds, and neither of those two things can be
simulated.
"""
from __future__ import annotations

import tempfile

import pytest

from invisible_playwright._juggler import connection as conn
from invisible_playwright._juggler.protocol import COMMANDS, EVENTS


# ── without a browser ───────────────────────────────────────────────────────

def test_the_generated_protocol_has_the_five_domains():
    """If the generator ingests a wrong Protocol.js, the count moves."""
    domains = {n.split(".")[0] for n in COMMANDS}
    assert domains == {"Browser", "Page", "Network", "Runtime", "Heap"}
    # ⛔ THESE TWO NUMBERS WERE 71 AND 34 WHILE THE PINNED ENGINE ALREADY
    # DECLARED 75 AND 35, and this test was green the whole time: it fixes
    # the count of the mirror, not the mirror's agreement with the engine, so
    # a mirror that stops being regenerated stays "right" forever. The four
    # commands it lacked (`Network.getResponseBody`, the three screencast
    # ones) and the one event were shipped by the engine and never mirrored.
    # What ties the mirror to the engine is `gen_juggler_protocol.py --check`
    # against the pinned binary, run where the binary is - the e2e job.
    assert len(COMMANDS) == 76, "commands: %d" % len(COMMANDS)
    assert len(EVENTS) == 35, "events: %d" % len(EVENTS)


def test_the_commands_the_client_will_use_are_declared():
    """The browser enforces the schema as a CLOSED WORLD: an undeclared
    command does not degrade, it REJECTS. These are the ones on the
    minimum path."""
    for name in ("Browser.enable", "Browser.createBrowserContext",
                 "Browser.newPage", "Page.navigate", "Runtime.evaluate",
                 # Asked after every click and hover since [B217]: an engine
                 # without it refuses the question, and the mirror must say so
                 # before a browser does.
                 "Page.pointerLanded"):
        assert name in COMMANDS, name


def test_every_type_uses_only_the_eight_known_combinators():
    """A new combinator in Protocol.js must make the generator REJECT,
    not slip through unnoticed.

    ⛔ The set below holds NINE names for eight combinators: `Object` is
    not a `t.` combinator, it is the structural kind the generator emits
    for a brace literal. The count in the function name is the number of
    things `PrimitiveTypes.js` declares and we accept, which is what a
    reader of this test cares about.
    """
    known = {"String", "Number", "Boolean", "Any", "Enum",
             "Nullable", "Optional", "Array", "Object"}
    seen: set = set()
    visited = 0

    def walk(t):
        nonlocal visited
        if not isinstance(t, dict):
            return
        visited += 1
        seen.add(t.get("k"))
        if "of" in t:
            walk(t["of"])
        for v in (t.get("fields") or {}).values():
            walk(v)

    for spec in COMMANDS.values():
        walk(spec.get("params"))
        walk(spec.get("returns"))
    for spec in EVENTS.values():
        walk(spec)
    # ⛔ THE COVERAGE ASSERTION COMES FIRST, and it exists because this
    # test nearly went blind in silence. The walk recurses through the keys
    # `of` and `fields`; when those two were renamed from `di` and `campi`,
    # `walk` stopped descending and `seen` collapsed to the handful of
    # top-level kinds - while the assertion below still PASSED, because a
    # smaller set is still a subset. A gate that checks less and says the
    # same thing is worse than one that fails.
    assert visited > 400, (
        "the walk only visited %d type nodes: it is not descending any "
        "more, so the assertion below proves almost nothing" % visited)
    assert seen <= known, "unexpected combinators: %s" % (seen - known)


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
