"""E2E: the engine never reads the host's hosts file (drivers\\etc\\hosts, /etc/hosts), with a proxy or without.

WHY. Firefox reads that file once, in the first moments of the parent process, to keep the names it holds out of the DoH
(`network.trr.exclude-etc-hosts`, default true in Mozilla's build). A name found there is then answered by the host: with a
proxy that is a name leaving by the machine's resolver, and with or without a proxy it is a value taken from the host
(the file is the host's, not the persona's). The engine declares `network.trr.exclude-etc-hosts` false by default, so the read
(DNSServiceBase::DoReadEtcHostsFile, which returns before building the path when the pref is false) never happens.

HOW IT IS SEEN. A late attach of a debugger cannot prove it (the read is at start-up, before any attach), so the browser is
launched with MOZ_LOG=sync,nsHostResolver:5 and MOZ_LOG_FILE=<dir>/moz-%PID.log (one file per process): the line the engine
writes when it reads the file is "Reading hosts file at <path>" (DNSServiceBase.cpp:121). The test asserts that no log of any
process of the browser contains it, and, as a control that the logging worked, that the parent process logged the start of
its resolver ("nsHostResolver::Init"). The environment of the test process reaches the browser through the product's launch
(the diagnostic runs of the owner pass MOZ_LOG the same way).

Red on an engine whose default is true (the round-2 binary reads it twice per parent process); green on one with the
default false (commit fd21b34813 of the engine).
"""
from __future__ import annotations

import glob
import os
import time

import pytest

from test_doh_through_the_proxy_e2e import (  # noqa: F401  (fixtures are used by name)
    _hermetic_session, _launch, _page_in, _text, doh, gateway, pages, trap, watcher)

pytestmark = pytest.mark.e2e

_READ = b"Reading hosts file at"
_STARTED = b"nsHostResolver::Init"
#: Start-up tasks are in the first seconds; the page load and this pause let them all happen before the browser is closed.
SETTLE_S = 4.0


def _logs_of(directory):
    return sorted(glob.glob(os.path.join(str(directory), "moz-*.log*")))


def _count(path, needle):
    with open(path, "rb") as f:
        return sum(1 for line in f if needle in line)


def _report(directory):
    rows = []
    for path in _logs_of(directory):
        rows.append("%s: started=%d reads=%d" % (os.path.basename(path), _count(path, _STARTED), _count(path, _READ)))
    return "\n  ".join(rows) or "(no log file was written)"


@pytest.mark.parametrize("with_a_proxy", [True, False], ids=["launch_proxy", "no_proxy"])
def test_the_hosts_file_of_the_host_is_never_read(with_a_proxy, firefox_binary, doh, pages, gateway, watcher, tmp_path,
                                                  monkeypatch):
    logs = tmp_path / "moz"
    logs.mkdir()
    monkeypatch.setenv("MOZ_LOG", "sync,nsHostResolver:5")
    monkeypatch.setenv("MOZ_LOG_FILE", str(logs / "moz-%PID.log"))
    gw = gateway("gw") if with_a_proxy else None
    with _launch(firefox_binary, gw.proxy if gw else None) as browser:
        context, page = _page_in(browser)
        page.goto("about:blank")
        time.sleep(SETTLE_S)
        context.close()
    watcher.stop()

    report = _report(logs)
    started = [p for p in _logs_of(logs) if _count(p, _STARTED)]
    assert started, ("the control failed: no process logged the start of its resolver, so the logging did not work and an "
                     "empty result would prove nothing\n  " + report)
    reading = [p for p in _logs_of(logs) if _count(p, _READ)]
    assert reading == [], ("the browser read the host's hosts file ('Reading hosts file at ...'): the default of "
                           "network.trr.exclude-etc-hosts must be false\n  " + report)
