"""A protocol message is JSON, and a value that JSON would change or lose says where it is.

The client and the server of this package pass each other dicts in one process, so nothing
checked that a message could cross a socket. `_juggler/wire.py` is that check and
`JsonWireTransport` puts it between the two ends. No browser starts here.

Known-bad, run before this file was trusted: with no `wire.py` the import of this file fails;
with `first_problem` returning None for bytes, the second test goes red; with
`JsonWireTransport.emit_message` forwarding the message unchecked, the test of the reply that is not
JSON goes red.
"""
from __future__ import annotations

import asyncio
import math

import pytest

from invisible_playwright._juggler import wire
from invisible_playwright._juggler.transport import InProcessTransport, JsonWireTransport

pytestmark = pytest.mark.unit


# ── the module ─────────────────────────────────────────────────────────────

def test_a_plain_message_round_trips_unchanged():
    message = {"id": 7, "guid": "page@1", "method": "goto",
               "params": {"url": "https://a.test/", "waitUntil": "load", "n": [1, 2.5, None, True]},
               "metadata": {"title": "Navigate", "timeout": 30000}}
    assert wire.round_trip(message) == message


def test_bytes_are_refused_and_the_path_is_named():
    message = {"id": 1, "params": {"payloads": [{"name": "a.txt", "buffer": b"abc"}]}}
    with pytest.raises(wire.WireError) as err:
        wire.dumps(message)
    assert err.value.path == "$.params.payloads[0].buffer"
    assert "bytes" in str(err.value)


def test_a_key_that_is_not_a_string_is_refused():
    with pytest.raises(wire.WireError) as err:
        wire.dumps({"params": {1: "x"}})
    assert err.value.path == "$.params[1]"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_not_a_number_is_refused(bad):
    with pytest.raises(wire.WireError):
        wire.dumps({"result": {"value": bad}})


def test_an_object_is_refused_and_a_tuple_reads_back_as_a_list():
    with pytest.raises(wire.WireError) as err:
        wire.dumps({"result": {"page": object()}})
    assert err.value.path == "$.result.page"
    assert wire.round_trip({"a": (1, 2)}) == {"a": [1, 2]}


def test_a_lone_surrogate_survives_the_text():
    """`ensure_ascii` keeps it as an escape, so encoding the text to UTF-8 for a socket cannot fail."""
    message = {"result": {"value": "x\ud800y"}}
    assert wire.round_trip(message) == message
    wire.dumps(message).encode("utf-8")


# ── the transport ──────────────────────────────────────────────────────────

class _Server:
    """What `InProcessTransport` needs of a server: attach, handle, shutdown."""

    def __init__(self, result):
        self._result = result
        self.seen = []

    def attach(self, transport):
        self.transport = transport

    def handle(self, message):
        self.seen.append(message)
        return self._result

    def shutdown(self):
        pass


def _call(transport_class, result, message):
    """Send `message` through a `transport_class` over a server answering `result`;
    return (what the server saw, what the client received, the transport)."""
    async def go():
        loop = asyncio.get_running_loop()
        server = _Server(result)
        transport = transport_class(loop, server)
        got = []
        done = asyncio.Event()

        def on_message(reply):
            got.append(reply)
            done.set()

        transport.on_message = on_message
        await transport.connect()
        transport.send(message)
        await asyncio.wait_for(done.wait(), 5)
        transport.request_stop()
        return server.seen, got, transport

    return asyncio.run(go())


def test_the_wire_transport_hands_the_server_the_text_s_message():
    seen, got, transport = _call(JsonWireTransport, {"ok": [1, 2]},
                                 {"id": 3, "guid": "g", "method": "m", "params": {"t": (1, 2)}})
    assert seen == [{"id": 3, "guid": "g", "method": "m", "params": {"t": [1, 2]}}]
    assert got == [{"id": 3, "result": {"ok": [1, 2]}}]
    assert transport.wire_errors == []


def test_a_reply_that_is_not_json_fails_its_call_naming_the_path():
    seen, got, transport = _call(JsonWireTransport, {"buffer": b"\x00\x01"},
                                 {"id": 4, "guid": "g", "method": "m", "params": {}})
    assert len(got) == 1 and got[0]["id"] == 4
    message = got[0]["error"]["error"]["message"]
    assert "not JSON" in message and "$.result.buffer" in message
    assert len(transport.wire_errors) == 1


def test_the_plain_transport_still_passes_the_object_it_is_given():
    """Local use is unchanged: no JSON, the same dicts, which is why this check had to be made opt-in."""
    seen, got, _ = _call(InProcessTransport, {"buffer": b"\x00\x01"},
                         {"id": 5, "guid": "g", "method": "m", "params": {}})
    assert got == [{"id": 5, "result": {"buffer": b"\x00\x01"}}]


def test_a_client_message_that_is_not_json_raises_from_send():
    async def go():
        loop = asyncio.get_running_loop()
        transport = JsonWireTransport(loop, _Server(None))
        with pytest.raises(wire.WireError) as err:
            transport.send({"id": 1, "params": {"x": {1, 2}}})
        assert err.value.path == "$.params.x"

    asyncio.run(go())
