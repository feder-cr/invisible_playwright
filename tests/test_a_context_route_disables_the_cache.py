"""A route turns the context's cache off, and the last route turns it back on.

Playwright upstream pairs the two commands in `doUpdateRequestInterception`
(`packages/playwright-core/src/server/firefox/ffBrowser.ts`, v1.61.0 line 351):
`Browser.setRequestInterception` and `Browser.setCacheDisabled` carry the same
flag, for the same `browserContextId`. A server that sends only the first leaves
the engine's cache2 filling while a route holds every request: ~200 MB of disk
cache in a long routed session. Both commands are about the context, so a page
route (`Network.setRequestInterception`) sends no cache command, as upstream.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_playwright._juggler.server import (
    BrowserContextDispatcher, BrowserDispatcher, Server,
)


class _Engine(EventListeners):
    """Records every command the server sends."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout=30.0):
        self.sent.append((method, params))
        return {}


def _context(context_id="C1"):
    engine = _Engine()
    browser = BrowserDispatcher(Server(), None, engine, "151.0", session_seed=1)
    context = BrowserContextDispatcher(browser.server, browser, {}, context_id)
    browser.contexts.append(context)
    return engine, context


def _flags(engine, command):
    return [p for m, p in engine.sent if m == command]


@pytest.mark.unit
def test_a_route_disables_the_cache_of_its_context():
    engine, context = _context("C1")

    context.op_set_interception({"patterns": [{"glob": "**/*"}]})

    assert _flags(engine, "Browser.setRequestInterception") == [
        {"browserContextId": "C1", "enabled": True}]
    assert _flags(engine, "Browser.setCacheDisabled") == [
        {"browserContextId": "C1", "cacheDisabled": True}]


@pytest.mark.unit
def test_the_last_route_gone_enables_the_cache_again():
    engine, context = _context("C1")
    context.op_set_interception({"patterns": [{"glob": "**/*"}]})
    engine.sent.clear()

    context.op_set_interception({"patterns": []})

    assert _flags(engine, "Browser.setRequestInterception") == [
        {"browserContextId": "C1", "enabled": False}]
    assert _flags(engine, "Browser.setCacheDisabled") == [
        {"browserContextId": "C1", "cacheDisabled": False}]


@pytest.mark.unit
def test_the_default_context_is_addressed_by_absence():
    engine, context = _context(None)

    context.op_set_interception({"patterns": [{"glob": "**/*"}]})

    assert _flags(engine, "Browser.setCacheDisabled") == [
        {"cacheDisabled": True}]
