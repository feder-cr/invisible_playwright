"""Fontconfig reads nothing of the host (e2e, Linux): no process of the browser,
parent or content, opens, stats or probes the host's fontconfig
configuration or its font directories.

The engine runs fontconfig on an empty configuration installed before the
first ``FcInit()`` (``StealthFcEmptyConfig``, nsLookAndFeel and the platform
font list); the fonts are the bundle's. The engine's own gtest only looks at
the final state, so it would also pass if the host's configuration had been
loaded first, by GTK, Pango or anything else, and replaced afterwards. What
sees the order is the system calls themselves: the browser runs under
``strace -f`` and a page with text is drawn, and a single look at
``/etc/fonts``, ``~/.config/fontconfig``, ``$FONTCONFIG_FILE`` or
``/usr/share/fonts`` by any process fails the test.

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
import sys
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not sys.platform.startswith("linux"),
                       reason="fontconfig is the Linux font system"),
    pytest.mark.skipif(shutil.which("strace") is None,
                       reason="needs strace (apt install strace)"),
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


def _stand_in(binary: str, tree: Path, log: Path) -> str:
    """A copy of the engine's directory made of links, whose executable runs
    the real one under strace."""
    real = Path(os.path.realpath(binary))
    tree.mkdir()
    for entry in real.parent.iterdir():
        if entry.name != real.name:
            (tree / entry.name).symlink_to(entry)
    script = tree / real.name
    script.write_text(
        "#!/bin/sh\n"
        'exec %s -f -qq -s 4096 -o %s -e trace=%s %s "$@"\n'
        % (shutil.which("strace"), log, _SYSCALLS, real))
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


def _kill_leftovers(tree: Path) -> None:
    """The stand-in's strace and whatever it traced, if the session left any."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmdline = Path("/proc/%s/cmdline" % pid).read_bytes()
        except OSError:
            continue
        if str(tree).encode() in cmdline:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass


def test_no_process_reads_the_host_fontconfig(firefox_binary, tmp_path):
    from invisible_playwright import InvisiblePlaywright

    log = tmp_path / "strace.log"
    tree = tmp_path / "engine"
    stand_in = _stand_in(firefox_binary, tree, log)
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
        _kill_leftovers(tree)

    text = log.read_text(errors="replace")
    assert "-contentproc" in text, (
        "strace saw no content process, so the test could not judge one")
    found = _host_lookups(log, real)
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
        "the browser looked at the host's fontconfig (%s):\n%s"
        % (", ".join(_host_font_paths()), "\n".join(report)))
