"""`from invisible_playwright import launch_server` works, and the README's example is the one that runs.

The server API lived only at `invisible_playwright._juggler.remote`, a private path: the README
and every caller had to name a module that is allowed to move. The names a user writes are on the
package, and the README block that shows them is the one a reader copies, so it is read here and
checked against the package: every name it imports from `invisible_playwright` exists, and the
two files (server and client) are both there. The example itself was run against the real engine
when it was written (the server prints its address, Playwright for Python connects, reads a page's
title and closes; the server's browser stays up until Enter).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import invisible_playwright

pytestmark = pytest.mark.unit

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def test_the_server_api_is_on_the_package():
    from invisible_playwright import RemoteServer, launch_server, launch_server_async
    from invisible_playwright._juggler import remote
    assert launch_server is remote.launch_server
    assert launch_server_async is remote.launch_server_async
    assert RemoteServer is remote.RemoteServer


def test_the_names_are_in_all():
    for name in ("launch_server", "launch_server_async", "RemoteServer"):
        assert name in invisible_playwright.__all__


def test_importing_the_package_does_not_need_the_websocket_library():
    """The extra is optional: the import is at the top of the package and must not reach for it."""
    source = (Path(invisible_playwright.__file__).parent / "_juggler" / "remote.py").read_text(encoding="utf-8")
    top = [line for line in source.splitlines() if re.match(r"^(import|from) websockets", line)]
    assert top == [], "remote.py imports websockets at the top: %r" % top


def _section() -> str:
    return README.split("### Serving one browser to many clients")[1].split("\n### ")[0]


def test_the_readme_example_names_only_what_the_package_exports():
    blocks = re.findall(r"```python\n(.*?)```", _section(), re.S)
    assert len(blocks) == 2, "the section shows a server and a client: %d blocks" % len(blocks)
    server, client = blocks
    imported = re.findall(r"from invisible_playwright import ([\w, ]+)", server)
    assert imported, "the server example does not import from invisible_playwright"
    for names in imported:
        for name in (n.strip() for n in names.split(",")):
            assert hasattr(invisible_playwright, name), name
    assert "ws_endpoint" in server and "launch_server(" in server
    assert ".firefox.connect(" in client
    assert "invisible_playwright._juggler" not in README, "the README names a private module"


def test_the_readme_says_how_to_install_the_extra_and_how_a_browser_ends():
    section = _section()
    assert 'uv add "invisible-playwright[remote]"' in section
    assert "server.close()" in section
