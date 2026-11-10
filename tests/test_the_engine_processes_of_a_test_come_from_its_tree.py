"""The selector of the engine processes that belong to a test: two trees, one machine.

The WebGL e2e looked for "the engine process with a CanvasRenderer thread" over the whole of /proc
and, in a run with several workers, was handed another worker's browser, which had not drawn
yet: a different answer on two runs of the same code. The selector now walks the tree under the
test's own process. This file runs anywhere (the machine is a stand-in), and a scan of the whole
machine fails it.
"""
from __future__ import annotations

import pytest

from engine_process_tree import maps_of_the_engine_below

pytestmark = pytest.mark.unit

ENGINE = "/work/firefox"
OWN = ENGINE + "/llvmpipe/"


class FakeMachine:
    """Two workers on one machine. Worker 1 (pid 100) has a browser (110) with a child that draws
    (111); worker 2 (pid 200) has a browser (210) with a child that has not drawn yet (211) and
    maps the host's libgallium. The listing of ALL pids puts worker 2's processes first."""

    def __init__(self):
        self.tree = {100: [110, 111], 200: [210, 211]}
        self.exes = {110: ENGINE + "/firefox", 111: ENGINE + "/firefox",
                     210: ENGINE + "/firefox", 211: ENGINE + "/firefox", 100: "/usr/bin/python3"}
        self.files = {
            110: ["/usr/lib/x86_64-linux-gnu/libgallium-25.2.8.so"],
            111: [OWN + "libEGL.so.1", OWN + "libgallium-26.2.6.so"],
            210: ["/usr/lib/x86_64-linux-gnu/libgallium-25.2.8.so"],
            211: ["/usr/lib/x86_64-linux-gnu/libgallium-25.2.8.so"],
        }
        self.all_pids = [211, 210, 111, 110]  # what a scan of the whole machine meets first

    def descendants(self, pid):
        return list(self.tree.get(pid, []))

    def exe(self, pid):
        return self.exes.get(pid)

    def maps(self, pid):
        return self.files[pid]


def test_a_test_sees_only_the_processes_below_it():
    machine = FakeMachine()
    one = maps_of_the_engine_below(100, ENGINE, machine)
    two = maps_of_the_engine_below(200, ENGINE, machine)
    assert set(one) == {110, 111}
    assert set(two) == {210, 211}


def test_the_other_workers_browser_is_never_the_one_chosen():
    """Known-bad: the whole-machine scan, which takes the first engine process it meets, finds
    pid 211 (no libEGL, the host's libgallium) for worker 1. The tree finds worker 1's own
    drawing process."""
    machine = FakeMachine()

    def first_on_the_machine():
        for pid in machine.all_pids:
            if machine.exe(pid) and machine.exe(pid).startswith(ENGINE):
                return {pid: machine.maps(pid)}

    assert OWN + "libEGL.so.1" not in next(iter(first_on_the_machine().values()))
    mine = maps_of_the_engine_below(100, ENGINE, machine)
    assert any(OWN + "libEGL.so.1" in maps for maps in mine.values())
    assert not set(mine) & {210, 211}


def test_a_process_that_is_not_the_engine_is_left_out():
    machine = FakeMachine()
    machine.tree[100] = [110, 111, 100100]
    machine.exes[100100] = "/usr/bin/Xvfb"
    machine.files[100100] = ["/usr/lib/x86_64-linux-gnu/libEGL.so.1"]
    assert 100100 not in maps_of_the_engine_below(100, ENGINE, machine)


def test_a_root_with_no_engine_below_is_empty():
    assert maps_of_the_engine_below(999, ENGINE, FakeMachine()) == {}
