"""The workflows that run the wrapper's tests launch with the GeoIP database of ci/geoip.json; the release alone looks at the real one.

Without STEALTHFOX_GEOIP_MMDB the core asks GitHub which database is the latest on every call (run 43211837926). tests.yml, e2e.yml and webrtc-e2e.yml make
the database (scripts/ci_lab_geoip.py: the same bytes every run, no download) BEFORE the step that runs pytest. user-install.yml proves the installation a user
gets and is left as it is. publish.yml is the release: it runs the three checks on the REAL database (scripts/ci_real_geoip_check.py) before it uploads, and
nothing else touches it.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
BUILD = "scripts/ci_lab_geoip.py build"
REAL = "scripts/ci_real_geoip_check.py"
RUNS_PYTEST = {
    "tests.yml": "      - name: Run pytest\n",
    "e2e.yml": "      - name: Run the full e2e suite under a virtual display\n",
    "webrtc-e2e.yml": "      - name: Run WebRTC realness e2e (xvfb for the headless Firefox)\n",
}


@pytest.mark.parametrize("name, pytest_step", sorted(RUNS_PYTEST.items()))
def test_the_database_is_made_before_the_step_that_runs_the_tests(name, pytest_step):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert text.count(BUILD) == 1, "%s makes the GeoIP database %d times" % (name, text.count(BUILD))
    assert pytest_step in text, "the step that runs the tests of %s moved: update this test with it" % name
    assert text.index(BUILD) < text.index(pytest_step), "%s makes the database after the tests started" % name
    assert REAL not in text, "the real database is the release's alone"


def test_the_workflow_that_proves_the_installation_is_left_alone():
    text = (WORKFLOWS / "user-install.yml").read_text(encoding="utf-8")
    assert BUILD not in text and REAL not in text


def test_the_release_checks_the_real_database_before_it_uploads_and_makes_no_lab_one():
    text = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    assert text.count("python " + REAL) == 1 and BUILD not in text
    assert "  real-geoip-database:\n" in text
    assert "needs: [already-published, gate, real-geoip-database]" in text, "the upload does not wait for the real database"
    # and no other workflow runs the real checks
    for other in sorted(WORKFLOWS.glob("*.yml")):
        if other.name != "publish.yml":
            assert REAL not in other.read_text(encoding="utf-8"), "%s runs the checks of the release" % other.name


@pytest.mark.parametrize("name, pytest_step", sorted(RUNS_PYTEST.items()))
def test_the_leg_verifies_after_the_tests_that_it_ran_with_the_pinned_database(name, pytest_step):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    step = "      - name: The leg ran with the pinned GeoIP database\n        if: always()\n"
    assert text.count(step) == 1 and "ci_lab_geoip.py verify" in text, name
    assert text.index(pytest_step) < text.index(step), "%s verifies before the tests ran" % name
