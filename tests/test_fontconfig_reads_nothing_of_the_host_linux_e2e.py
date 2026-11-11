"""Fontconfig reads nothing of the host (e2e, Linux): no process of the browser opens
the host's fontconfig configuration or its font files, and no content process looks at
them at all (open, stat, access, readlink). The parent's own existence probes (lstat,
stat, access, readlink) are not reads: see ``judge``.

The engine runs fontconfig on an empty configuration installed before the
first ``FcInit()`` (``StealthFcEmptyConfig``, nsLookAndFeel and the platform
font list); the fonts are the bundle's. The engine's own gtest only looks at
the final state, so it would also pass if the host's configuration had been
loaded first, by GTK, Pango or anything else, and replaced afterwards. What
sees the order is the system calls themselves: the browser runs under
``strace -f`` and a page with text is drawn, and one ``open`` of
``/etc/fonts``, ``~/.config/fontconfig``, ``$FONTCONFIG_FILE`` or
``/usr/share/fonts`` by any process, or one ``stat`` there by a content process, fails
the test.

Why the parent's existence probes pass: the parent builds the permissions of the content
sandbox (SandboxBrokerPolicyFactory::InitContentPolicy) and, for each path a content
process may be given, asks whether it exists (``lstat``, ``stat``). It opens nothing and
no byte of those files enters the process; a page cannot see the call. Fontconfig that
read the host would ``open`` its configuration, and that is what fails.

strace runs through a stand-in tree: a temporary directory with a link to
every file of the engine's directory, where the executable is a script that
execs the real one under strace. The core reads the engine's identity from
the directory of the path it is given, so the seal check passes as it does
for the real tree.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not sys.platform.startswith("linux"),
                       reason="fontconfig is the Linux font system"),
    pytest.mark.skipif(shutil.which("strace") is None or shutil.which("setsid") is None,
                       reason="needs strace and setsid (apt install strace util-linux)"),
]

_SYSCALLS = ("execve,clone,clone3,fork,vfork,open,openat,stat,lstat,"
             "newfstatat,statx,access,faccessat,faccessat2,readlink")

_PAGE = ("<!doctype html><meta charset=utf-8><body style='font-family:serif'>"
         "<h1>Fontconfig</h1><p style='font-family:sans-serif'>The quick brown "
         "fox jumps over the lazy dog.</p><code>monospace 0123456789</code>"
         "<p style='font-family:Arial'>Arial</p></body>")


def _host_font_paths() -> list:
    """Every place the host keeps fontconfig configuration or fonts."""
    home = os.path.expanduser("~")
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    paths = ["/etc/fonts", os.path.join(config_home, "fontconfig"),
             os.path.join(home, ".config", "fontconfig"),
             os.path.join(home, ".fonts.conf"), "/usr/share/fonts"]
    if os.environ.get("FONTCONFIG_FILE"):
        paths.append(os.environ["FONTCONFIG_FILE"])
    if os.environ.get("FONTCONFIG_PATH"):
        paths += os.environ["FONTCONFIG_PATH"].split(":")
    return sorted(set(p.rstrip("/") for p in paths if p))


def _strace_options() -> str:
    """strace's own flags: quiet, follow forks, and die with the traced process
    when this build knows --kill-on-exit."""
    help_text = subprocess.run([shutil.which("strace"), "-h"], capture_output=True,
                               text=True).stdout
    return "-f -qq -s 4096" + (" --kill-on-exit" if "--kill-on-exit" in help_text else "")


def _require_strace_can_trace(tmp: Path) -> None:
    """strace needs ptrace: a container or yama can refuse it, and then the
    failure is the environment's, said as such, not the launch's."""
    probe = subprocess.run([shutil.which("strace"), "-o", str(tmp / "probe.log"), "true"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.fail("strace cannot trace here: %s" % (probe.stderr.strip() or probe.returncode),
                    pytrace=False)


def _stand_in(binary: str, tree: Path, log: Path, pidfile: Path) -> str:
    """A copy of the engine's directory made of links, whose executable runs
    the real one under strace, in a session of its own.

    The script writes its pid and execs ``setsid``: it is not a group leader,
    so setsid does not fork, the pid stays, and it is the id of a process
    group that holds strace and everything it traces (``_kill_group``)."""
    real = Path(os.path.realpath(binary))
    tree.mkdir()
    for entry in real.parent.iterdir():
        if entry.name != real.name:
            (tree / entry.name).symlink_to(entry)
    script = tree / real.name
    script.write_text(
        "#!/bin/sh\n"
        "echo $$ > %s\n"
        'exec %s %s %s -o %s -e trace=%s %s "$@"\n'
        % (pidfile, shutil.which("setsid"), shutil.which("strace"), _strace_options(),
           log, _SYSCALLS, real))
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


_LINE = re.compile(r"^(\d+)\s+(\w+)\((.*)$")
# strace -f splits a call that another thread interrupts in two lines:
# "PID clone3({...} <unfinished ...>" and "PID <... clone3 resumed>) = CHILD".
_RESUMED = re.compile(r"^(\d+)\s+<\.\.\. (\w+) resumed>(.*)$")
_RESULT = re.compile(r"=\s+(\d+)\s*$")


def _host_lookups(log: Path, real_binary: str) -> dict:
    """``{"parent"|"content"|"other": ["<syscall> <line>", ...]}`` for every
    system call of the browser that names a host font path."""
    lines = log.read_text(errors="replace").splitlines()
    parent_of = {}
    argv_of = {}
    for line in lines:
        m = _LINE.match(line) or _RESUMED.match(line)
        if not m:
            continue
        pid, call, rest = int(m.group(1)), m.group(2), m.group(3)
        if call in ("clone", "clone3", "fork", "vfork"):
            r = _RESULT.search(rest)
            if r:
                parent_of[int(r.group(1))] = pid
        elif call == "execve" and real_binary in rest:
            # The arguments are on the first line even when the result comes
            # on a resumed one; the last execve of a process is what it runs.
            argv_of[pid] = rest

    def role(pid: int) -> str:
        seen = set()
        while pid not in seen:
            seen.add(pid)
            argv = argv_of.get(pid)
            if argv is not None and real_binary in argv:
                return "content" if "-contentproc" in argv else "parent"
            if pid not in parent_of:
                break
            pid = parent_of[pid]
        return "other"

    paths = _host_font_paths()
    found = defaultdict(list)
    for line in lines:
        m = _LINE.match(line)
        if not m or m.group(2) in ("execve", "clone", "clone3", "fork", "vfork"):
            continue
        quoted = re.findall(r'"([^"]*)"', m.group(3))
        if any(q == p or q.startswith(p + "/") for q in quoted for p in paths):
            found[role(int(m.group(1)))].append(line)
    return dict(found)


# What a call does to a path. An existence probe is the question "is it there": no byte is read.
_OPENS = ("open", "openat", "openat2", "creat")


def _call_of(line: str) -> str:
    m = _LINE.match(line)
    return m.group(2) if m else ""


def judge(found: dict) -> dict:
    """The part of ``_host_lookups``' result that fails the test.

    - any process that OPENS a host fontconfig or font path (an open that fails with ENOENT
      too: it is fontconfig trying to read its configuration);
    - a content process that does anything at all there (stat, access, readlink included):
      content has no reason to look, and it is the process a page runs in.

    The parent's existence probes (stat, lstat, newfstatat, statx, access, faccessat, readlink)
    are ignored, for the reason in the module's docstring."""
    bad = {}
    for who, hits in found.items():
        kept = list(hits) if who == "content" else [h for h in hits if _call_of(h) in _OPENS]
        if kept:
            bad[who] = kept
    return bad


def _kill_group(pidfile: Path) -> None:
    """Kill the stand-in's process group: strace, the browser and its children.

    Looking for the stand-in's path in the command lines found nothing (strace
    and its children carry the real binary's path), so the group is the handle."""
    try:
        pgid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def _group_members(pidfile: Path) -> list:
    """Pids still in the stand-in's process group."""
    try:
        pgid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return []
    members = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            stat_line = Path("/proc/%s/stat" % pid).read_text()
        except OSError:
            continue
        fields = stat_line.rsplit(")", 1)[1].split()
        # fields: state ppid pgrp ...; a zombie is already dead.
        if fields[0] != "Z" and int(fields[2]) == pgid:
            members.append(int(pid))
    return members


def _wait_for_exit(pidfile: Path, seconds: float = 10.0) -> list:
    end = time.monotonic() + seconds
    left = _group_members(pidfile)
    while left and time.monotonic() < end:
        time.sleep(0.1)
        left = _group_members(pidfile)
    return left


def test_a_killed_stand_in_leaves_neither_strace_nor_the_browser(firefox_binary, tmp_path):
    """The cleanup after a forced timeout kills everything: it launches the
    stand-in as the test does, kills it as the test's `finally` does, and
    looks for survivors."""
    _require_strace_can_trace(tmp_path)
    log = tmp_path / "strace.log"
    pidfile = tmp_path / "stand-in.pid"
    stand_in = _stand_in(firefox_binary, tmp_path / "engine", log, pidfile)
    profile = tmp_path / "profile"
    profile.mkdir()
    proc = subprocess.Popen([stand_in, "-headless", "-no-remote", "-profile", str(profile),
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end and "-contentproc" not in (
                log.read_text(errors="replace") if log.exists() else ""):
            time.sleep(0.2)
        assert len(_group_members(pidfile)) >= 3, "strace and the browser did not come up"
        _kill_group(pidfile)
        proc.wait(timeout=10)
        left = _wait_for_exit(pidfile)
    finally:
        _kill_group(pidfile)
    assert not left, "processes left after the kill: %s" % [
        Path("/proc/%d/cmdline" % p).read_bytes()[:120] for p in left
        if Path("/proc/%d" % p).exists()]


def test_no_process_reads_the_host_fontconfig(firefox_binary, tmp_path, engine_at_least):
    engine_at_least("firefox-63")
    from invisible_playwright import InvisiblePlaywright

    _require_strace_can_trace(tmp_path)
    log = tmp_path / "strace.log"
    tree = tmp_path / "engine"
    pidfile = tmp_path / "stand-in.pid"
    stand_in = _stand_in(firefox_binary, tree, log, pidfile)
    real = os.path.realpath(firefox_binary)
    try:
        with InvisiblePlaywright(seed=4246, binary_path=stand_in,
                                 headless=True) as browser:
            page = browser.new_page()
            page.set_content(_PAGE)
            # Text was laid out with real fonts: the page measured something.
            assert page.evaluate(
                "() => document.querySelector('p').getBoundingClientRect().width") > 0
            page.screenshot()
    finally:
        _kill_group(pidfile)

    text = log.read_text(errors="replace")
    assert "-contentproc" in text, (
        "strace saw no content process, so the test could not judge one")
    found = judge(_host_lookups(log, real))
    report = []
    for who, hits in sorted(found.items()):
        pids = sorted({int(h.split()[0]) for h in hits})
        names = sorted({q for h in hits for q in re.findall(r'"([^"]*)"', h)
                        if any(q == p or q.startswith(p + "/")
                               for p in _host_font_paths())})
        roots = {p: sum(1 for n in names if n == p or n.startswith(p + "/"))
                 for p in _host_font_paths()}
        report.append("%s: %d calls by pids %s, %d paths (%s), first ones:"
                      % (who, len(hits), pids, len(names),
                         ", ".join("%s %d" % kv for kv in roots.items() if kv[1])))
        report += ["  " + n for n in names[:12]]
        report += ["  " + h[:240] for h in hits[:4]]
    assert not found, (
        "the browser opened the host's fontconfig, or a content process looked at it (%s):\n%s"
        % (", ".join(_host_font_paths()), "\n".join(report)))
