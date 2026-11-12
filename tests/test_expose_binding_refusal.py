"""Exposed functions need an engine that hands the page-side end of a binding over and has `Page.addBinding`.

No fallback to a name on the window: an engine without them is refused with a sentence that says so, before anything is installed.
(No browser: the engine is read from its files.)
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_an_engine_without_what_the_functions_need_refuses_and_leaves_no_name(tmp_path):
    """No fallback to a name on the window: an engine without `Page.addBinding` and the binding that hands its end over is
    refused with a sentence that says so."""
    from invisible_playwright._juggler import _bindings
    from invisible_playwright._juggler.dispatcher import ProtocolException
    from invisible_playwright._juggler.server import BrowserContextDispatcher

    loose = tmp_path / "modules" / "juggler" / "protocol"
    loose.mkdir(parents=True)
    (loose / "Protocol.js").write_text("'addBinding': { params: {} },", encoding="utf-8")
    assert _bindings.engine_supports(str(tmp_path)) is False          # one addBinding: the browser's only
    assert _bindings.engine_supports("") is False
    (loose / "Protocol.js").write_text("'addBinding': { params: {} },\n'addBinding': { params: {} },", encoding="utf-8")
    _bindings.engine_supports.cache_clear()
    assert _bindings.engine_supports(str(tmp_path)) is True

    class Browser:
        engine_dir = str(tmp_path / "old")

    context = object.__new__(BrowserContextDispatcher)
    context.__dict__["browser"] = Browser()
    with pytest.raises(ProtocolException, match="newer engine"):
        BrowserContextDispatcher.require_binding_engine(context)
