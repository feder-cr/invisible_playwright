"""The e2e tests that look at processes ask for the processes of a site by its name. No browser.

With `fission.processSiteNames` on, a content process that hosts a site is named by that site, and on Linux the name
is cut at 15 characters. The browser starts processes of its own (the pool refill, a process for a Mozilla site
started by the New Tab page), so a test that compared two counts of processes read them as the effect of the frame
under test (test_same_site_other_port_stays_in_process, red in 14 of 30 runs of the e2e suite). The names below are
the ones measured on firefox-62 (Linux), with command lines as /proc gives them.
"""
import _processes
import pytest

pytestmark = pytest.mark.unit

ENGINE = "/cache/firefox-62/firefox"
DIR = "/cache/firefox-62"
A, B = "http://a.test", "http://b.test"


class _Proc:
    def __init__(self, pid, name, kind="tab", exe=ENGINE):
        self.pid, self._name, self._exe = pid, name, exe
        self._cmd = [exe, "-contentproc", "-isForBrowser", "-prefsLen", "1", "-parentBuildID", "20261111073531",
                     "-appDir", DIR + "/browser", str(pid), kind]

    def exe(self):
        return self._exe

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmd


def _engine_at_a():
    """a.test shown: its process, the one the New Tab page started, and the engine's other children."""
    return [_Proc(184, "Socket Process", "socket"), _Proc(200, "WebExtensions"), _Proc(334, "http://a.test^u"),
            _Proc(377, "Privileged Cont"), _Proc(410, "mozgcp.net"), _Proc(433, "Web Content"),
            _Proc(438, "Web Content")]


def test_a_process_named_by_the_site_is_the_sites():
    assert _processes.site_pids(_engine_at_a(), A, DIR) == {334}


@pytest.mark.parametrize("name", ["mozgcp.net", "Web Content", "Privileged Cont", "WebExtensions", "Isolated Web Co"])
def test_a_process_with_another_name_is_not_the_sites(name):
    assert _processes.site_pids([_Proc(1, name)], A) == set()


def test_the_process_of_another_site_is_not_the_sites():
    assert _processes.site_pids([_Proc(1, "http://b.test^u")], A) == set()
    assert _processes.site_pids([_Proc(1, "http://b.test^u")], B) == {1}


def test_the_process_the_browser_starts_for_itself_does_not_move_the_sites():
    """The measured flaw: a second isolated process (mozgcp.net) arriving between two reads changed the old count."""
    before = _processes.site_pids(_engine_at_a(), A, DIR)
    after = _processes.site_pids(_engine_at_a() + [_Proc(549, "mozgcp.net"), _Proc(550, "Web Content")], A, DIR)
    assert before == after == {334}


def test_https_is_dropped_from_the_site():
    assert _processes.name_of_site("https://example.com") == "example.com"
    assert _processes.hosts_site("example.com", "https://example.com")
    assert _processes.hosts_site("example.com^use", "https://example.com")


def test_a_site_longer_than_the_name_matches_on_its_first_15_characters():
    site = "http://a-very-long-site.example"
    assert _processes.name_of_site(site) == "http://a-very-l"
    assert _processes.hosts_site("http://a-very-l", site)
    assert not _processes.hosts_site("http://a-very-x", site)


def test_a_site_that_starts_like_another_is_not_the_other():
    assert not _processes.hosts_site("http://a.testin", A)
    assert not _processes.hosts_site("http://a.testing.example", A)
    assert _processes.hosts_site("http://a.test^u", A)


def test_a_process_of_another_firefox_is_not_counted():
    other = _Proc(900, "http://a.test^u", exe="/usr/lib/firefox/firefox")
    assert _processes.site_pids(_engine_at_a() + [other], A, DIR) == {334}


def test_a_process_that_is_not_a_tab_is_not_counted():
    assert _processes.site_pids([_Proc(1, "http://a.test^u", kind="gpu")], A) == set()
