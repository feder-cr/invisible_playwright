"""The typing seed reaches the engine client, and stops there.

⛔ THE PROBLEM THIS SOLVES. The engine client has no seed: it is built by a
transport that knows nothing about a session. The rhythm has to be per-session
or it is a constant shared by every install, which is the linkage key
`19-cursor-signature.md` is about.

⛔ AND THE TWO SHAPES THAT WERE REJECTED, written down because both look
reasonable and both are wrong. A module-level value is wrong the moment one
process holds two sessions: the second would type with the first's hand. And
deriving it from `zoom.stealth.fpp.hw_seed`, which already travels, is wrong
because that value has about twenty possibilities, so unrelated sessions would
share a rhythm - the very thing being removed.

What is left is the pref dict: the launcher composes it in full, `op_launch`
already receives it, and the key comes back out before `user.js` is written so
no session identifier reaches the disk.
"""
from __future__ import annotations

from invisible_core._fpforge import generate_profile
from invisible_playwright._behaviour import TypingPersona
from invisible_playwright._cursor import ENGINE_PYTHON
from invisible_playwright._juggler.server import (
    TYPING_SEED_PREF, take_typing_persona,
)
from invisible_playwright._session import build_prefs


def _prefs(**kw):
    base = dict(profile=generate_profile(42, None), locale="en-US",
                timezone="", extra_prefs=None, headless=False,
                virtual_display=False, cursor_engine=ENGINE_PYTHON,
                humanize=True, typing_seed=42)
    base.update(kw)
    return build_prefs(**base)


def test_the_seed_rides_in_the_launch_prefs():
    assert _prefs()[TYPING_SEED_PREF] == 42


def test_turning_humanising_off_sends_no_hand():
    """A caller who asked for a machine gets one. Giving them a hand on the
    keyboard anyway would be a second answer to a question they answered."""
    assert TYPING_SEED_PREF not in _prefs(humanize=False)


def test_the_pref_namespace_is_not_the_binarys():
    """⛔ `stealthfox.*` means "the patched binary reads this". A typing key
    under that prefix would send the next reader into C++ looking for something
    answered in Python."""
    assert not TYPING_SEED_PREF.startswith("stealthfox.")
    assert not TYPING_SEED_PREF.startswith("zoom.stealth.")


def test_the_key_comes_back_out_before_the_profile_is_written():
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE. A version that built the persona and
    left the key in the dict would behave identically in every observable way
    except for writing a session identifier into the profile - which no test
    that drives a browser would ever notice.

    To watch it fail, change `rest.pop` to `rest.get` in `take_typing_persona`.
    """
    rest, persona = take_typing_persona({TYPING_SEED_PREF: 42,
                                         "network.cookie.cookieBehavior": 0})
    assert TYPING_SEED_PREF not in rest
    assert rest == {"network.cookie.cookieBehavior": 0}
    assert isinstance(persona, TypingPersona)


def test_without_the_key_there_is_no_hand_and_nothing_is_lost():
    rest, persona = take_typing_persona({"network.cookie.cookieBehavior": 0})
    assert persona is None
    assert rest == {"network.cookie.cookieBehavior": 0}


def test_the_persona_that_arrives_is_the_one_the_seed_names():
    """The two ends have to agree, or the session would type with a hand
    nobody drew."""
    _, persona = take_typing_persona({TYPING_SEED_PREF: 4242})
    assert persona == TypingPersona.from_seed(4242)


def test_a_malformed_seed_raises_instead_of_typing_at_pipe_speed():
    """⛔ Falling back to no rhythm would make the defect the quiet default
    whenever the launcher sent something unexpected, and nothing would say so.
    """
    import pytest
    with pytest.raises((ValueError, TypeError)):
        take_typing_persona({TYPING_SEED_PREF: "not a number"})
