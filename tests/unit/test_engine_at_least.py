"""engine_at_least (tests/conftest.py): which engines a test runs on. No browser."""
import pytest
from _pytest.outcomes import Failed, Skipped

import conftest

pytestmark = pytest.mark.unit


def _check(tag, minimum="firefox-61"):
    conftest._skip_unless_engine_at_least(minimum, tag)


def test_an_older_engine_is_skipped_with_the_reason():
    with pytest.raises(Skipped, match="needs firefox-61 or later; this engine is firefox-58"):
        _check("firefox-58")


@pytest.mark.parametrize("tag", ["firefox-61", "firefox-61-candidate", "firefox-62"])
def test_the_engine_that_promises_it_runs(tag):
    _check(tag)


def test_a_candidate_counts_as_its_number():
    with pytest.raises(Skipped):
        _check("firefox-59-candidate")


@pytest.mark.parametrize("tag", ["local", "my-build", "", None])
def test_a_seal_with_no_number_fails_and_says_why(tag):
    with pytest.raises(Failed, match="has no number: a candidate must be sealed as firefox-N-candidate"):
        _check(tag)


@pytest.mark.parametrize("tag", ["firefox-60", "firefox-60-candidate"])
def test_firefox_60_does_not_run_what_the_61_promises(tag):
    """firefox-60 is the next release of the engine (firefox-59 plus the Juggler memory fixes); the
    shelved line with the idwr text, the colours, the maths and the media goals is firefox-61. A test
    that asks for 61 must skip on 60, or the 60 would run goals that line has not shipped."""
    with pytest.raises(Skipped, match="needs firefox-61 or later; this engine is " + tag):
        _check(tag)
