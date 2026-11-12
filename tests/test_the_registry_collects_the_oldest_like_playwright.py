"""The guid registry collects like Playwright's server (1.62 `dispatchers/dispatcher.ts`).

Past the limit of a bucket the oldest tenth of it is disposed with `__dispose__` {reason:
"gc"} to the client that owns it, the subtree silently; below the limit nothing changes. A
call on a collected object answers "target closed", not a crash.

Known-bad: without `collect_stale` the registry holds every object and no `__dispose__`
is sent; with the limit applied to the wrong bucket (the frames in one) the bucket test
fails; with a victim's child announced as its own `__dispose__` the subtree test fails.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import websockets  # noqa: F401 - the dev extra installs it: a missing one is a failure, never a skip

from websockets.asyncio.client import connect  # noqa: E402

from invisible_playwright._juggler import dispatcher, remote  # noqa: E402
from invisible_playwright._juggler.dispatcher import Dispatcher  # noqa: E402
from invisible_playwright._juggler.server import LocalUtilsDispatcher  # noqa: E402

pytestmark = pytest.mark.unit


class Request(Dispatcher):
    TYPE = "Request"
    METHODS = {"ping": "op_ping"}

    def op_ping(self, params):
        return {"ok": True}


class Response(Dispatcher):
    TYPE = "Response"
    METHODS: dict = {}


class MainFrame(Dispatcher):
    TYPE = "Frame"

    def gc_bucket(self):
        return "MainFrame"


class ChildFrame(Dispatcher):
    TYPE = "Frame"

    def gc_bucket(self):
        return "Frame"


class FakeContext(Dispatcher):
    TYPE = "BrowserContext"
    METHODS = {"make": "op_make"}

    def op_make(self, params):
        kind = {"Request": Request, "MainFrame": MainFrame, "Frame": ChildFrame}[params["kind"]]
        made = []
        for _ in range(params["count"]):
            made.append(kind(self.server, self).guid)
            if params.get("withResponse"):
                Response(self.server, self.server._objects[made[-1]])
        return {"guids": made}


class FakeBrowser(Dispatcher):
    TYPE = "Browser"
    METHODS = {"newContext": "op_new_context"}

    def op_new_context(self, params):
        return {"context": FakeContext(self.server, self).channel}


@pytest.fixture
def served():
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    browser = FakeBrowser(server, browser_type, {"version": "firefox/1", "name": "firefox",
                                                 "browserName": "firefox"})
    # Playwright's rule has no age: these tests are about it. The age is below.
    server.gc_min_age = 0.0
    endpoint = remote.RemoteEndpoint(server, browser_type, browser)
    endpoint.start()
    yield endpoint
    endpoint.close()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def call(ws, msg_id, guid, method, params=None):
    await ws.send(json.dumps({"id": msg_id, "guid": guid, "method": method,
                              "params": params or {}, "metadata": {}}))
    before = []
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == msg_id:
            return before, message
        before.append(message)


async def session(endpoint):
    ws = await connect(endpoint.ws_endpoint)
    await call(ws, 1, "", "initialize", {})
    _, made = await call(ws, 2, endpoint.browser.guid, "newContext", {})
    return ws, made["result"]["context"]["guid"]


def disposed_in(messages):
    return [(m["guid"], m["params"]) for m in messages if m["method"] == "__dispose__"]


def test_the_limits_are_playwrights_and_one_per_type():
    assert dispatcher.GC_LIMIT_DEFAULT == 10000
    # Playwright's, plus the three network types lowered (see the comment on GC_LIMITS)
    assert {k: v for k, v in dispatcher.GC_LIMITS.items() if v != 2000} == {
        "JSHandle": 100000, "ElementHandle": 100000}
    assert {k for k, v in dispatcher.GC_LIMITS.items() if v == 2000} == {"Request", "Response", "Route"}
    limits = dispatcher.gc_limits_from_env({"INVPW_GC_LIMITS": "Request=1000, Response=500,default=7,bad"})
    assert limits["Request"] == 1000 and limits["Response"] == 500 and limits["default"] == 7
    assert limits["JSHandle"] == 100000
    assert dispatcher.gc_limits_from_env({})["default"] == 10000


def test_below_the_limit_nothing_is_disposed(served):
    served.server.gc_limits["Request"] = 20

    async def go():
        ws, ctx = await session(served)
        before, reply = await call(ws, 3, ctx, "make", {"kind": "Request", "count": 20})
        await ws.close()
        return before, reply
    before, reply = run(go())
    assert disposed_in(before) == []
    assert len(reply["result"]["guids"]) == 20
    assert sum(1 for o in served.server._objects.values() if o.TYPE == "Request") == 20


def test_past_the_limit_the_oldest_tenth_is_disposed_with_reason_gc_to_its_owner(served):
    served.server.gc_limits["Request"] = 20

    async def go():
        ws, ctx = await session(served)
        other = await connect(served.ws_endpoint)
        await call(other, 1, "", "initialize", {})
        before, reply = await call(ws, 3, ctx, "make", {"kind": "Request", "count": 21})
        # the other client is told nothing of what is not its own
        await call(other, 2, served.browser.guid, "newContext", {})
        await ws.close()
        await other.close()
        return before, reply
    before, reply = run(go())
    guids = reply["result"]["guids"]
    assert disposed_in(before) == [(guids[0], {"reason": "gc"}), (guids[1], {"reason": "gc"})]
    # the new object is announced before the ones it makes room for are disposed
    order = [(m["method"], m["guid"] if m["method"] == "__dispose__" else m["params"]["guid"])
             for m in before if m["method"] in ("__create__", "__dispose__")]
    assert order.index(("__create__", guids[20])) < order.index(("__dispose__", guids[0]))
    live = [o.guid for o in served.server._objects.values() if o.TYPE == "Request"]
    assert live == guids[2:]


def test_a_call_on_a_collected_object_answers_target_closed(served):
    served.server.gc_limits["Request"] = 20

    async def go():
        ws, ctx = await session(served)
        _, made = await call(ws, 3, ctx, "make", {"kind": "Request", "count": 21})
        guids = made["result"]["guids"]
        _, gone = await call(ws, 4, guids[0], "ping")
        _, alive = await call(ws, 5, guids[-1], "ping")
        await ws.close()
        return gone, alive
    gone, alive = run(go())
    assert "closed" in gone["error"]["error"]["message"].lower()
    assert alive["result"] == {"ok": True}


def test_the_subtree_of_a_collected_object_goes_without_a_message_of_its_own(served):
    served.server.gc_limits["Request"] = 20

    async def go():
        ws, ctx = await session(served)
        before, made = await call(ws, 3, ctx, "make",
                                  {"kind": "Request", "count": 21, "withResponse": True})
        await ws.close()
        return before, made
    before, made = run(go())
    assert [g for g, _ in disposed_in(before)] == made["result"]["guids"][:2]
    assert sum(1 for o in served.server._objects.values() if o.TYPE == "Response") == 19


def test_main_frames_and_frames_are_counted_apart(served):
    served.server.gc_limits.update({"MainFrame": 10, "Frame": 10})

    async def go():
        ws, ctx = await session(served)
        b1, _ = await call(ws, 3, ctx, "make", {"kind": "MainFrame", "count": 10})
        b2, _ = await call(ws, 4, ctx, "make", {"kind": "Frame", "count": 10})
        b3, r3 = await call(ws, 5, ctx, "make", {"kind": "Frame", "count": 1})
        await ws.close()
        return b1, b2, b3, r3
    b1, b2, b3, r3 = run(go())
    assert disposed_in(b1) == [] and disposed_in(b2) == []
    assert [p for _, p in disposed_in(b3)] == [{"reason": "gc"}]
    kinds = [type(o).__name__ for o in served.server._objects.values()
             if o.TYPE == "Frame"]
    assert kinds.count("MainFrame") == 10 and kinds.count("ChildFrame") == 10



def test_what_a_client_was_just_handed_stays_valid_however_fast_the_bucket_fills(served):
    """The Go client uses a request inside the callback of its event. Past the limit the
    oldest tenth goes, but none younger than GC_MIN_AGE_S: the first request is still
    answered after 60 more were made over a limit of 20; once it is old enough it goes.
    Known-bad: collecting the oldest tenth whatever its age answers `target closed`."""
    served.server.gc_limits["Request"] = 20
    served.server.gc_min_age = 30.0

    async def go():
        ws, ctx = await session(served)
        before, made = await call(ws, 3, ctx, "make", {"kind": "Request", "count": 60})
        guids = made["result"]["guids"]
        _, held = await call(ws, 4, guids[0], "ping")
        grown = sum(1 for o in served.server._objects.values() if o.TYPE == "Request")
        served.server.gc_min_age = 0.0
        before2, _ = await call(ws, 5, ctx, "make", {"kind": "Request", "count": 1})
        _, gone = await call(ws, 6, guids[0], "ping")
        await ws.close()
        return before, held, grown, before2, gone
    before, held, grown, before2, gone = run(go())
    assert disposed_in(before) == [] and held["result"] == {"ok": True}
    assert grown == 60                          # over the limit, and nothing is lost
    assert [p for _, p in disposed_in(before2)][:1] == [{"reason": "gc"}]
    assert "closed" in gone["error"]["error"]["message"].lower()
