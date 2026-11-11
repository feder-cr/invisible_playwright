"""Which of the engine's processes host a site: what the e2e tests that count processes read.

A content process is not a site's by being new. The engine keeps preallocated content processes
(`dom.ipc.processPrelaunch.fission.number`, 3), hands one to a site when a site needs a process, and starts
another to refill the pool whenever it gets to it, so a pid that appears between two reads is often that refill.
On Linux the process says which it is: `ContentChild::SetProcessName` names it by its remote type with
`prctl(PR_SET_NAME)` (ipc/glue/ProcessUtils_linux.cpp), "Isolated Web Co" once it hosts a site of strategy 1
(`webIsolated=<site>`), "Web Content" while it is preallocated. Windows names no process
(ProcessUtils_none.cpp): a test that counts sites skips there and says so.

Measured on firefox-62 (Linux): a cross-site frame renames one preallocated process and a new "Web Content"
refills the pool; a same-site frame changes neither; a closed context leaves no "Isolated Web Co", while the
pool of "Web Content" fills up behind it in about half the runs.
"""
from __future__ import annotations

import os
import sys

import pytest

#: The name a content process of strategy 1 takes once it hosts a site (PR_SET_NAME keeps 15 characters of
#: "Isolated Web Content"). A preallocated one is "Web Content" until a site is given to it.
SITE_PROCESS_NAME = "Isolated Web Co"


def site_pids(processes, engine_dir=None):
    """The pids, among `processes` (psutil-like), of the content processes that host a site; with `engine_dir`,
    only those of the engine in that directory."""
    import psutil

    pids = set()
    for proc in processes:
        try:
            if engine_dir is not None and not os.path.normcase(proc.exe()).startswith(os.path.normcase(engine_dir)):
                continue
            name, cmd = proc.name(), proc.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd and cmd and cmd[-1] == "tab" and name == SITE_PROCESS_NAME:
            pids.add(proc.pid)
    return pids


def site_processes_below(root_pid, engine_dir=None):
    """The pids of the processes below `root_pid` that host a site. Off Linux the test skips: no name says it."""
    import psutil

    if not sys.platform.startswith("linux"):
        pytest.skip("only Linux names a content process by the site it hosts; %s names none" % sys.platform)
    try:
        children = psutil.Process(root_pid).children(recursive=True)
    except psutil.Error:
        return set()
    return site_pids(children, engine_dir)
