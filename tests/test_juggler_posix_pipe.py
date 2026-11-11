"""The POSIX leg of the pipe, executed instead of read.

⛔ THIS LEG SHIPPED BROKEN AND NOBODY COULD HAVE KNOWN. It was written from
`nsRemoteDebuggingPipe.cpp` - descriptors hardwired to 3 for reading and 4 for
writing - and never run: the suite lives on Windows, where the pipe travels as
inheritable HANDLEs from the environment and none of this code executes. The
project's own notes said so in as many words: "the Linux leg. Descriptors 3 and
4 are written following the C++, but nobody has executed them."

**What was wrong, and why the symptom pointed away from it.** On the first real
run the browser started, printed `Juggler listening to the pipe`, stayed alive -
and the first write raised `BrokenPipeError`. A live process with no reader.

CPython's close-all-descriptors sweep runs AFTER `preexec_fn`, so the two
descriptors the spawn creates with `dup2` were closed again before `exec` unless
named in the keep list. Measured:

    preexec_fn + pass_fds(its_read, its_write)  ->  alive=[3]
    3 and 4 also kept                           ->  alive=[3, 4]

Fd 3 survived by accident - the parent's first `os.pipe()` happened to return 3,
so it was already in the keep list and the `dup2` onto it was a no-op. That
accident is what let the browser ANNOUNCE itself while never receiving anything,
and it is why the failure looked like the browser rather than like us.

⛔ AND A LAUNCH THAT MERELY STARTS PROVES NOTHING HERE. The broken version
started, printed its readiness line and stayed alive. So this test navigates and
reads a value back OUT of the document: the pipe has to carry messages in both
directions before anything is claimed about it.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import time

import pytest

from _servers import LocalServer

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the POSIX leg does not execute on Windows, where the pipe travels "
           "as inheritable HANDLEs read from the environment")

PAGE = b"""<!doctype html><html><head><title>posix</title></head>
<body>ok</body></html>"""


@pytest.fixture
def served():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    with LocalServer(("127.0.0.1", 0), H) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d/" % srv.server_address[1]
        finally:
            srv.shutdown()


@pytest.mark.e2e
def test_the_posix_pipe_carries_messages_both_ways(firefox_binary, served, first_run_prefs):
    from invisible_core.juggler import connection as conn
    from invisible_core.juggler.injected import InjectedScript
    from invisible_core.juggler.lifecycle import Lifecycle

    profile = tempfile.mkdtemp(prefix="posix_pipe_")
    with open(os.path.join(profile, "user.js"), "w", encoding="utf-8") as f:
        f.writelines("user_pref(%s, %s);\n" % (json.dumps(k), json.dumps(v)) for k, v in first_run_prefs.items())
    c = conn.launch(str(firefox_binary), profile, headless=True)
    sessions: dict = {}
    c.add_listener(lambda m, p, s: (
        sessions.__setitem__(p["targetInfo"]["targetId"], p["sessionId"])
        if m == "Browser.attachedToTarget" else None))
    try:
        # ⛔ The first WRITE is the assertion, not the launch. The broken
        # version reached this line with a healthy-looking process and died
        # here with EPIPE.
        c.send("Browser.enable", {"attachToDefaultContext": True}, timeout=40)
        ctx = c.send("Browser.createBrowserContext", {"removeOnDetach": True})
        page = c.send("Browser.newPage",
                      {"browserContextId": ctx["browserContextId"]})
        end = time.time() + 30
        while page["targetId"] not in sessions and time.time() < end:
            time.sleep(0.02)
        assert page["targetId"] in sessions, (
            "the browser never attached to the page it just created: messages "
            "go out and nothing comes back")
        session = sessions[page["targetId"]]
        life = Lifecycle(c, session)
        injected = InjectedScript(c, session)
        injected.install()
        life.goto(served, until="load", timeout=40)
        assert injected.title(life.main_frame) == "posix", (
            "the page loaded but its title did not come back through the pipe")
    finally:
        try:
            c.close()
        except Exception:
            pass


# A parent whose descriptor table has nothing on 3 and 4 and whose pipes land far from them launches a program that reads fd 3 and
# answers on fd 4, the contract of the engine (`nsRemoteDebuggingPipe.cpp` hard-wires the two numbers), and says what else it holds.
_SPAWN_PARENT = """
import fcntl, os, sys
from invisible_core.juggler import connection

for fd in range(3, 64):
    try:
        os.close(fd)
    except OSError:
        pass
real_pipe = os.pipe


def far_pipe():
    r, w = real_pipe()
    r2, w2 = fcntl.fcntl(r, fcntl.F_DUPFD, 20), fcntl.fcntl(w, fcntl.F_DUPFD, 20)
    os.close(r)
    os.close(w)
    return r2, w2


os.pipe = far_pipe
engine = ("import os; d = os.read(3, 16); "
          "extra = [fd for fd in range(5, 40) if os.path.exists('/proc/self/fd/%d' % fd) or os.path.exists('/dev/fd/%d' % fd)]; "
          "os.write(4, b'pong:' + d + b':' + str(len(extra)).encode())")
our_write, our_read, process = connection._spawn_posix(sys.executable, ["-c", engine], dict(os.environ))
os.write(our_write, b"ping")
sys.stdout.write(os.read(our_read, 64).decode())
process.wait(timeout=10)
"""


@pytest.mark.unit
def test_the_spawn_keeps_the_two_hardwired_descriptors():
    """What the launch must do, executed: the child it starts finds the pipe on descriptors 3 (it reads) and 4 (it writes), and no
    descriptor of the parent above them.

    ⛔ THIS ASSERTED THE TEXT OF THE CORE'S SOURCE and broke the day the core launched with `posix_spawn` and file actions instead of
    `pass_fds=(its_read, its_write, 3, 4)`: the behaviour did not change, the words did. It needs no Linux Firefox: the "engine" is a
    program that reads fd 3 and answers on fd 4, exactly the contract of `nsRemoteDebuggingPipe.cpp`. The day somebody changes the
    launch so that the browser starts with nothing on 4 (a live process whose first write gets EPIPE), this fails on every POSIX
    machine of the unit suite rather than on the one person who has a Linux build.
    """
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", _SPAWN_PARENT], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, ("the launch failed: %s" % (done.stderr.strip().splitlines()[-1:],))
    assert done.stdout.startswith("pong:ping:"), (
        "the child did not find the pipe on descriptors 3 and 4: it read nothing on 3 or its answer on 4 never came (%r)"
        % done.stdout)
    assert done.stdout.rsplit(":", 1)[-1] == "0", (
        "the child holds descriptors above 4 that the parent opened: " + done.stdout)
