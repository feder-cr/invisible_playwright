"""The session language is the core's decision, and this package only reads it.

The server hands the engine the language LIST a context locale stands for: the
e2e half, and the measurement behind it, is `test_context_locale_e2e.py`. Here
only the command: `Browser.setLocaleOverride` carries what the core's
`decide_session_locale(tag).accept_languages` gives, never the bare tag,
because the engine splits navigator.languages out of that value and prepares
the Accept-Language header from it.

And the other half of the rule: since invisible-core 36.32.0 the core decides
the language once (`prepare_session_geo(...).locale`, a SessionLocale) and
this package carries no decision of its own. The guards below fail when an
"auto" branch or one of the removed core names comes back into the source.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from invisible_core import SessionGeo, SessionLocale, decide_session_locale
from invisible_playwright._juggler.connection import EventListeners
from invisible_playwright._juggler.server import BrowserDispatcher, Server

_PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "invisible_playwright"


class _Connection(EventListeners):
    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params))
        return {"browserContextId": "CTX"}


def _locale_commands(params):
    conn = _Connection()
    BrowserDispatcher(Server(), None, conn, "151.0")._apply_context_options(
        "CTX", params)
    return [p for m, p in conn.sent if m == "Browser.setLocaleOverride"]


@pytest.mark.unit
@pytest.mark.parametrize("locale", ["de-DE", "it-IT", "en-US", "en-GB", "pt_BR"])
def test_the_locale_goes_out_as_the_core_list(locale):
    assert _locale_commands({"locale": locale}) == [
        {"browserContextId": "CTX",
         "locale": decide_session_locale(locale).accept_languages}]


@pytest.mark.unit
def test_de_de_is_the_list_a_german_firefox_reports():
    # Written out once, so a change in the core's table shows up here as a
    # decision rather than passing through silently.
    assert _locale_commands({"locale": "de-DE"})[0]["locale"] == "de-DE, de, en-US, en"


@pytest.mark.unit
def test_no_locale_sends_nothing():
    """The context then keeps the session's list, seeded by the engine."""
    assert _locale_commands({}) == []


@pytest.mark.unit
def test_a_context_locale_of_auto_is_refused_without_the_network(monkeypatch):
    """"auto" is decided once, at launch, from the egress. This side does not
    know the proxy, so deciding it here would discover the HOST's address and
    pair the home country's language with the proxy's timezone. Known-bad:
    the server calling the core's launch decision instead of SessionLocale.of."""
    from invisible_core import _geo

    def _no_network(*_a, **_k):
        raise AssertionError("a context locale must not discover anything")
    monkeypatch.setattr(_geo, "discover_egress_ip", _no_network)
    with pytest.raises(ValueError, match="auto"):
        _locale_commands({"locale": "auto"})


# -- the default context reads the session decision --------------------------

def _decided(cls, requested):
    """A session past the geo step of __enter__, without the network."""
    obj = cls(seed=42, timezone="Europe/London", locale=requested)
    obj._locale = SessionGeo("Europe/London", None,
                             locale=decide_session_locale(requested)).locale
    return obj


@pytest.mark.unit
@pytest.mark.parametrize("api", ["sync", "async"])
@pytest.mark.parametrize("requested", ["en-AU", "fr-FR", "de-DE", "it-IT", "en-GB"])
def test_the_default_context_locale_is_the_decision_s_primary(api, requested):
    """`new_context` gets ONE tag, and it is the one navigator.language reports.

    An Australian session decides `en-US, en` (Firefox has no en-AU build); a
    default context carrying the requested "en-AU" would have been the second
    answer the 36.32.0 core removed.
    """
    if api == "sync":
        from invisible_playwright import InvisiblePlaywright as cls
    else:
        from invisible_playwright.async_api import InvisiblePlaywright as cls
    obj = _decided(cls, requested)
    assert isinstance(obj._locale, SessionLocale)
    assert obj._default_context_kwargs()["locale"] == obj._locale.primary


@pytest.mark.unit
def test_an_australian_session_s_default_context_is_en_us():
    from invisible_playwright import InvisiblePlaywright

    obj = _decided(InvisiblePlaywright, "en-AU")
    assert obj._default_context_kwargs()["locale"] == "en-US"


@pytest.mark.unit
@pytest.mark.parametrize("requested", [
    "en-AU", "en-ZA", "en-CA", "fr-BE", "de-CH", "he-IL", "ja-JP", "zh-CN",
    "zh-TW", "nb-NO", "ro-RO", "pt-BR", "es-MX", "sl-SI"])
def test_the_default_context_gets_back_the_list_the_profile_declared(requested):
    """primary -> the server -> the engine must land on the launch list.

    The default context carries `primary`; the server turns it into a list with
    the same core decision. If the two lists differed, the first context of a
    session would declare a different language list from its own prefs.
    """
    session = decide_session_locale(requested)
    assert _locale_commands({"locale": session.primary}) == [
        {"browserContextId": "CTX", "locale": session.accept_languages}]


@pytest.mark.unit
def test_before_enter_there_is_no_decision_and_no_context_locale():
    from invisible_playwright import InvisiblePlaywright

    obj = InvisiblePlaywright(seed=42, locale="de-DE")
    assert obj._locale is None
    assert "locale" not in obj._default_context_kwargs()


# -- no locale decision of its own ---------------------------------------------

_REMOVED_CORE_NAMES = {"resolve_session_locale", "consent_region_lang",
                       "accept_languages"}


def _sources():
    for path in sorted(_PKG_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or "_pw" in path.relative_to(_PKG_DIR).parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_no_module_imports_a_removed_locale_name():
    offenders = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            # `SessionLocale.accept_languages` is the property to read; what
            # must not come back is the core MODULE's function of that name.
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("invisible_core"):
                names = {a.name for a in node.names}
            elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                  and node.value.id == "invisible_core"):
                names = {node.attr}
            else:
                continue
            for name in names & _REMOVED_CORE_NAMES:
                offenders.append(f"{path.name}:{node.lineno} {name}")
    assert not offenders, offenders


def _mentions_locale(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and "locale" in sub.id.lower():
            return True
        if isinstance(sub, ast.Attribute) and "locale" in sub.attr.lower():
            return True
    return False


@pytest.mark.unit
def test_no_module_compares_a_locale_with_auto():
    """The `if locale == "auto"` branch lives in the core only.

    Every consumer used to carry one around `resolve_session_locale`; any
    comparison between something named like a locale and the string "auto"
    is that branch coming back.
    """
    offenders = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            consts = [n for n in [node.left, *node.comparators]
                      if isinstance(n, ast.Constant) and n.value == "auto"]
            if consts and _mentions_locale(node):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


@pytest.mark.unit
def test_the_launch_hands_the_requested_locale_to_the_one_geo_call():
    """Both entry points make ONE prepare_session_geo call and pass the request."""
    for name in ("launcher.py", "async_api.py"):
        tree = ast.parse((_PKG_DIR / name).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and any(
            isinstance(a, ast.Name) and a.id == "prepare_session_geo"
            for a in [n.func, *n.args])]
        assert len(calls) == 1, name
        assert any(isinstance(a, ast.Attribute) and a.attr == "_locale_requested"
                   for a in calls[0].args), name
