"""The fake web, profile P2: ONE routed page reused for every address reaches `networkidle` on every navigation, as stock does.

A page is difficult in the way real ad-heavy pages are: beacons and keepalive fetches fired while a document or a frame is
being left, slow fetches the page aborts among them, frames removed or sent to about:blank. The failure is a RATE, not a
fixed page: one request whose end the client never hears holds every later address of the same page, canaries included.
So each hard page is loaded several times in a row, each followed by the quiet canary, and not one load may be left without
`networkidle`. The outcome stock Firefox has on each page is declared in tests/fakeweb/pages.json; scripts/run_fakeweb.py
runs stock in the same job and is the CI entry.
"""
from __future__ import annotations

import importlib
import json
import pathlib

import pytest

from fakeweb import profile_p2, sites

pytestmark = pytest.mark.e2e

HERE = pathlib.Path(__file__).resolve().parent / "fakeweb"
HARD = ["leaving_beacons_subframe", "leaving_beacons", "cancel_among_beacons"]
REPETITIONS = 10


def _stock_client():
    """The stock Playwright client (a pinned dev dependency), imported by name: the fork's guard forbids a static import."""
    try:
        return importlib.import_module("playwright.sync_api").sync_playwright
    except ImportError as exc:      # a broken environment, not a reason to skip: a skipped gate checks nothing
        pytest.fail("the stock Playwright client is missing (%s): install the wrapper with its dev extra" % exc,
                    pytrace=False)


def test_every_hard_page_reaches_networkidle_every_time_on_a_reused_routed_page(firefox_binary):
    declared = json.loads((HERE / "pages.json").read_text(encoding="utf-8"))["stock_firefox"]
    assert all(declared[p] == "idle" for p in HARD), "this test holds the pages stock idles on"
    origin, _other, stop = sites.start((0, 0))
    try:
        cells = profile_p2.run_on_candidate(_stock_client(), firefox_binary, origin, HARD, REPETITIONS)
    finally:
        stop()
    bad = ["round %d %s%s: %s" % (c["round"], c["pattern"], " (canary after %s)" % c["after"] if c["canary"] else "", c["err"])
           for c in cells if not c["idle"]]
    assert not bad, "%d of %d navigations did not reach networkidle:\n  %s" % (len(bad), len(cells), "\n  ".join(bad[:12]))
