"""A message in the 1.62 form reaches the operations as the 1.61 form did.

Playwright 1.62 moved the call's `timeout` out of the parameters of nearly
every command and into the metadata, and added optional fields and two
commands. The server read `params["timeout"]` only, so a 1.62 client's
`page.goto(url, timeout=5000)` arrived with the timeout in `metadata`, the
operation found none and waited the 30 s default: the caller's own limit was
silently lost. `Dispatcher.handle` now folds the variants into one shape
(`_juggler/variants.py`) without asking which version sent the message.

The messages go through the real `Server.handle` to the real dispatcher
classes (routing tables included); only the operations behind them are
replaced by a recorder, because the thing under test is what they are GIVEN.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler._marshal import _caller_timeout
from invisible_playwright._juggler.dispatcher import ProtocolException, Server
from invisible_playwright._juggler.server import (
    ElementHandleDispatcher, FrameDispatcher, PageDispatcher)

pytestmark = pytest.mark.unit


def _put(server, cls, guid, seen, monkeypatch, ops):
    """One `cls` object in the server, its operations `ops` replaced by a
    recorder of the timeout they would wait, read the way the real ones do."""
    obj = cls.__new__(cls)
    obj.server, obj.guid, obj.parent, obj.disposed = server, guid, None, False
    server.register(obj)
    for op in ops:
        def record(self, params, _op=op):
            seen.append((_op, _caller_timeout(params), dict(params)))
            return {}
        monkeypatch.setattr(cls, op, record)


def _timeout_of(server, seen, guid, method, params, metadata=None):
    message = {"guid": guid, "method": method, "params": params}
    if metadata is not None:
        message["metadata"] = metadata
    server.handle(message)
    return seen[-1][1]


CASES = [
    ("frame@1", "goto", {"url": "http://x/"}),
    ("frame@1", "click", {"selector": "a", "strict": True}),
    ("frame@1", "fill", {"selector": "a", "value": "v", "strict": True}),
    ("frame@1", "waitForSelector", {"selector": "a", "strict": True}),
    ("page@1", "reload", {}),
    ("page@1", "screenshot", {"type": "png"}),
]


@pytest.fixture
def world(monkeypatch):
    server, seen = Server(), []
    _put(server, FrameDispatcher, "frame@1", seen, monkeypatch,
         ("op_goto", "op_click", "op_fill", "op_wait_for_selector"))
    _put(server, PageDispatcher, "page@1", seen, monkeypatch,
         ("op_reload", "op_screenshot"))
    _put(server, ElementHandleDispatcher, "handle@1", seen, monkeypatch,
         ("op_click",))
    return server, seen


@pytest.mark.parametrize("guid,method,params", CASES,
                         ids=[c[1] for c in CASES])
def test_a_timeout_in_the_metadata_is_the_timeout_of_the_call(
        world, guid, method, params):
    server, seen = world
    in_params = _timeout_of(server, seen, guid, method,
                            dict(params, timeout=5000))
    in_metadata = _timeout_of(server, seen, guid, method, dict(params),
                              {"timeout": 5000})
    assert in_params == 5.0
    assert in_metadata == in_params


@pytest.mark.parametrize("guid,method,params", CASES,
                         ids=[c[1] for c in CASES])
def test_no_timeout_anywhere_is_the_default_and_zero_is_no_limit(
        world, guid, method, params):
    server, seen = world
    assert _timeout_of(server, seen, guid, method, dict(params),
                       {"title": "x"}) == 30.0
    assert _timeout_of(server, seen, guid, method, dict(params),
                       {"timeout": 0}) is None
    assert _timeout_of(server, seen, guid, method, dict(params, timeout=0),
                       {"timeout": 7000}) is None


def test_the_timeout_of_the_parameters_wins_over_the_metadata(world):
    server, seen = world
    assert _timeout_of(server, seen, "frame@1", "goto",
                       {"url": "u", "timeout": 2000}, {"timeout": 9000}) == 2.0


def test_the_callers_message_is_not_changed(world):
    server, seen = world
    params, metadata = {"url": "u"}, {"timeout": 4000, "title": "t"}
    server.handle({"guid": "frame@1", "method": "goto", "params": params,
                   "metadata": metadata})
    assert params == {"url": "u"}
    assert metadata == {"timeout": 4000, "title": "t"}


def test_a_field_this_server_does_not_know_is_not_a_failure(world):
    server, seen = world
    server.handle({"guid": "frame@1", "method": "click",
                   "params": {"selector": "a", "scroll": "none",
                              "someFieldOfTheFuture": {"a": [1]}},
                   "metadata": {"timeout": 1000, "stepId": "s", "other": 1}})
    op, timeout, params = seen[-1]
    assert op == "op_click" and timeout == 1.0
    assert params["selector"] == "a" and params["scroll"] == "none"


def test_an_element_handle_takes_the_timeout_from_the_metadata_too(world):
    server, seen = world
    assert _timeout_of(server, seen, "handle@1", "click", {},
                       {"timeout": 3000}) == 3.0


@pytest.mark.parametrize("guid,method,params,why", [
    ("page@1", "screencastFrameAck", {"frameId": 4}, "screencastFrameAck"),
    ("page@1", "screenshot", {"type": "webp"}, "webp"),
    ("frame@1", "waitForFunction",
     {"expression": "e => e", "selector": "a"}, "selector"),
])
def test_a_command_not_served_refuses_with_its_reason(world, guid, method,
                                                      params, why):
    server, seen = world
    before = len(seen)
    with pytest.raises(ProtocolException) as refused:
        server.handle({"guid": guid, "method": method, "params": params})
    assert why in str(refused.value)
    assert "has no method" not in str(refused.value)
    assert len(seen) == before


@pytest.mark.parametrize("method,params", [
    ("storageState", {"credentials": True}),
    ("setStorageState", {"storageState": {"credentials": [{"id": "x"}]}}),
    ("newContext", {"storageState": {"credentials": [{"id": "x"}]}}),
])
def test_virtual_credentials_refuse_and_their_absence_does_not(method, params):
    from invisible_playwright._juggler import variants
    with pytest.raises(ProtocolException, match="credentials"):
        variants.to_internal(method, params, {})
    for nothing in (False, None, []):
        variants.to_internal(
            method, {"credentials": nothing,
                     "storageState": {"credentials": nothing}}, {})
