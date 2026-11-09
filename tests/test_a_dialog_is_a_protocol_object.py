"""A dialog is an object of the protocol, and the server never reads the client.

⛔ WHAT IT REPLACES. The dialog was a "fused" class: no `__create__`, no guid,
no channel. The server built it from the client's own registry
(`Server.twin`, bound by `bind_impl_objects`) and handed it to the context by
calling a client function. Now it is announced with `__create__` under its
Page, and the `dialog` event of the context carries its channel, like every
other object.

Known-bad: `PageDispatcher._emit_fused_dialog` and `Server.bind_twins`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from invisible_playwright._juggler import dispatcher as dispatcher_mod
from invisible_playwright._juggler.dispatcher import Server
from invisible_playwright._juggler.server import PageDispatcher


class _Wire:
    """The transport, reduced to what the server sends up."""

    def __init__(self) -> None:
        self.sent: List[Dict] = []

    def emit_message(self, message: Dict) -> None:
        self.sent.append(message)


class _Context:
    """The context's `emit` goes where a real one does: up the wire."""

    def __init__(self, server: Server, guid: str) -> None:
        self.server = server
        self.guid = guid

    def emit(self, method: str, params: Dict) -> None:
        self.server.send_up({"guid": self.guid, "method": method,
                             "params": params})


def _opened() -> "tuple[Server, _Wire, PageDispatcher]":
    server = Server()
    wire = _Wire()
    server.attach(wire)
    page = PageDispatcher.__new__(PageDispatcher)
    page.server = server
    page.guid = "page@1"
    page.parent = None
    page.context = _Context(server, "browsercontext@1")
    page.number = 1
    page._dialogs_open = {}
    page._dialogs_seen = 0
    page._on_juggler_event("Page.dialogOpened", {
        "dialogId": "d1", "type": "prompt", "message": "Name?",
        "defaultValue": "x"})
    return server, wire, page


def test_a_dialog_is_announced_with_a_guid_under_its_page_before_the_event():
    _server, wire, _page = _opened()
    created = [m for m in wire.sent if m["method"] == "__create__"]
    assert len(created) == 1
    create = created[0]
    assert create["guid"] == "page@1"  # the parent is the Page
    assert create["params"]["type"] == "Dialog"
    assert create["params"]["guid"].startswith("dialog@")
    assert create["params"]["initializer"] == {
        "type": "prompt", "message": "Name?", "defaultValue": "x",
        "page": {"guid": "page@1"}}
    event = [m for m in wire.sent if m["method"] == "dialog"]
    assert len(event) == 1
    assert event[0]["guid"] == "browsercontext@1"
    assert event[0]["params"] == {"dialog": {"guid": create["params"]["guid"]}}
    # ORDER IS THE PROTOCOL: the object exists on the client before it is named.
    assert wire.sent.index(create) < wire.sent.index(event[0])


def test_the_server_has_no_way_to_reach_the_clients_objects():
    server = Server()
    for gone in ("bind_twins", "twin", "deliver", "call_soon", "run_blocking"):
        assert not hasattr(server, gone), gone
    assert not hasattr(PageDispatcher, "_emit_fused_dialog")
    assert not hasattr(PageDispatcher, "send_async")


def test_a_dialog_answers_accept_and_dismiss_by_its_guid():
    server, _wire, page = _opened()
    sent: List[Any] = []
    page.send = lambda command, params, timeout=None: sent.append((command, params))
    guid = next(o.guid for o in server._objects.values() if o.TYPE == "Dialog")
    server.handle({"guid": guid, "method": "dismiss", "params": {}})
    assert sent == [("Page.handleDialog", {"dialogId": "d1", "accept": False})]


def test_the_module_keeps_no_client_registry():
    assert not hasattr(dispatcher_mod.Server(), "_twins")
