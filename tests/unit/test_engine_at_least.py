"""engine_at_least (tests/conftest.py): which engines a test runs on. No browser."""
import pytest
from _pytest.outcomes import Failed, Skipped

import conftest

pytestmark = pytest.mark.unit


def _check(tag, minimum="firefox-64"):
    conftest._skip_unless_engine_at_least(minimum, tag)


def test_an_older_engine_is_skipped_with_the_reason():
    with pytest.raises(Skipped, match="needs firefox-64 or later; this engine is firefox-58"):
        _check("firefox-58")


@pytest.mark.parametrize("tag", ["firefox-64", "firefox-64-candidate", "firefox-65"])
def test_the_engine_that_promises_it_runs(tag):
    _check(tag)


def test_a_candidate_counts_as_its_number():
    with pytest.raises(Skipped):
        _check("firefox-59-candidate")


@pytest.mark.parametrize("tag", ["local", "my-build", "", None])
def test_a_seal_with_no_number_fails_and_says_why(tag):
    with pytest.raises(Failed, match="has no number: a candidate must be sealed as firefox-N-candidate"):
        _check(tag)


@pytest.mark.parametrize("tag", ["firefox-61", "firefox-61-candidate", "firefox-62", "firefox-62-candidate",
                                 "firefox-63", "firefox-63-candidate"])
def test_firefox_62_and_63_do_not_run_what_the_64_promises(tag):
    """firefox-62 is the Fission release (one process per site) and firefox-63 the coherent UDP; the shelved
    line with the idwr text, the colours, the maths and the media goals is firefox-64. A test that asks for 64
    must skip on 63 and 62 (and on 61), or they would run goals that line has not shipped."""
    with pytest.raises(Skipped, match="needs firefox-64 or later; this engine is " + tag):
        _check(tag)
