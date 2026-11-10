"""The e2e run stops at the first refusal of the engine.

The engine refuses a launch whose declarations are incomplete ("stealth: zoom.stealth.X is not
declared. Engine rule 7 ..."), and it refuses it the same way for every test that follows: the
core and the engine do not agree, and no test after the first can say anything the first did not.
Before this plugin the run went on for 26 minutes, launching the browser for each of ~450 tests
and filling the desktop with crash windows, to report one cause 313 times.

The plugin reads the text of a FAILED report. A refusal in it is the engine's own sentence, and
the run is stopped there, with that sentence as the reason:

* serial: `session.shouldstop` on the test's session (pytest stops before the next test);
* under xdist: the controller's `DSession.shouldstop` (the workers are told to finish, and the
  tests that have not started do not start). A worker's own session is not touched: stopping a
  worker alone would make the controller schedule its tests on the others.

A test that EXPECTS a refusal (`pytest.raises`) does not fail, so it never reaches here.
"""
from __future__ import annotations

import re
from typing import Optional

#: The sentences the engine's gate writes through StealthRefuse (StealthDeclarationGate.cpp).
REFUSAL = re.compile(
    r"stealth: [^\n]*(?:is not declared\. Engine rule 7"
    r"|The scale is part of the declaration)")


def refusal_in(text: str) -> Optional[str]:
    """The engine's refusal sentence in `text` (one line, as printed), or None."""
    match = REFUSAL.search(text or "")
    if not match:
        return None
    end = text.find("\n", match.end())
    return text[match.start():end if end != -1 else None].strip()[:300]


def reason_to_stop(report) -> Optional[str]:
    """Why the run must stop after this report, or None. Only the failure of an e2e test counts:
    a unit test that quotes the sentence (this plugin's own) did not launch the engine."""
    if not getattr(report, "failed", False):
        return None
    if "e2e" not in getattr(report, "keywords", {}):
        return None
    sentence = refusal_in(getattr(report, "longreprtext", "") or "")
    if sentence is None:
        return None
    return ("the engine refused a launch (%s): every test after this one would launch the same "
            "browser and be refused the same way. First seen in %s" % (sentence, report.nodeid))


class StopAtTheFirstRefusal:
    """The pytest plugin. Register an instance with `config.pluginmanager.register`."""

    def __init__(self, config) -> None:
        self.config = config
        self.stopped_by: Optional[str] = None
        self.session = None

    def _controller(self):
        return self.config.pluginmanager.getplugin("dsession")

    def pytest_sessionstart(self, session) -> None:
        self.session = session

    def pytest_runtest_logreport(self, report) -> None:
        if self.stopped_by is not None:
            return
        reason = reason_to_stop(report)
        if reason is None:
            return
        self.stopped_by = reason
        controller = self._controller()
        if controller is not None:                    # the controller of an xdist run
            controller.shouldstop = reason
        elif not hasattr(self.config, "workerinput"):  # the serial path (a worker has workerinput)
            self.session.shouldstop = reason

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.stopped_by:
            terminalreporter.write_sep("!", "stopped at the first refusal of the engine")
            terminalreporter.write_line(self.stopped_by)
