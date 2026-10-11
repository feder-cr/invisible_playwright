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
import os
import socketserver
import sys
import tempfile
import threading
import time

import pytest

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

    with socketserver.TCPServer(("127.0.0.1", 0), H) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d/" % srv.server_address[1]
        finally:
            srv.shutdown()


@pytest.mark.e2e
def test_the_posix_pipe_carries_messages_both_ways(firefox_binary, served):
    from invisible_core.juggler import connection as conn
    from invisible_core.juggler.injected import InjectedScript
    from invisible_core.juggler.lifecycle import Lifecycle

    profile = tempfile.mkdtemp(prefix="posix_pipe_")
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


@pytest.mark.unit
def test_the_spawn_keeps_the_two_hardwired_descriptors():
    """The keep list must name 3 and 4, and this is a check on the CODE.

    ⛔ Written as a source assertion on purpose. The behavioural test above
    needs a Linux Firefox; this one needs nothing, so the day somebody
    "simplifies" the keep list back to the two pipe ends the failure arrives on
    every POSIX machine in the unit suite rather than on the one person who has
    a Linux build.
    """
    import inspect

    from invisible_core.juggler import connection as conn

    source = inspect.getsource(conn._spawn_posix)
    assert "pass_fds=(its_read, its_write, 3, 4)" in source, (
        "the spawn no longer keeps descriptors 3 and 4. CPython's close sweep "
        "runs AFTER preexec_fn, so the dup2'd descriptors are closed again "
        "before exec and the browser starts with nothing on 4 - a live process "
        "whose first write gets EPIPE.")
