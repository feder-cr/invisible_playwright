"""A persistent profile keeps what a session wrote, for the next session.

⛔ MEASURED 2026-09-05 AGAINST THE PUBLISHED 0.12.0: IT KEPT NOTHING. Two
sessions on the same `profile_dir`; the first set a cookie and a localStorage
entry and read both back; the second read an empty cookie jar and `None`. On
disk, after either session, `cookies.sqlite` and `webappsstore.sqlite` held zero
rows and their WAL files were empty. The docs page on persistent profiles
promised "logging in once instead of every run", and listed two upstream
Playwright issues about vanishing cookies as things that are not your fault.
This one was ours.

The persistent launch handed back an ordinary context: `Browser.createBrowser
Context`, a Juggler container with a fresh userContextId, whose `destroy()`
calls `ContextualIdentityService.remove` and deletes the container's cookies and
storage with the identity. Everything a session wrote lived in a container that
died with the session. Upstream's persistent context is the DEFAULT one,
userContextId 0, whose state is the profile's; it is addressed by omitting
`browserContextId`, and it is never removed.

Two layers here. The pure one checks the addressing rule without a browser,
including the trap it exists for: `None` must become an ABSENT field, because
Juggler registers the default context under `undefined` and `Map.get(null)`
finds nothing. The e2e drives two sessions and reads the second one's state
from the page, and reads the cookie row from the profile on disk between them,
so a future regression cannot pass by keeping the data somewhere the page can
see and the disk cannot.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from invisible_playwright._juggler.server import _address


def test_the_default_context_is_addressed_by_absence_not_by_null():
    assert _address(None, {"cookies": []}) == {"cookies": []}
    # A caller that already wrote the key with None gets it REMOVED, not kept
    # as null: that is the exact form Juggler cannot resolve.
    assert "browserContextId" not in _address(None, {"browserContextId": None, "x": 1})


def test_a_created_context_is_addressed_by_its_id():
    out = _address("ctx-7", {"cookies": []})
    assert out == {"browserContextId": "ctx-7", "cookies": []}
    # and the id wins over whatever the caller wrote
    assert _address("ctx-7", {"browserContextId": "other"})["browserContextId"] == "ctx-7"


def test_the_input_dict_is_not_mutated():
    params = {"a": 1}
    _address("ctx-1", params)
    _address(None, params)
    assert params == {"a": 1}


def _rows(profile: str) -> list:
    path = os.path.join(profile, "cookies.sqlite").replace("\\", "/")
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return con.execute("select name, value from moz_cookies").fetchall()
    finally:
        con.close()


@pytest.mark.e2e
def test_cookies_and_local_storage_survive_to_the_next_session(firefox_binary):
    """⛔ BOTH STORES, AND THE DISK IN BETWEEN. A cookie alone could survive a
    fix that only moved cookies; the on-disk read separates "the second session
    saw it" from "it was actually persisted", which is the property a login
    relies on when the machine restarts.
    """
    from invisible_playwright import InvisiblePlaywright

    profile = tempfile.mkdtemp(prefix="invpw_persist_")
    with InvisiblePlaywright(headless=True, seed=11, binary_path=firefox_binary,
                             profile_dir=profile) as ctx:
        page = ctx.new_page()
        page.goto("https://example.com", timeout=60000)
        page.evaluate("() => { localStorage.setItem('kept', 'yes');"
                      " document.cookie = 'kept=yes; max-age=86400; SameSite=Lax'; }")
        assert page.evaluate("localStorage.getItem('kept')") == "yes"
        assert ("kept", "yes") in [(c["name"], c["value"]) for c in ctx.cookies()]

    rows = _rows(profile)
    assert ("kept", "yes") in rows, (
        "the cookie was visible to the session and never reached the profile: %r" % (rows,))

    with InvisiblePlaywright(headless=True, seed=11, binary_path=firefox_binary,
                             profile_dir=profile) as ctx:
        page = ctx.new_page()
        page.goto("https://example.com", timeout=60000)
        assert page.evaluate("localStorage.getItem('kept')") == "yes", "localStorage did not survive"
        assert "kept=yes" in page.evaluate("document.cookie"), "the cookie did not survive"
        assert ("kept", "yes") in [(c["name"], c["value"]) for c in ctx.cookies()]
