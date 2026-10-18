"""The seam where Node stops being necessary.

⛔ THIS IS THE KEYSTONE OF THE WHOLE DETACHMENT, and its shape is the one
decision that makes the rest small. `_pw/_impl` is not replaced - 799 of its
methods never touch the channel at all, and throwing them away would mean
rewriting working logic for nothing. What is replaced is the ONE thing under
them: `PipeTransport`, which spawns `node cli.js run-driver` and speaks the
Playwright protocol over a pipe.

The seam is `Transport`, an abstract class with five methods. Everything above
it - channels, guids, `ChannelOwner`, the sync and async facades, the 823
generated API methods - keeps working unchanged, because from up there nothing
can tell whether the answer crossed a pipe or came from the next object in the
same process.

**THE PROTOCOL, MEASURED AND NOT DEDUCED.** What crosses this seam is recorded
by `scripts/capture_protocol.py` against the real Node driver, and the server is
written against that recording. Reading `_impl` tells you which initializer
fields are CONSUMED; it cannot tell you which are SENT, and the gap between
those two sets is exactly where a future code path breaks.

Four message shapes, in both directions:

    ->  {"id": N, "guid": G, "method": M, "params": P, "metadata": {...}}
    <-  {"id": N, "result": R}                     an answer
    <-  {"id": N, "error": {"error": {...}}}       a failure
    <-  {"guid": G, "method": "__create__",
         "params": {"type": T, "guid": G2, "initializer": I}}
    <-  {"guid": G, "method": M, "params": P}      an event

⛔ **THREADS, AND WHY THIS IS NOT OPTIONAL.** `send()` is called ON THE ASYNCIO
LOOP. Our Juggler operations block: `goto` waits for a load state, `click` waits
for actionability. Running them inline would block the loop, and under the sync
facade - which drives the loop from a greenlet - that is a deadlock, not a
slowdown. So `send()` only enqueues; a small pool of worker threads runs the
operations and posts every reply back with `loop.call_soon_threadsafe`, which is
the only way to touch the loop from another thread.

⛔ **AND ORDERING IS NOT GUARANTEED, WHICH IS CORRECT.** Replies are matched by
`id`, never by arrival order, so a slow `goto` cannot hold up a `title()` on
another page. What must stay ordered is `__create__` before any message that
names the guid it creates, and that is the dispatcher's job, not the pool's.
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
import traceback
from typing import Any, Dict, Optional

from invisible_playwright._pw._impl._helper import parse_error
from invisible_playwright._pw._impl._transport import Transport


def _error_payload(failure: BaseException) -> Dict[str, str]:
    """The `{name, message, stack}` shape both an error REPLY and a
    directly-translated exception are built from - one function, so the two
    do not drift the way rule 16 forbids.

    ⛔ EVERY TimeoutError IS NAMED "TimeoutError", whatever its subclass. The
    client picks its class by that exact name (`parse_error`), and the name
    used to be the subclass's own: a click whose element never became
    actionable raised the core's `ElementNotActionable`, and the caller got a
    plain Error where Playwright raises TimeoutError, so `except TimeoutError`
    around a click caught nothing (found 2026-10-11 with the launch timeout,
    which is the core's `ReplyTimeout`). The subclass is still in the stack."""
    name = type(failure).__name__
    if isinstance(failure, TimeoutError):
        name = "TimeoutError"
    return {
        "name": name,
        "message": str(failure) or type(failure).__name__,
        "stack": "".join(traceback.format_exception(
            type(failure), failure, failure.__traceback__))[-4000:],
    }


def _translate_exception(failure: BaseException) -> BaseException:
    """A `_juggler`-side exception, reshaped into the fork's own class - the
    same translation `reply_error` puts on the wire, taken directly instead of
    round-tripping through a message."""
    return parse_error(_error_payload(failure))


def _settle_result(future: asyncio.Future, result: Any) -> None:
    """⛔ Guarded: a future the caller already cancelled must not be resolved
    again - `asyncio.Future.set_result` on a cancelled future raises, and this
    runs on the loop where that exception would have nowhere useful to go."""
    if not future.done():
        future.set_result(result)


def _settle_exception(future: asyncio.Future, failure: BaseException) -> None:
    if not future.done():
        future.set_exception(failure)


class InProcessTransport(Transport):
    """A `Transport` whose other end is a Python object, not a subprocess."""

    #: ⛔ More than one, because a blocking operation must not hold up the
    #: others, and exactly this few because our Juggler connection serialises
    #: on its own lock anyway: a bigger pool would only add contention.
    WORKERS = 4

    def __init__(self, loop: asyncio.AbstractEventLoop, server: Any) -> None:
        super().__init__(loop)
        self._server = server
        self._queue: queue.Queue = queue.Queue()
        self._stopped = threading.Event()
        self._stopped_future: asyncio.Future = loop.create_future()
        self._threads: list = []
        server.attach(self)

    # ── the fused-type escape hatch ──────────────────────────────────────────
    def bind_impl_objects(self, objects: Dict[str, Any],
                          deliver_event: Any) -> None:
        """Give the server a live view of the CLIENT's guid registry, and its
        way to deliver an event under the correct execution context.

        ⛔ THE ONLY BRIDGE BETWEEN THE TWO GRAPHS. `objects` is a REFERENCE,
        not a copy: the client mutates it in place as it creates and disposes
        things, so this always sees the CURRENT state with nothing to go
        stale. It exists because a FUSED type - one with no `__create__` and no
        guid of its own - still needs to reach the live impl-side object a
        SIBLING guid corresponds to (a Dialog needs its Page's twin, to find
        the BrowserContext to notify).

        ⛔ AND `deliver_event` IS NOT OPTIONAL EITHER, found by an actual
        dialog hanging rather than by the unit suite. `Connection.dispatch`
        wraps every wire event in an `EventGreenlet` under the sync facade, so
        a handler that calls an async method (`dialog.accept()`) can suspend
        into it and resume. A fused type delivers its event directly, never
        through `dispatch`, so without this it never gets that wrapping - and
        the handler hangs with no exception, because it suspended into a fiber
        with nobody on the other end of the switch. Dialog is the first user
        of both, 2026-08-29.
        """
        self._server.bind_twins(objects, deliver_event)

    def run_blocking(self, fn: Any, *args: Any) -> asyncio.Future:
        """Run `fn(*args)` on this transport's OWN worker pool; return an
        awaitable that resolves on the LOOP once it is done.

        ⛔ THE SAME POOL AS EVERY CHANNEL MESSAGE, on purpose: a fused type's
        blocking call and a channel-routed call must never be able to starve
        each other by running on separate pools with separate limits. `_work`
        below accepts a plain callable exactly as it accepts a message dict,
        from the SAME queue, serviced by the SAME four threads.

        ⛔ AND THE EXCEPTION IS TRANSLATED, not passed through raw. `fn` runs
        `_juggler` code and can raise `_juggler.dispatcher.TargetClosedError` -
        a DIFFERENT CLASS than `_pw._impl._errors.TargetClosedError` with the
        SAME NAME. A fused type's own exception handling
        (`is_target_closed_error`) checks the FORK's class by `isinstance`, so
        a raw `_juggler` exception reaching it would look like an ordinary
        error and never be swallowed - reproducing, through this new path, the
        exact regression fixed on 2026-08-29 in `Page.close()`. The
        translation below goes through `parse_error`, the same one the
        message-envelope path already uses: not a second way of doing it, the
        one way, reused.
        """
        future: asyncio.Future = self._loop.create_future()

        def job() -> None:
            try:
                result = fn(*args)
            except Exception as failure:
                translated = _translate_exception(failure)
                self._loop.call_soon_threadsafe(_settle_exception, future,
                                                translated)
            else:
                self._loop.call_soon_threadsafe(_settle_result, future, result)

        self._queue.put(job)
        return future

    def call_soon(self, fn: Any, *args: Any) -> None:
        """Hand a call to the LOOP thread, fire-and-forget - the
        construction half of what `run_blocking` does for a call whose
        RESULT matters. A fused type's incoming EVENT (a dialog that just
        opened, built on the connection's read-loop thread) needs this to
        reach the code that must run on the asyncio loop, exactly like every
        message this transport already delivers via `emit_message` below -
        just carrying a real object instead of a wire message.
        """
        if self._stopped.is_set():
            return
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass  # the loop is already closed; nobody is left to receive it

    # ── the five methods the seam declares ──────────────────────────────────
    async def connect(self) -> None:
        for i in range(self.WORKERS):
            t = threading.Thread(target=self._work, name="juggler-%d" % i,
                                 daemon=True)
            t.start()
            self._threads.append(t)

    async def run(self) -> None:
        """⛔ Must not return until the transport is stopped: `Connection.run`
        awaits this and treats its return as the connection ending."""
        await self._stopped_future

    def request_stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        # One sentinel per worker: a thread blocked on `get()` does not notice
        # a flag.
        for _ in self._threads:
            self._queue.put(None)
        try:
            self._server.shutdown()
        except Exception:
            pass
        if not self._stopped_future.done():
            self._loop.call_soon_threadsafe(self._settle)

    def _settle(self) -> None:
        if not self._stopped_future.done():
            self._stopped_future.set_result(None)

    async def wait_until_stopped(self) -> None:
        await self._stopped_future
        for t in self._threads:
            t.join(timeout=5)

    def send(self, message: Dict) -> None:
        """⛔ ENQUEUE ONLY. Running the operation here would run it on the
        event loop, and every Juggler operation blocks."""
        self._queue.put(message)

    # ── the worker side ─────────────────────────────────────────────────────
    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            # ⛔ A PLAIN CALLABLE IS A JOB FROM `run_blocking`, and a dict is a
            # channel message: the same queue, the same four threads, told
            # apart by shape rather than by a second queue that would split
            # the pool in two.
            if callable(item):
                item()
                continue
            message = item
            try:
                result = self._server.handle(message)
            except Exception as failure:
                self.reply_error(message.get("id"), failure)
            else:
                self.reply(message.get("id"), result)

    # ── what the server calls back ──────────────────────────────────────────
    def emit_message(self, message: Dict) -> None:
        """Hand one protocol message up to `Connection.dispatch`.

        ⛔ `call_soon_threadsafe` and never a direct call: `on_message` walks
        into asyncio futures, and touching those from a worker thread corrupts
        the loop in ways that surface much later and somewhere else.
        """
        if self._stopped.is_set():
            return
        try:
            self._loop.call_soon_threadsafe(self.on_message, message)
        except RuntimeError:
            # The loop is already closed: the session is going away, and a
            # message arriving now has nobody left to read it.
            pass

    def reply(self, msg_id: Optional[int], result: Any) -> None:
        if msg_id is None:
            return
        out: Dict[str, Any] = {"id": msg_id}
        if result is not None:
            out["result"] = result
        self.emit_message(out)

    def reply_error(self, msg_id: Optional[int], failure: BaseException) -> None:
        """⛔ The shape matters: `Connection.dispatch` reads
        `msg["error"]["error"]["message"]`, and a flatter error is silently
        turned into a generic one, which is how a precise server-side reason
        becomes "Protocol error" by the time a user sees it."""
        if msg_id is None:
            return
        out: Dict[str, Any] = {"id": msg_id,
                               "error": {"error": _error_payload(failure)}}
        # An error that carries details for the client (`ExpectNotMet`) sends
        # them where `Connection.dispatch` reads them.
        details = getattr(failure, "details", None)
        if details is not None:
            out["errorDetails"] = details
        # And its call log, where `Connection.dispatch` reads it: the client
        # prints it under "Call log:" ([B341]).
        log = getattr(failure, "log", None)
        if log:
            out["log"] = list(log)
        self.emit_message(out)

# ── choosing one ────────────────────────────────────────────────────────────
#
# ⛔ THIS WAS A MODULE OF ITS OWN UNTIL 2026-08-29, and what it chose between
# no longer exists. `factory.py` held 88 lines to pick between the Node driver
# and this transport; the driver went on 2026-08-28, so a module whose whole
# job was to call another module was left choosing between one thing and an
# error message.
#
# ⛔ AND ITS TYPO GUARD WENT WITH IT, on purpose. It refused an unknown value
# on the argument that "a typo would run the driver while you believed you
# were testing the Python path". That was true and is not: there is no driver
# to run by accident, so the check protected nothing and cost a concept.
#
# What was KEPT is the message below. Version 0.7.4 shipped with the driver as
# the default, so somebody can have `INVPW_TRANSPORT=driver` still set in a
# shell, and "unknown transport" would tell them nothing about what happened.

CHOICE_ENV = "INVPW_TRANSPORT"
DRIVER = "driver"
JUGGLER = "juggler"


def chosen() -> str:
    """Which transport this process asked for. Anything but `driver` is us."""
    return (os.environ.get(CHOICE_ENV) or JUGGLER).strip().lower()


def make_transport(loop: asyncio.AbstractEventLoop) -> "InProcessTransport":
    """The transport a `Connection` should speak through."""
    if chosen() == DRIVER:
        raise RuntimeError(
            "the Node driver was removed on 2026-08-28: this package no "
            "longer ships `_driver/` and no longer downloads node. What "
            "replaced it is the in-process Python server, which is now the "
            "only transport - unset %s and it runs. To get the old arm back "
            "for a COMPARISON (the only thing it was still for): check out "
            "the last commit that carried it into a git worktree and point "
            "INVPW_DRIVER_TREE at it. Both `judge_both_transports.py` and "
            "`diff_protocol.py` read that variable." % CHOICE_ENV)
    from .server import JugglerServer
    return InProcessTransport(loop, JugglerServer())
