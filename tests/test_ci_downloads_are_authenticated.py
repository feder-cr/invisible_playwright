"""Every workflow that runs the suite downloads the engine with the job's token.

On 2026-11-08 GitHub had timeouts for unauthenticated users, and the gates
here failed three times on the engine download, because the jobs called the
release API anonymously. The core uses GITHUB_TOKEN when it is set, so the fix
is declaring it once, at the top of each workflow that runs tests.
user-install.yml is the exception: it is the user's path, and users download
anonymously.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
ANONYMOUS_ON_PURPOSE = {"user-install.yml"}
RUNS_TESTS = re.compile(r"pytest|run_e2e\.py")
TOP_LEVEL_TOKEN = re.compile(r"^env:\n(?:[ ]{2}.*\n)*?[ ]{2}GITHUB_TOKEN: \$\{\{ github\.token \}\}\n",
                             re.MULTILINE)


def test_every_workflow_that_runs_tests_declares_the_token():
    missing = [
        p.name for p in sorted(WORKFLOWS.glob("*.yml"))
        if p.name not in ANONYMOUS_ON_PURPOSE
        and RUNS_TESTS.search(p.read_text(encoding="utf-8"))
        and not TOP_LEVEL_TOKEN.search(p.read_text(encoding="utf-8"))
    ]
    assert not missing, ("these workflows run tests that download the engine "
                         "anonymously; declare `env: GITHUB_TOKEN: ${{ github.token }}` "
                         "at the top: " + ", ".join(missing))


def test_the_user_path_stays_anonymous_at_the_top():
    text = (WORKFLOWS / "user-install.yml").read_text(encoding="utf-8")
    assert not TOP_LEVEL_TOKEN.search(text)
