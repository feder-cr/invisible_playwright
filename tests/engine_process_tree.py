"""The engine processes that belong to THIS test, found from the tree and not from the machine.

An e2e run opens several browsers at once (pytest-xdist workers, each with its own). A test that
wants "the engine process that does X" and looks for it over the whole of /proc can be handed the
process of another worker's browser, which has not done X yet: that is how
test_webgl_draws_on_the_engine_s_own_gl failed on one run of the Linux gate and passed on the
next with the same engine and the same core.

The tree is the answer: a worker's browsers are its descendants, and a process that is not below
the worker is not the test's business. `maps_of_the_engine_below` takes the root, the engine's
folder and a `view` of the machine (the real one, or a stand-in in the unit test).
"""
from __future__ import annotations

from typing import Dict, List, Optional


class Machine:
    """What the selector asks of the machine: the real one, through psutil and /proc."""

    def descendants(self, pid: int) -> List[int]:
        import psutil

        try:
            return [p.pid for p in psutil.Process(pid).children(recursive=True)]
        except psutil.Error:
            return []

    def exe(self, pid: int) -> Optional[str]:
        import os

        try:
            return os.readlink("/proc/%d/exe" % pid)
        except OSError:
            return None

    def maps(self, pid: int) -> List[str]:
        try:
            with open("/proc/%d/maps" % pid) as f:
                return sorted({line.split()[-1] for line in f if len(line.split()) > 5})
        except OSError:
            return []


def maps_of_the_engine_below(root: int, binary_dir: str, machine: Optional[Machine] = None) -> Dict[int, List[str]]:
    """{pid: mapped files} of every engine process (executable inside `binary_dir`) that is a
    descendant of `root`. Nothing outside the tree is looked at."""
    machine = machine or Machine()
    found: Dict[int, List[str]] = {}
    for pid in machine.descendants(root):
        exe = machine.exe(pid)
        if exe and exe.startswith(binary_dir):
            found[pid] = machine.maps(pid)
    return found
