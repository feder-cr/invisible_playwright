"""The persona's keyboard is the one of the language the launcher decided.

⛔ KNOWN-BAD. Every session typed on one US table whatever its locale: an
Italian persona (`intl.accept_languages` = "it-IT,it,en-US,en") typed `ò` with
no key event and `@` as Shift+Digit2, where an Italian keyboard sends AltGr+ò;
and every persona sent Chromium's key codes for `;`, `=`, `-` where Firefox on
Windows sends 59, 61, 173. The server now reads the language the launcher
wrote into the launch prefs and hands the matching layout to every page's
hands (`invisible_core.juggler.keyboard.layout_for`). The core owns the tables
and the keyboard; this file tests the wiring, on the public pieces.
"""
from __future__ import annotations

from invisible_core.juggler import PageActs
from invisible_core.juggler.keyboard import layout_for

from invisible_playwright._juggler.server import BrowserDispatcher, keyboard_layout_for


def test_the_layout_comes_from_the_language_the_launcher_wrote():
    assert keyboard_layout_for is layout_for
    assert keyboard_layout_for("it-IT,it,en-US,en") == "it"
    assert keyboard_layout_for("en-US,en") == "us"
    assert keyboard_layout_for("") == "us"


def test_the_browser_hands_its_layout_to_every_page(monkeypatch):
    """`actions_for_page` builds the page's hands with the session's layout."""
    seen = {}

    class _Actions:
        def __init__(self, conn, session, lifecycle, injected, **kw):
            seen.update(kw)

    monkeypatch.setattr("invisible_playwright._juggler.server.Actions", _Actions)
    browser = BrowserDispatcher.__new__(BrowserDispatcher)
    browser.conn = object()
    browser.session_seed = 4242
    browser.motion_budget_s = None
    browser.layout = "it"
    browser.actions_for_page("S", lifecycle=None, injected=None, number=1)
    assert seen["layout"] == "it"
    assert isinstance(seen["acts"], PageActs) and seen["session_seed"] == 4242


def test_the_default_is_the_us_keyboard():
    """A browser built without a layout types on the US keyboard, as before."""
    import inspect
    sig = inspect.signature(BrowserDispatcher.__init__)
    assert sig.parameters["layout"].default == "us"
