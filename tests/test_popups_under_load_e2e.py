"""Popups under load: the matrix of test_popups_reach_the_client_e2e.py, REPEATS times, with four browsers at once.

Why this file exists: a popup could be announced while still on its initial about:blank, and the navigation to its destination then
never reached the client (its url stays about:blank after `load`, or `load` never comes). That happens only when several browsers
run at once: with one browser the matrix was green; with four pytest workers on windows-2022 it was red in 11 runs of 20 on the
engine 63 (13 cases of 640) and in 3 of 20 on the published engine, always a case with no route. The required suite that runs this
file runs it with four workers (`-n 4 --dist load`): the first suite of the gate that needs several processes for one file,
because the defect is only seen with several browsers together.

REPEATS is chosen so that, at the rate measured on the engine 63 (13 cases of 640, about 2%), a run with the defect is red all but
certainly: 8 repeats are 256 cases, and the chance that all of them pass with the defect is 0.98^256, about 0.6%.

Each repeat is the same test function of the matrix, with the same module-level browser per transport (one per worker): the load is
four browsers opening popups at the same time, not more browsers.
"""
from __future__ import annotations

import pytest

import test_popups_reach_the_client_e2e as matrix

pytestmark = pytest.mark.e2e

REPEATS = 8

# The local site of the matrix, for this module. The browser is the shared one of conftest.py: one per transport for the whole
# module, so one per worker.
site = matrix.site


@pytest.mark.parametrize("repeat", range(1, REPEATS + 1), ids=lambda n: "r%d" % n)
@pytest.mark.parametrize("routed", [False, True], ids=["no-route", "route"])
@pytest.mark.parametrize("target", ["same-site", "other-site"])
@pytest.mark.parametrize("kind", sorted(matrix.KINDS))
def test_a_popup_reaches_the_client_under_load(browser, site, kind, target, routed, repeat):
    matrix.test_a_popup_reaches_the_client(browser, site, kind, target, routed)
