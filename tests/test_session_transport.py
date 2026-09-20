"""What the launcher tells the engine client about the SESSION, and how.

⛔ THE PROBLEM THIS SOLVES. The engine client has no session: it is built by a
transport that knows nothing about one. Two facts have to reach it anyway - the
seed its rhythms are drawn from, and the budget one movement may spend - or each
becomes a constant shared by every install, which is the linkage key
`19-cursor-signature.md` is about.

⛔ AND THE TWO SHAPES THAT WERE REJECTED for the seed, written down because both
look reasonable and both are wrong. A module-level value is wrong the moment one
process holds two sessions: the second would type with the first's hand. And
deriving it from `zoom.stealth.fpp.hw_seed`, which already travels, is wrong
because that value has about twenty possibilities, so unrelated sessions would
share a rhythm - the very thing being removed.

What is left is the pref dict: the launcher composes it in full, `op_launch`
already receives it, and the keys come back out before `user.js` is written so
no session identifier reaches the disk.
"""
from __future__ import annotations

import pytest

from invisible_core._fpforge import generate_profile
from invisible_playwright._behaviour import TypingPersona
from invisible_playwright._cursor import ENGINE_PYTHON, max_seconds_for
from invisible_playwright._juggler.server import (
    MOTION_BUDGET_PREF, SESSION_SEED_PREF, take_session_motion,
)
from invisible_playwright._session import build_prefs


def _prefs(**kw):
    base = dict(profile=generate_profile(42, None), locale="en-US",
                timezone="", extra_prefs=None,
                virtual_display=False, cursor_engine=ENGINE_PYTHON,
                humanize=True, session_seed=42)
    base.update(kw)
    return build_prefs(**base)


# ── the seed ────────────────────────────────────────────────────────────────

def test_the_seed_rides_in_the_launch_prefs():
    assert _prefs()[SESSION_SEED_PREF] == 42


def test_turning_humanising_off_sends_no_hand():
    """A caller who asked for a machine gets one. Giving them a hand on the
    keyboard anyway would be a second answer to a question they answered."""
    assert SESSION_SEED_PREF not in _prefs(humanize=False)


def test_the_pref_namespace_is_not_the_binarys():
    """⛔ `stealthfox.*` means "the patched binary reads this". A session key
    under that prefix would send the next reader into C++ looking for something
    answered in Python."""
    for key in (SESSION_SEED_PREF, MOTION_BUDGET_PREF):
        assert not key.startswith("stealthfox.")
        assert not key.startswith("zoom.stealth.")


def test_the_keys_come_back_out_before_the_profile_is_written():
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE. A version that read these and left
    them in the dict would behave identically in every observable way except for
    writing a session identifier into the profile - which no test that drives a
    browser would ever notice.

    To watch it fail, change either `rest.pop` to `rest.get` in
    `take_session_motion`.
    """
    rest, seed, budget = take_session_motion({
        SESSION_SEED_PREF: 42,
        MOTION_BUDGET_PREF: 1500,
        "network.cookie.cookieBehavior": 0})
    assert SESSION_SEED_PREF not in rest
    assert MOTION_BUDGET_PREF not in rest
    assert rest == {"network.cookie.cookieBehavior": 0}
    assert seed == 42
    assert budget == 1.5


def test_without_the_keys_there_is_no_hand_and_nothing_is_lost():
    rest, seed, budget = take_session_motion({"network.cookie.cookieBehavior": 0})
    assert seed is None
    assert budget is None
    assert rest == {"network.cookie.cookieBehavior": 0}


def test_the_persona_that_arrives_is_the_one_the_seed_names():
    """The two ends have to agree, or the session would type with a hand
    nobody drew."""
    _, seed, _ = take_session_motion({SESSION_SEED_PREF: 4242})
    assert seed == 4242
    assert TypingPersona.from_seed(seed) == TypingPersona.from_seed(4242)


def test_a_malformed_seed_raises_instead_of_typing_at_pipe_speed():
    """⛔ Falling back to no rhythm would make the defect the quiet default
    whenever the launcher sent something unexpected, and nothing would say so.
    """
    with pytest.raises((ValueError, TypeError)):
        take_session_motion({SESSION_SEED_PREF: "not a number"})


# ── the motion budget ───────────────────────────────────────────────────────

def test_the_budget_rides_along_and_is_the_one_the_caller_asked_for():
    """⛔ THE DEFECT THIS CLOSES. The client caps every movement it makes at
    `humanize=<seconds>`; the drag's travel is generated on the SERVER, where
    that number did not exist. So a caller who asked for short movements got
    them everywhere except in a drag, silently."""
    assert _prefs(humanize=0.4)[MOTION_BUDGET_PREF] == 400


def test_the_budget_is_derived_and_not_a_second_opinion():
    """One function decides what `humanize=` means. A server-side default would
    be a second answer to a question the caller already answered."""
    for humanize in (True, 0.25, 2.5):
        assert (_prefs(humanize=humanize)[MOTION_BUDGET_PREF]
                == round(max_seconds_for(humanize) * 1000.0))


def test_the_budget_is_an_int_because_gecko_has_no_float_pref():
    """⛔ `ui.textScaleFactor` written as a float arrived with the right value
    and the wrong type, and killed the browser on the second context. A pref
    that looks numeric is not the same as a pref the engine can hold."""
    value = _prefs()[MOTION_BUDGET_PREF]
    assert isinstance(value, int) and not isinstance(value, bool)


def test_turning_humanising_off_sends_no_budget():
    assert MOTION_BUDGET_PREF not in _prefs(humanize=False)


def test_a_malformed_budget_raises():
    with pytest.raises((ValueError, TypeError)):
        take_session_motion({MOTION_BUDGET_PREF: "soon"})
