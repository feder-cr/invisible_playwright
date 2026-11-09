"""The criterion of the fontconfig e2e: what fails and what passes, on fake strace lines.

The e2e (tests/test_fontconfig_reads_nothing_of_the_host_linux_e2e.py) fails if a process opens
the host's fontconfig configuration or fonts, or if a content process looks at them at all. The
parent's existence probes are not reads: the parent builds the content sandbox's permissions and
asks whether each path exists (candidate 42640321957: one lstat of ~/.fonts.conf).

Known-bad inputs, which must fail: a parent that opens /etc/fonts/fonts.conf (a fontconfig that
reads the host), a parent that opens ~/.fonts.conf even when it is not there, a content process
that stats a host font directory. The one that must pass: the parent's lstat.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

E2E = Path(__file__).resolve().parents[1] / "test_fontconfig_reads_nothing_of_the_host_linux_e2e.py"
BINARY = "/engine/firefox"
HOME = "/home/runner"


def _module():
    spec = importlib.util.spec_from_file_location("fontconfig_e2e", E2E)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fontconfig_e2e"] = module
    spec.loader.exec_module(module)
    # The paths of a Linux runner, whatever machine the unit test runs on.
    module._host_font_paths = lambda: ["/etc/fonts", HOME + "/.config/fontconfig",
                                       HOME + "/.fonts.conf", "/usr/share/fonts"]
    return module


def _log(tmp_path, *calls):
    """A strace log of a parent (pid 100) that starts a content process (pid 200) and the calls."""
    lines = ['100 execve("%s", ["%s"], 0x0) = 0' % (BINARY, BINARY),
             "100 clone3({flags=CLONE_VM}, 88) = 200",
             '200 execve("%s", ["%s", "-contentproc", "x"], 0x0) = 0' % (BINARY, BINARY)]
    lines += list(calls)
    path = tmp_path / "strace.log"
    path.write_text("\n".join(lines) + "\n")
    return path


def _verdict(tmp_path, *calls):
    module = _module()
    return module.judge(module._host_lookups(_log(tmp_path, *calls), BINARY))


def test_the_parents_existence_probe_of_a_host_path_passes(tmp_path):
    assert _verdict(tmp_path,
                    '100 lstat("%s/.fonts.conf", 0x7ffc) = -1 ENOENT (No such file or directory)' % HOME,
                    '100 newfstatat(AT_FDCWD, "/etc/fonts", 0x7ffc, 0) = -1 ENOENT (x)',
                    '100 access("/usr/share/fonts", R_OK) = -1 ENOENT (x)',
                    '100 readlink("/etc/fonts/fonts.conf", 0x7ffc, 1024) = -1 ENOENT (x)') == {}


def test_a_parent_that_opens_the_host_configuration_fails(tmp_path):
    verdict = _verdict(tmp_path, '100 openat(AT_FDCWD, "/etc/fonts/fonts.conf", O_RDONLY) = 5')
    assert list(verdict) == ["parent"] and len(verdict["parent"]) == 1


def test_a_parent_that_opens_the_host_configuration_fails_even_when_it_is_not_there(tmp_path):
    verdict = _verdict(tmp_path,
                       '100 openat(AT_FDCWD, "%s/.fonts.conf", O_RDONLY) = -1 ENOENT (x)' % HOME)
    assert list(verdict) == ["parent"]


def test_a_content_process_that_looks_at_the_host_at_all_fails(tmp_path):
    for call in ('200 stat("/usr/share/fonts", 0x7ffc) = -1 ENOENT (x)',
                 '200 access("/etc/fonts", R_OK) = 0',
                 '200 readlink("/etc/fonts/fonts.conf", 0x7ffc, 1024) = -1 EINVAL (x)',
                 '200 openat(AT_FDCWD, "/usr/share/fonts/a.ttf", O_RDONLY) = 6'):
        verdict = _verdict(tmp_path, call)
        assert list(verdict) == ["content"], call


def test_a_call_on_another_path_is_not_a_host_font_lookup(tmp_path):
    assert _verdict(tmp_path,
                    '100 openat(AT_FDCWD, "/engine/fonts/arial.ttf", O_RDONLY) = 7',
                    '200 stat("/engine/fonts/arial.ttf", 0x7ffc) = 0') == {}
