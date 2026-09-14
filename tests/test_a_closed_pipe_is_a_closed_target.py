"""The browser being gone is one fact, and it reaches a caller as one type.

Until 0.15.0 a disposed object raised `TargetClosedError` from the dispatcher
while a closed pipe came out of the connection as a nameless error, which the
client turned into the fork's plain `Error` with `the pipe closed` in it. A
caller that had to tell "the page refused" from "the browser died" was left
matching sentences. Measured 2026-09-14 on the AIHawk server's retry.

No browser starts here: the connection is built on an `os.pipe()` pair and
the browser's end is closed by the test.

Known-bad, run before this file was trusted: drop the `name` from the box
the reader leaves for a waiter (the first test goes red); raise
`ProtocolError` again on a pipe that is already closed (the second goes
red); leave the dispatcher with a class of its own (the third goes red);
export nothing from `async_api` (the fourth goes red).
"""
from __future__ import annotations

import os
import threading

import pytest

from invisible_playwright._juggler import connection, dispatcher
from invisible_playwright._juggler.connection import Connection, TargetClosedError


def _pipes():
    """A connection whose browser end the test holds: (connection, the
    browser's write end, the browser's read end)."""
    ours_r, theirs_w = os.pipe()      # the browser writes, we read
    theirs_r, ours_w = os.pipe()      # we write, the browser reads
    conn = Connection(to_browser=ours_w, from_browser=ours_r)
    return conn, theirs_w, theirs_r


def test_a_pipe_that_closes_while_a_call_waits_is_a_closed_target():
    conn, theirs_w, theirs_r = _pipes()
    caught: list = []

    def call():
        try:
            conn.send("Browser.getInfo", timeout=5)
        except BaseException as failure:      # noqa: BLE001 - the type is the point
            caught.append(failure)

    t = threading.Thread(target=call)
    t.start()
    os.close(theirs_w)                        # the browser goes away
    t.join(timeout=5)
    os.close(theirs_r)
    assert not t.is_alive(), "the waiter was left to the timeout"
    assert caught and isinstance(caught[0], TargetClosedError), caught
    assert "the pipe closed" in str(caught[0])


def test_a_call_on_a_pipe_already_closed_is_a_closed_target():
    conn, theirs_w, theirs_r = _pipes()
    os.close(theirs_w)
    deadline = threading.Event()
    for _ in range(200):
        if conn._closed:
            break
        deadline.wait(0.01)
    os.close(theirs_r)
    assert conn._closed, "the reader did not notice the pipe closing"
    with pytest.raises(TargetClosedError, match="the pipe is closed"):
        conn.send("Browser.getInfo", timeout=1)


def test_the_dispatcher_and_the_connection_raise_the_same_class():
    """One class for one fact. The dispatcher used to define its own with the
    same name, which the wire translated correctly and Python did not."""
    assert dispatcher.TargetClosedError is connection.TargetClosedError


def test_the_public_api_exports_the_type_a_caller_catches():
    from invisible_playwright import async_api, sync_api
    from invisible_playwright._pw._impl._errors import TargetClosedError as forks

    assert async_api.TargetClosedError is forks
    assert sync_api.TargetClosedError is forks
    assert "TargetClosedError" in async_api.__all__
    assert "TargetClosedError" in sync_api.__all__


def test_a_closed_pipe_reaches_the_client_as_the_forks_closed_target():
    """The translation the client applies to what the connection raises: the
    NAME of the class is what `parse_error` reads, so the connection's class
    has to be called exactly this."""
    from invisible_playwright._juggler.transport import _translate_exception
    from invisible_playwright._pw._impl._errors import is_target_closed_error

    translated = _translate_exception(TargetClosedError("the pipe is closed: "))
    assert is_target_closed_error(translated), type(translated)
