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
from invisible_core.juggler.keyboard import layouts_for

from invisible_playwright._juggler.server import BrowserDispatcher, keyboard_layouts_for


def test_the_layout_comes_from_the_language_the_launcher_wrote():
    assert keyboard_layouts_for is layouts_for
    assert keyboard_layouts_for("it-IT,it,en-US,en") == ["00000410"]
    assert keyboard_layouts_for("en-US,en") == ["00000409"]
    assert keyboard_layouts_for("") == ["00000409"]


def test_the_session_hands_the_server_its_language_and_country_s_keyboard():
    """German in Switzerland types on the Swiss keyboard: only the session knows
    the country, so the decision travels in a server-only pref, and the server
    takes it out before the profile is written."""
    from invisible_core import SessionLocale
    from invisible_playwright._juggler.server import KEYBOARD_LAYOUT_PREF
    from invisible_playwright._session import build_prefs
    from invisible_core import generate_profile
    prefs = build_prefs(profile=generate_profile(seed=4242), locale=SessionLocale(("de", "en-US", "en"), "CH"),
                        timezone="Europe/Zurich", extra_prefs=None, virtual_display=False,
                        cursor_engine="python", humanize=True, session_seed=4242)
    # Swiss French is the second keyboard Windows installs with Swiss German.
    assert prefs[KEYBOARD_LAYOUT_PREF] == "00000807,0000100C"


def test_a_russian_session_has_english_as_its_second_keyboard():
    """Windows installs English (US) with Russian: the persona types a Latin
    letter on it, so the pref carries both, the language's first."""
    from invisible_core import SessionLocale, generate_profile
    from invisible_playwright._juggler.server import KEYBOARD_LAYOUT_PREF
    from invisible_playwright._session import build_prefs
    prefs = build_prefs(profile=generate_profile(seed=4242), locale=SessionLocale(("ru-RU", "ru"), "RU"),
                        timezone="Europe/Moscow", extra_prefs=None, virtual_display=False,
                        cursor_engine="python", humanize=True, session_seed=4242)
    assert prefs[KEYBOARD_LAYOUT_PREF] == "00000419,00000409"


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
    browser.layout = "00000410"
    browser.actions_for_page("S", lifecycle=None, injected=None, number=1)
    assert seen["layout"] == "00000410"
    assert isinstance(seen["acts"], PageActs) and seen["session_seed"] == 4242


def test_the_default_is_the_us_keyboard():
    """A browser built without a layout types on the US keyboard, as before."""
    import inspect
    sig = inspect.signature(BrowserDispatcher.__init__)
    assert sig.parameters["layout"].default == "us"  # the alias of 00000409
