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


def _children_of(binary: str):
    """(pid, kind) of every ``-contentproc`` process that runs ``binary``; the kind is the last word of its command line."""
    target = os.path.realpath(binary)
    found = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            exe = os.path.realpath(os.readlink(entry / "exe"))
            words = [w.decode("utf-8", "replace") for w in (entry / "cmdline").read_bytes().split(b"\0") if w]
        except OSError:
            continue
        if exe == target and "-contentproc" in words:
            found.append((int(entry.name), words[-1]))
    return found


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
        say = {pid: _seccomp(pid) for pid in sockets + tabs}
        known = "socket %s, tab %s; %s" % (sockets, tabs, _kernel_says())
        assert sockets, "the browser has no socket process after a PeerConnection (%s)" % known
        assert tabs, "the browser has no tab process (%s)" % known
        unsandboxed = {pid: s for pid, s in say.items() if s != "2"}
        assert unsandboxed == {}, (
            "these processes run without a seccomp filter (Seccomp in /proc/<pid>/status is not 2): %s. The browser is not "
            "sandboxed on this machine, so this leg proves nothing about WebRTC with the sandbox on (%s)" % (unsandboxed, known))
