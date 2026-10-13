"""Stopping a screencast always returns, and the browser goes on answering.

Until firefox-44 the engine created, started and stopped its capture module on
the parent's main thread. The window capturer reads the window with
PrintWindow, which waits for the thread that owns the window to answer - that
same main thread - while StopCapture waited for the capture thread without
answering any message. A stop that landed while a capture was inside
PrintWindow left the two waiting for each other, and Juggler answered nothing
more ([B277]). Measured on firefox-43 and on the first firefox-44 build, one
browser on the hidden desktop: wedged at the ninth stop, the main thread in a
condition variable under DesktopCaptureImpl::StopCapture and the capture
thread in NtUserPrintWindow. In the full e2e run it was a worker that died at
its 420 s deadline, once on the screencast rate test and once on the hidden
desktop test.

The stops run in a thread of their own, so a stop that never returns fails
this test naming the stop it hung on, instead of eating the run. The browser
is then ended from here, or it would stay wedged after the test.
"""
from __future__ import annotations

import random
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

#: Fifty stops: one browser wedged at the ninth on the builds that had the
#: defect, four browsers within the first fifteen stops between them.
STOPS = 50

#: The rates the public API is asked for in practice; the defect showed at
#: every one of them.
RATES = (2, 10, 25, 30)

#: A start or a stop that answers takes well under a tenth of a second
#: (measured over hundreds: 0.04 s at most). A wedged browser never answers,
#: so this only has to be long enough not to mistake a slow machine for it.
CALL_BUDGET_S = 30.0

PAGE = """<!doctype html><title>screencast stops</title>
<body style="margin:0"><canvas id=c width=800 height=600></canvas><script>
const g = document.getElementById('c').getContext('2d'); let i = 0;
(function f() { g.fillStyle = 'hsl(' + (i++ % 360) + ',80%,50%)';
  g.fillRect(0, 0, 800, 600); requestAnimationFrame(f); })();
</script>"""


def test_every_screencast_stop_returns_and_the_browser_still_answers(
        firefox_binary):
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright._reaper import find_processes, terminate

    session = InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                  headless=True)
    progress = {"phase": "launch", "since": time.monotonic(), "stops": 0,
                "title": None}
    failure: list[BaseException] = []

    def drive():
        try:
            with session as browser:
                page = browser.new_page()
                page.set_content(PAGE)
                rng = random.Random(7)
                for i in range(STOPS):
                    progress.update(phase="start", since=time.monotonic())
                    page.screencast.start(on_frame=lambda frame: None,
                                          fps=RATES[i % len(RATES)])
                    time.sleep(rng.uniform(0, 0.3))
                    progress.update(phase="stop", since=time.monotonic())
                    page.screencast.stop()
                    progress["stops"] += 1
                progress.update(phase="after", since=time.monotonic())
                progress["title"] = page.title()
                progress["phase"] = "done"
        except BaseException as e:  # handed to the test thread below
            failure.append(e)
            progress["phase"] = "failed"

    worker = threading.Thread(target=drive, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(1)
        phase = progress["phase"]
        if phase in ("start", "stop", "after") and (
                time.monotonic() - progress["since"] > CALL_BUDGET_S):
            hung = progress["stops"]
            terminate(find_processes(session._session_token))
            worker.join(30)
            pytest.fail(
                "screencast %s number %d did not return in %.0f s: the engine "
                "stopped answering (the capture module waited on the main "
                "thread, which waited on it)" % (phase, hung + 1, CALL_BUDGET_S))
    if failure:
        raise failure[0]
    assert progress["stops"] == STOPS
    assert progress["title"] == "screencast stops"
