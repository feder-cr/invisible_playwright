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


def _web_processes(root_pid):
    """The pids of the engine's web content processes (`-contentproc ... tab`) below `root_pid`."""
    import psutil

    found = set()
    try:
        children = psutil.Process(root_pid).children(recursive=True)
    except psutil.Error:
        return found
    for process in children:
        try:
            cmd = process.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd and cmd[-1] == "tab":
            found.add(process.pid)
    return found


def _quiet(root_pid, seconds=3.0, limit=30.0):
    """The processes once their set has not changed for `seconds`."""
    import time

    end = time.time() + limit
    last, since = _web_processes(root_pid), time.time()
    while time.time() < end:
        time.sleep(0.25)
        now = _web_processes(root_pid)
        if now != last:
            last, since = now, time.time()
        elif time.time() - since >= seconds:
            break
    return last


def test_the_other_origin_of_the_fake_web_is_another_site_in_another_process(firefox_binary):
    """The page origin and the other origin must be two SITES (127.0.0.1 and localhost), or a frame of the other origin stays in the
    page's process and the fake web can never exercise a request of a frame of another process (the third cause of the stalled
    networkidle). With the spares off every new content process was started for a page: the page `ad_iframes` has frames of the
    other origin, so loading it must add at least one."""
    from invisible_playwright._juggler.remote import launch_server

    sync_playwright = _stock_client()
    origin, other, stop = sites.start((0, 0))
    server = launch_server(headless=True, binary_path=firefox_binary, locale="en-US", timezone="Europe/Rome",
                           extra_prefs={"dom.ipc.processPrelaunch.enabled": False})
    try:
        assert origin.split(":")[1] != other.split(":")[1], "A and O are the same host: %s %s" % (origin, other)
        with sync_playwright() as p:
            browser = p.firefox.connect(server.ws_endpoint)
            context = browser.new_context()
            page = context.new_page()
            page.goto(origin + "/p/quiet", wait_until="load")
            before = _quiet(server.process.pid)
            page.goto(origin + "/p/ad_iframes", wait_until="load")
            page.wait_for_timeout(1500)
            after = _quiet(server.process.pid)
            context.close()
            browser.close()
    finally:
        server.close()
        stop()
    assert len(after - before) >= 1, "the frames of the other origin added no process: before %s, after %s" % (
        sorted(before), sorted(after))


LOADERS = 3


def test_the_page_that_leaves_with_beacons_reaches_networkidle_every_time_under_a_load_of_other_browsers(firefox_binary):
    """MANDATORY CASE. `leaving_beacons_subframe` sends beacons from a frame of another site while it is sent to about:blank. With the
    context route holding them and other browsers loading pages in the background (the declared load: three loaders of ours, each a
    browser reloading an ad-heavy page every two seconds), a beacon's channel can be cancelled with its document after the route resumed
    it, and an engine that does not end that request leaves the page waiting for ever. Quiet, it passes; loaded, Playwright's own Firefox
    idles 30 rounds of 30 and an engine without the end of a cancelled resumed request idled 1 of 30. All ten rounds must idle."""
    import subprocess
    import sys

    loader = pathlib.Path(__file__).resolve().parent / "fakeweb" / "loader.py"
    procs = [subprocess.Popen([sys.executable, str(loader), firefox_binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for _ in range(LOADERS)]
    origin, _other, stop = sites.start((0, 0))
    try:
        import time

        time.sleep(20)                     # the loaders are up and loading
        cells = profile_p2.run_on_candidate(_stock_client(), firefox_binary, origin, ["leaving_beacons_subframe"], 10)
    finally:
        stop()
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
    bad = ["round %d%s: %s -- in flight: %s" % (c["round"], " (canary)" if c["canary"] else "", c["err"],
                                                 [(r["type"], r["method"], r["frame"]) for r in c.get("in_flight", [])[:3]])
           for c in cells if not c["idle"]]
    assert not bad, "%d of %d navigations did not reach networkidle under load:%s" % (
        len(bad), len(cells), "".join("\n  " + line for line in bad[:6]))
