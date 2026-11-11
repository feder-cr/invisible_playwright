"""`request.resource_type` agrees with the pinned Playwright driver for every cause the engine reports.

The table that turns a Juggler `cause` (the name of an nsIContentPolicy constant) into a Playwright resource type was a copy of the
driver's, and it went stale: the pinned driver (the `playwright==` of the dev extra, scripts/playwright_pin.txt) says `beacon` for
TYPE_BEACON and `cspreport` for TYPE_CSP_REPORT, ours said `other` for both, so a beacon was an `other` request to a client and to a
route filter, where stock Firefox shows `beacon` (measured on cnbc.com and scmp.com: the same POSTs, 2 and 8 per page, `other`
here and `beacon` on stock). The test reads the driver's own table from the installed package (no import of it), so the next driver
version that changes it fails here, and nothing else needs updating by hand.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

from invisible_playwright._juggler import _marshal

pytestmark = pytest.mark.unit


def _driver_table():
    spec = importlib.util.find_spec("playwright")
    if spec is None or not spec.submodule_search_locations:
        pytest.fail("the pinned Playwright driver is not installed: the dev extra pins it", pytrace=False)
    bundle = pathlib.Path(list(spec.submodule_search_locations)[0]) / "driver" / "package" / "lib" / "coreBundle.js"
    if not bundle.exists():
        pytest.fail("the pinned driver has no coreBundle.js at %s" % bundle, pytrace=False)
    text = bundle.read_text(encoding="utf-8", errors="replace")
    start = text.index("TYPE_INVALID:")
    block = text[text.rindex("{", 0, start): text.index("};", start)]
    return dict(re.findall(r'(TYPE_[A-Z_]+):\s*"([a-z]+)"', block))


def test_every_cause_the_driver_knows_has_the_same_resource_type_here():
    driver = _driver_table()
    assert len(driver) >= 20, driver
    ours = {cause: _marshal._resource_type({"cause": cause}) for cause in driver}
    assert ours == driver, {c: (ours[c], driver[c]) for c in driver if ours[c] != driver[c]}


def test_a_beacon_is_a_beacon_and_a_csp_report_a_cspreport():
    assert _marshal._resource_type({"cause": "TYPE_BEACON"}) == "beacon"
    assert _marshal._resource_type({"cause": "TYPE_CSP_REPORT"}) == "cspreport"
