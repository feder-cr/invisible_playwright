"""Hunt scaffolding, never merged: repeats the tests that look at processes by the name of the site, under load.

It runs only from .github/workflows/site-names-hunt.yml (a push to a `hunt/site-names**` branch), with HUNT_REPS set.
The new tests are the bench's own functions called again and again; the old one is the comparison of two counts that
was red in 14 of 30 runs, to show that it still fails under the same load while the new ones do not.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import test_a_context_that_ends_leaves_no_process_e2e as sw
import test_fission_strategy1_bench as bench

pytestmark = pytest.mark.e2e

HUNT_REPS = int(os.environ.get("HUNT_REPS") or 0)

# The bench's fixtures, under the same names, without collecting the bench's tests a second time.
page = bench.page
browser = bench.browser
cross_site_loop = bench.cross_site_loop
engine_dir = bench.engine_dir


@pytest.mark.parametrize("rep", range(HUNT_REPS))
def test_hunt_new_same_site(rep, page, cross_site_loop, engine_dir):
    bench.test_same_site_other_port_stays_in_process(page, cross_site_loop, engine_dir)


@pytest.mark.parametrize("rep", range(HUNT_REPS))
def test_hunt_new_cross_site(rep, page, cross_site_loop, engine_dir):
    bench.test_cross_site_iframe_gets_its_own_process(page, cross_site_loop, engine_dir)


def _isolated_pids(engine_dir):
    """The old oracle: the pids of the engine's `-contentproc ... tab` children called `Isolated Web Co`."""
    import psutil

    pids = set()
    try:
        children = psutil.Process(os.getpid()).children(recursive=True)
    except psutil.Error:
        return pids
    for child in children:
        try:
            if not os.path.normcase(child.exe()).startswith(os.path.normcase(engine_dir)):
                continue
            name, cmd = child.name(), child.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd and cmd[-1] == "tab" and name == "Isolated Web Co":
            pids.add(child.pid)
    return pids


def _quiet(engine_dir, seconds=3.0, limit=30.0):
    """The set read once it has not changed for `seconds` (limit 30 s, poll 0.25 s)."""
    end = time.time() + limit
    last, since = _isolated_pids(engine_dir), time.time()
    while time.time() < end:
        time.sleep(0.25)
        now = _isolated_pids(engine_dir)
        if now != last:
            last, since = now, time.time()
        elif time.time() - since >= seconds:
            break
    return last


@pytest.mark.parametrize("rep", range(HUNT_REPS))
def test_hunt_old_same_site(rep, firefox_binary, cross_site_loop, engine_dir):
    """The old comparison, self-contained: two reads of the set of `Isolated Web Co` pids around a same-site frame."""
    from invisible_playwright import InvisiblePlaywright

    prefs = {"fission.autostart": True, "fission.webContentIsolationStrategy": 1, "dom.ipc.processCount.webIsolated": 4,
             "network.dns.localDomains": "a.test,b.test,c.test", "dom.ipc.processPrelaunch.fission.number": 0}
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True,
                             extra_prefs=prefs) as browser_:
        page_ = browser_.new_context().new_page()
        page_.set_default_timeout(3000)
        page_.set_default_navigation_timeout(20000)
        page_.goto(cross_site_loop["plain"])
        before = _quiet(engine_dir)
        assert len(before) >= 1, "the page of a site has no process of its own to compare with: %s" % sorted(before)
        page_.evaluate("(u) => { const f = document.createElement('iframe'); f.id = 's'; f.src = u;"
                       " document.body.appendChild(f); }", cross_site_loop["leaf"])
        assert bench.wait_for(page_, lambda: any(r["path"] == "/report/loaded" and r["query"]["path"] == ["/leaf"]
                                                 for r in cross_site_loop["reports"]))
        after = _quiet(engine_dir)
        assert after == before, "a same-site frame changed the processes of the sites: %s -> %s" % (
            sorted(before), sorted(after))


@pytest.mark.parametrize("rep", range(HUNT_REPS))
def test_hunt_service_worker(rep, firefox_binary, engine_at_least):
    sw.test_closed_contexts_with_a_never_stopping_service_worker_leave_no_process(firefox_binary, engine_at_least)


def test_hunt_names_seen(page, cross_site_loop, engine_dir):
    """Prints the real names of the engine's content processes once the cross-site page has run."""
    import psutil

    page.goto(cross_site_loop["top"], wait_until="load")
    assert bench.wait_for(page, lambda: any(r["path"] == "/report/loaded" and r["host"] == "a.test" and
                                            r["query"]["path"] == ["/leaf"] for r in cross_site_loop["reports"]))
    seen = []
    for child in psutil.Process(os.getpid()).children(recursive=True):
        try:
            if not os.path.normcase(child.exe()).startswith(os.path.normcase(engine_dir)):
                continue
            name, cmd = child.name(), child.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd:
            seen.append((name, cmd[-1]))
    seen.sort()
    print("HUNT names seen:", json.dumps(seen))
    where = os.environ.get("HUNT_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        with open(os.path.join(where, "names.json"), "w", encoding="utf-8") as out:
            json.dump(seen, out, indent=1)
