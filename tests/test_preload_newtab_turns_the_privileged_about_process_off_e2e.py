"""``preload_newtab=False`` leaves no privileged-about process; without the option the preloaded New Tab page has one.

A retail Firefox keeps a New Tab page preloaded in a process of its own (remote type ``privilegedabout``, about 200 MB). The core writes nothing
by default, so the session has it; ``preload_newtab=False`` declares ``browser.newtab.preload`` false and the process is not born (measured on the
candidate engine, three runs of three). The processes are read from the engine's own log, which names the type of every process it creates
(``Setting remoteType of process <pid> to <type>``): it needs ``MOZ_LOG=Process:5,timestamp,sync`` and a file base before the launch, and it holds
on every platform. The assertion is on ``privilegedabout`` only: a Mozilla site's process beside it depends on the country of the machine.
"""
import inspect
import re
import time

import pytest

TYPE = re.compile(r"Setting remoteType of process (\d+) to (\S+)")


def _remote_types(tmp_path, binary, **option):
    from invisible_playwright import InvisiblePlaywright

    mp = pytest.MonkeyPatch()
    base = tmp_path / ("proc-%s" % ("-".join("%s-%s" % kv for kv in sorted(option.items())) or "default"))
    mp.setenv("MOZ_LOG", "Process:5,timestamp,sync")
    mp.setenv("MOZ_LOG_FILE", str(base))
    try:
        with InvisiblePlaywright(seed=7, binary_path=binary, headless=True, humanize=False, locale="en-US",
                                 timezone="America/New_York", **option) as browser:
            page = browser.new_context().new_page()
            page.goto("about:blank")
            time.sleep(10)
    finally:
        mp.undo()
    types = []
    for path in sorted(tmp_path.glob(base.name + "*")):
        types += [m.group(2) for m in TYPE.finditer(path.read_text(encoding="utf-8", errors="replace"))]
    return types


@pytest.mark.e2e
def test_without_the_option_the_new_tab_page_is_preloaded_in_its_own_process(firefox_binary, tmp_path):
    types = _remote_types(tmp_path, firefox_binary)
    assert types, "the engine wrote no line about its processes: the control cannot judge"
    assert "privilegedabout" in types, "the retail keeps the New Tab page preloaded: %s" % sorted(set(types))


@pytest.mark.e2e
def test_asked_to_turn_the_preload_off_there_is_no_privileged_about_process(firefox_binary, tmp_path):
    from invisible_core import prefs as core_prefs

    if "preload_newtab" not in inspect.signature(core_prefs.compose_session_prefs).parameters:
        pytest.skip("this core does not know preload_newtab yet")
    types = _remote_types(tmp_path, firefox_binary, preload_newtab=False)
    assert types, "the engine wrote no line about its processes: the control cannot judge"
    assert "privilegedabout" not in types, "a process of the preloaded New Tab page was born: %s" % sorted(set(types))
