"""Every route command (continue, abort, fulfill, redirect) leaves through `RouteDispatcher._send`, and `_send` swallows the one
error that is not an error: the request the browser has already closed.

Stock Playwright's server sends the three interception commands as may-fail. Here a new command that talked to the page
directly (`self.request.page.send("Network....")`) would bring the old defect back: the client keeps an error for the whole
connection and raises it on the next route command, which is then never sent (see
`test_networkidle_through_a_served_browser_with_routes_e2e.py`). No browser: the source is read.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

SERVER = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_playwright" / "_juggler" / "server.py"


def _route_dispatcher():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RouteDispatcher")


def _methods(cls):
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def _sends_network_command(fn):
    """The `page.send` / `conn.send` / `_send` calls of a function that name a Network.* command string."""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for arg in node.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("Network."):
                    out.append((getattr(node.func, "attr", getattr(node.func, "id", "?")), arg.value))
    return out


def test_every_op_of_a_route_goes_through__send_and_nothing_else_talks_to_the_page():
    methods = _methods(_route_dispatcher())
    ops = {name: fn for name, fn in methods.items() if name.startswith("op_")}
    assert set(ops) >= {"op_abort", "op_continue", "op_fulfill", "op_redirect"}, sorted(ops)
    for name, fn in ops.items():
        sends = _sends_network_command(fn)
        assert sends, "%s sends no Network command at all: update this guard if it is a new kind" % name
        for via, command in sends:
            assert via == "_send", "%s sends %s through %s, not through RouteDispatcher._send" % (name, command, via)
    # and the only direct talk to the page in the class is _send's
    for name, fn in methods.items():
        if name in ops or name == "_send":
            continue
        assert not _sends_network_command(fn), "%s sends a Network command by itself" % name


def test__send_swallows_a_request_the_browser_already_closed_and_nothing_else():
    send = _methods(_route_dispatcher())["_send"]
    handlers = [n for n in ast.walk(send) if isinstance(n, ast.ExceptHandler)]
    caught = {ast.unparse(h.type) for h in handlers if h.type is not None}
    assert caught == {"juggler.ProtocolError", "juggler.TargetClosedError"}, caught
    source = ast.get_source_segment(SERVER.read_text(encoding="utf-8"), send)
    assert '"Cannot find request" not in str(exc)' in source and "raise" in source, \
        "a ProtocolError other than 'Cannot find request' must still reach the client"
    assert source.index("self.dispose()") > source.index("except juggler.TargetClosedError"), \
        "the route is disposed after a swallowed error too"


def test_the_stock_client_the_networkidle_e2e_drives_the_server_with_is_a_pinned_dev_dependency():
    """That e2e fails (not skips) without the stock Playwright client, so CI must install it: the `dev` extra pins it to the
    version of scripts/playwright_pin.txt."""
    import tomllib

    root = SERVER.parents[3]
    dev = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["dev"]
    pin = (root / "scripts" / "playwright_pin.txt").read_text(encoding="utf-8").strip()
    assert "playwright==%s" % pin in dev, dev
