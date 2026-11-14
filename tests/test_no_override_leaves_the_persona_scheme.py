"""A context that asks for no override keeps the persona's colour scheme.

The persona declares the theme (`ui.systemUsesDarkTheme`, and the
`prefers-color-scheme` content override the core writes beside it). Playwright's
clients say "this option is not overridden" with the string "no-override" (the
Go client sends it on every `newContext`). The server used to pass it on as
`Browser.setColorScheme`, whose enum is dark / light / no-preference: the engine
refused the whole context, so the one client that sends it could not open a page
at all, and anyone who worked around the refusal by forcing a scheme was
choosing the page's theme instead of the persona's.

Unit half: "no-override" never becomes an engine command, and an explicit
"dark", "light" or "no-preference" still does. The e2e half, one real browser
per persona, is `test_the_scheme_is_the_personas_e2e.py`.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_playwright._juggler.server import (BrowserDispatcher,
                                                  PageDispatcher, Server)
from _browser_census import closes_with_the_test


class _Connection(EventListeners):
    def __init__(self):
        super().__init__()
        closes_with_the_test(self)
        self.sent = []

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params))
        return {"browserContextId": "CTX"}


_LOOK = ("Browser.setColorScheme", "Browser.setReducedMotion", "Browser.setForcedColors")


def _commands(params):
    conn = _Connection()
    dispatcher = BrowserDispatcher(Server(), None, conn, "151.0")
    conn.sent.clear()                     # what the constructor says is not the option set
    dispatcher._apply_context_options("CTX", params)
    # Only the commands about the look of the page: the context also gets its
    # downloads folder, which is not what is asked here.
    return [(m, p) for m, p in conn.sent if m in _LOOK]


@pytest.mark.unit
@pytest.mark.parametrize("option,command", [
    ("colorScheme", "Browser.setColorScheme"),
    ("reducedMotion", "Browser.setReducedMotion"),
    ("forcedColors", "Browser.setForcedColors"),
])
def test_no_override_is_not_an_engine_command(option, command):
    assert [m for m, _ in _commands({option: "no-override"}) if m == command] == []


@pytest.mark.unit
def test_the_go_clients_whole_option_set_sends_nothing_about_the_scheme():
    sent = _commands({"colorScheme": "no-override", "reducedMotion": "no-override",
                      "forcedColors": "no-override", "contrast": "no-override"})
    assert sent == []


@pytest.mark.unit
@pytest.mark.parametrize("scheme", ["dark", "light", "no-preference"])
def test_an_explicit_scheme_is_still_sent(scheme):
    assert _commands({"colorScheme": scheme}) == [
        ("Browser.setColorScheme", {"browserContextId": "CTX", "colorScheme": scheme})]


class _Page(PageDispatcher):
    def __init__(self):
        self.calls = []

    def send(self, command, params, *args, **kwargs):
        self.calls.append((command, params))
        return {}


@pytest.mark.unit
def test_emulate_media_no_override_resets_without_naming_a_scheme():
    page = _Page()
    page.op_emulate_media({"colorScheme": "no-override"})
    # The engine's call replaces the page's emulation: the scheme left out is
    # the scheme taken back, and the enum has no "no-override".
    assert page.calls == [("Page.setEmulatedMedia", {})]


@pytest.mark.unit
def test_emulate_media_no_override_for_the_medium_is_the_empty_type():
    page = _Page()
    page.op_emulate_media({"media": "no-override", "colorScheme": "dark"})
    assert page.calls == [("Page.setEmulatedMedia", {"type": "", "colorScheme": "dark"})]


@pytest.mark.unit
def test_emulate_media_with_nothing_asked_sends_nothing():
    page = _Page()
    page.op_emulate_media({})
    assert page.calls == []


@pytest.mark.unit
def test_emulate_media_an_explicit_scheme_is_sent():
    page = _Page()
    page.op_emulate_media({"colorScheme": "light"})
    assert page.calls == [("Page.setEmulatedMedia", {"colorScheme": "light"})]
