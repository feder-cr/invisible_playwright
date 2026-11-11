"""The e2e tests that count processes count the ones that host a site, not every new content process. No browser.

The engine keeps preallocated content processes and starts one to refill the pool after it hands one to a site,
whenever it gets to it. The bench counted every `-contentproc ... tab` pid, so a refill that landed between its
two reads was "a process the same-site frame started" (test_same_site_other_port_stays_in_process, red on the
e2e of the commit of 0.50.0: `[13443]`). The processes below are the ones measured on firefox-62 (Linux):
names and command lines as /proc gives them.
"""
import _processes
import pytest

pytestmark = pytest.mark.unit

ENGINE = "/cache/firefox-62/firefox"
DIR = "/cache/firefox-62"


class _Proc:
    def __init__(self, pid, name, kind, exe=ENGINE):
        self.pid, self._name, self._exe = pid, name, exe
        self._cmd = [exe, "-contentproc", "-isForBrowser", "-prefsLen", "1", "-parentBuildID", "20261111073531",
                     "-appDir", DIR + "/browser", str(pid), kind]

    def exe(self):
        return self._exe

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmd


def _engine_at_plain():
    """a.test/plain shown: one process for the site, three preallocated, and the engine's other children."""
    return [_Proc(184, "Socket Process", "socket"), _Proc(200, "WebExtensions", "tab"),
            _Proc(334, "Isolated Web Co", "tab"), _Proc(377, "Privileged Cont", "tab"),
            _Proc(433, "Web Content", "tab"), _Proc(438, "Web Content", "tab"), _Proc(460, "Web Content", "tab")]


def test_a_preallocated_process_that_arrives_is_not_a_site():
    before = _processes.site_pids(_engine_at_plain(), DIR)
    after = _processes.site_pids(_engine_at_plain() + [_Proc(549, "Web Content", "tab")], DIR)
    assert before == after == {334}


def test_a_preallocated_process_given_to_a_site_is_a_new_site_process():
    """A cross-site frame: 433 leaves the pool for b.test (and is renamed), 549 refills the pool."""
    procs = [p for p in _engine_at_plain() if p.pid != 433]
    procs += [_Proc(433, "Isolated Web Co", "tab"), _Proc(549, "Web Content", "tab")]
    assert _processes.site_pids(procs, DIR) - _processes.site_pids(_engine_at_plain(), DIR) == {433}


def test_a_process_of_another_firefox_is_not_counted():
    other = _Proc(900, "Isolated Web Co", "tab", exe="/usr/lib/firefox/firefox")
    assert _processes.site_pids(_engine_at_plain() + [other], DIR) == {334}


def test_the_pool_refilled_behind_a_closed_context_is_not_a_process_left():
    """After the fourth closed context of test_a_context_that_ends_leaves_no_process_e2e, as measured: three
    preallocated processes and no site. Counting every content process said 5 ([2, 2, 2, 5, 5])."""
    left = [_Proc(1219, "WebExtensions", "tab"), _Proc(1382, "Privileged Cont", "tab"),
            _Proc(1739, "Web Content", "tab"), _Proc(1744, "Web Content", "tab"), _Proc(1821, "Web Content", "tab")]
    assert _processes.site_pids(left) == set()
    assert _processes.site_pids(left + [_Proc(1900, "Isolated Web Co", "tab")]) == {1900}
