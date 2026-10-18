"""A failed expect reports the value the page had, read from the wire once ([B344]).

The vendored client converts the `received` value of a failed expect twice:
`Frame._expect` turns it from its wire form into a Python value, and
`AssertionsBase._expect_impl` used to pass that Python value through
`parse_value` again, reading it AS the wire form. A primitive survived that,
because `parse_value` hands anything that is not a dict back unchanged. An
object did not: its keys were read as serialization tags, so `{"v": "x"}` was
reported as `x`, `{"n": 5}` as `5`, and `{"a": 1}` died with `KeyError: 'id'`
instead of the assertion's message. Playwright 1.61.0 has the same two reads,
and so did its main branch on 2026-10-16.

The two real functions run here, in the order a failed assertion runs them,
with only the channel replaced: it raises the error our server sends, with
the value in the wire form the protocol carries. No browser is involved.
"""
from __future__ import annotations

import asyncio

import pytest

from invisible_playwright._pw._impl._assertions import AssertionsBase
from invisible_playwright._pw._impl._errors import Error
from invisible_playwright._pw._impl._frame import Frame


class _FailingChannel:
    """The server's answer to a failed expect: an error carrying the details."""

    def __init__(self, received_wire):
        self.received_wire = received_wire

    async def send(self, method, timeout, params, title=None):
        error = Error("expect failed")
        error._details = {"received": {"value": self.received_wire}}
        error._log = []
        raise error


class _Frame:
    def __init__(self, received_wire):
        self._channel = _FailingChannel(received_wire)
        self._timeout = None


class _Locator:
    _loop = None
    _dispatcher_fiber = None


class _Assertions(AssertionsBase):
    def __init__(self, received_wire):
        super().__init__(_Locator())
        self._frame = _Frame(received_wire)

    async def _call_expect(self, expression, expect_options, title):
        return await Frame._expect(self._frame, "#x", expression, expect_options, title)


def _message(received_wire) -> str:
    with pytest.raises(AssertionError) as failure:
        asyncio.run(_Assertions(received_wire)._expect_impl(
            "to.have.property", {"expressionArg": "p"}, None,
            "Locator expected to have JS property 'p'"))
    return str(failure.value)


@pytest.mark.parametrize("wire,shown", [
    ({"o": [{"k": "a", "v": {"n": 1}}], "id": 1}, "{'a': 1}"),
    ({"o": [{"k": "v", "v": {"s": "x"}}], "id": 1}, "{'v': 'x'}"),
    ({"o": [{"k": "n", "v": {"n": 5}}], "id": 1}, "{'n': 5}"),
], ids=["a key that is the array tag", "a key that is the value tag",
        "a key that is the number tag"])
def test_an_object_is_reported_as_the_object(wire, shown):
    assert "Actual value: %s" % shown in _message(wire)


@pytest.mark.parametrize("wire,shown", [
    ({"s": "hello"}, "hello"),
    ({"n": 3}, "3"),
    ({"a": [{"n": 1}, {"s": "b"}], "id": 1}, "[1, 'b']"),
], ids=["a string", "a number", "a list"])
def test_what_already_worked_still_does(wire, shown):
    assert "Actual value: %s" % shown in _message(wire)
