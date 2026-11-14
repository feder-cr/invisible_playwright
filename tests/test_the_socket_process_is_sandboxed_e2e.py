"""The browser of the suite ``wrapper.e2e.udp.sandbox`` runs with its sandbox ON, as it does for a user whose system allows user namespaces.

Why this file exists (run 43188313516, diagnosis of the root legs): on the Ubuntu 24.04 runner a normal user is refused user namespaces by
AppArmor (``kernel.apparmor_restrict_unprivileged_userns``), so the browser starts WITHOUT the sandbox of its socket and content processes and
every non-root suite passed on a browser that no user with a sandbox runs; as root the namespace is allowed, the sandbox is on, and the gather
of a PeerConnection came back empty. The suite of this file is the non-root leg that turns the restriction off (``sysctl``, in the gate), so
the browser has its sandbox like on any Linux that allows it, and this test is what makes the leg mean something: it FAILS when the sandbox
is not on, because a green run of WebRTC on an unsandboxed browser proves nothing about the sandboxed one.

It reads the kernel's word: ``Seccomp: 2`` (a filter is installed) in ``/proc/<pid>/status`` of the socket process and of a tab process.
Linux only (the suite is listed for Linux only).
"""
from __future__ import annotations

import os
import pathlib
import time

import pytest

from invisible_playwright import InvisiblePlaywright

pytestmark = pytest.mark.e2e


def _seccomp(pid: int) -> str:
    try:
        for line in pathlib.Path("/proc/%d/status" % pid).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Seccomp:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return "gone"
    return "?"


# The name the kernel keeps for a process (``Name:`` in /proc/<pid>/status, set by the browser itself) -> its kind.
_KIND_BY_NAME = {"Socket Process": "socket", "Web Content": "tab"}


def _children_of(binary: str):
    """(pid, kind) of every child process of ``binary``; the kind is ``socket`` or ``tab`` (other kinds are named by their last word).

    A process that the fork server makes (every tab) has NO ``tab`` at the end of its command line: it is forked, not exec'ed, so its argv is the
    one of the fork server, and the browser then overwrites it with a single string (``<binary> -contentproc``) that has no NUL in it. Only the
    socket process, which is exec'ed, keeps the full argument list. So the kind of a process comes from its last argument when that is one of the
    known words, and from the name the browser gave it (``Web Content``, ``Socket Process``) otherwise.
    """
    target = os.path.realpath(binary)
    found = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            exe = os.path.realpath(os.readlink(entry / "exe"))
            raw = (entry / "cmdline").read_bytes()
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        words = [w.decode("utf-8", "replace") for w in raw.split(b"\0") if w]
        if exe != target or "-contentproc" not in raw.decode("utf-8", "replace"):
            continue
        name = next((l.split(":", 1)[1].strip() for l in status.splitlines() if l.startswith("Name:")), "")
        kind = words[-1] if words and words[-1] in ("socket", "tab") else _KIND_BY_NAME.get(name, words[-1] if words else name)
        found.append((int(entry.name), kind))
    return found


#: How long a process that reads ``Seccomp: 0`` is read again, in seconds, before it is judged unsandboxed. A child turns its filter on only after its own
#: start-up, and Firefox pre-launches content processes, so a process sampled while it starts legitimately reads 0 (run 43523960399: the newest tab
#: process, pid 4090 of [3905, 4002, 4005, 4090], read 0 and the same suite was green on the candidates before). A process that is still 0 after this
#: long has finished starting and runs without a filter: that is the failure the test is for.
STARTUP_SECONDS = 10


def _settled_seccomp(pids, seconds: float = STARTUP_SECONDS) -> dict:
    """``{pid: Seccomp}`` of every pid, where a pid that does not read ``2`` is read again until it does or ``seconds`` have passed. No pid is dropped."""
    say = {pid: _seccomp(pid) for pid in pids}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(s != "2" for s in say.values()):
        time.sleep(0.25)
        say = {pid: (s if s == "2" else _seccomp(pid)) for pid, s in say.items()}
    return say


def _kernel_says() -> str:
    out = []
    for name in ("kernel.apparmor_restrict_unprivileged_userns", "kernel.unprivileged_userns_clone", "user.max_user_namespaces"):
        path = pathlib.Path("/proc/sys") / name.replace(".", "/")
        try:
            out.append("%s=%s" % (name, path.read_text(encoding="utf-8").strip()))
        except OSError:
            out.append("%s=(absent)" % name)
    return ", ".join(out) + ", uid=%d" % os.getuid()


def test_the_socket_process_and_a_tab_process_have_a_seccomp_filter(firefox_binary):
    with InvisiblePlaywright(headless=True, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        # A PeerConnection makes the socket process do what the UDP line is about.
        page.evaluate("""async () => {
          const pc = new RTCPeerConnection();
          pc.createDataChannel('x');
          await pc.setLocalDescription(await pc.createOffer());
          await new Promise(r => setTimeout(r, 1500));
        }""")
        sockets, tabs = [], []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            kids = _children_of(firefox_binary)
            sockets = [pid for pid, kind in kids if kind == "socket"]
            tabs = [pid for pid, kind in kids if kind == "tab"]
            if sockets and tabs:
                break
            time.sleep(0.5)
        say = _settled_seccomp(sockets + tabs)
        known = "socket %s, tab %s; %s" % (sockets, tabs, _kernel_says())
        assert sockets, "the browser has no socket process after a PeerConnection (%s)" % known
        assert tabs, "the browser has no tab process (%s)" % known
        unsandboxed = {pid: s for pid, s in say.items() if s != "2"}
        assert unsandboxed == {}, (
            "these processes run without a seccomp filter (Seccomp in /proc/<pid>/status is not 2): %s. The browser is not "
            "sandboxed on this machine, so this leg proves nothing about WebRTC with the sandbox on (%s)" % (unsandboxed, known))
