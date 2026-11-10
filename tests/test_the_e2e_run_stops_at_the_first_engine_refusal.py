"""The e2e run stops at the first refusal of the engine instead of launching for 26 minutes.

Run on 59.58.0 against a firefox-59 engine whose gate wanted a declaration the core no longer made,
the suite went on for 26 minutes, launching the browser for every test and being refused every
time, and opened a crash window per refusal. The plugin in `tests/engine_refusal.py` reads the
text of a failed report and stops the run at the engine's own sentence.

The unit tests read the sentence. The integration tests run pytest, serial and under xdist, over
twenty tests of which the second fails with the engine's refusal: the old suite ran all twenty.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from engine_refusal import refusal_in, reason_to_stop

TESTS = Path(__file__).resolve().parent

SENTENCE = ("stealth: zoom.stealth.text.coverage_ladder is not declared. Engine rule 7 - a "
            "finite-domain value lives only in invisible_core, and a missing declaration refuses "
            "rather than falling back to a compiled default or to the host.")

# The failure as the wrapper's own tests print it (the browser's last output, indented).
PRINTED = ("E   invisible_playwright._pw._impl._errors.TargetClosedError: BrowserContext.new_page: "
           "Browser.newPage: the pipe closed:\nE     the browser exited with code 0\n"
           "E     its last output:\nE       Juggler listening to the pipe\nE       " + SENTENCE +
           "\nE       Exiting due to channel error.\n")


class Report:
    def __init__(self, failed, text, nodeid="tests/test_x.py::test_a", keywords=("e2e",)):
        self.failed, self.longreprtext, self.nodeid = failed, text, nodeid
        self.keywords = {k: 1 for k in keywords}


@pytest.mark.unit
def test_the_refusal_is_found_in_a_printed_failure():
    assert refusal_in(PRINTED) == SENTENCE[:300]


@pytest.mark.unit
def test_the_scale_refusal_is_found_too():
    text = ("E   stealth: layout.css.devPixelsPerPx is 1.5000, but the declared screen is 1920 CSS "
            "pixels over a 1920 device-pixel work area. The scale is part of the declaration "
            "(invisible_core sets it from the persona's DPR): pin screen.dpr.\n")
    assert refusal_in(text).startswith("stealth: layout.css.devPixelsPerPx")


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "", "AssertionError: 3 != 4", "TargetClosedError: the pipe closed: the browser exited with code 1",
    "stealth: something else the gate never says",
])
def test_any_other_failure_is_not_a_refusal(text):
    assert refusal_in(text) is None


@pytest.mark.unit
def test_only_a_failed_report_stops_the_run():
    assert reason_to_stop(Report(True, PRINTED)) is not None
    assert reason_to_stop(Report(False, PRINTED)) is None, "a test that expected the refusal passes"
    assert reason_to_stop(Report(True, "AssertionError")) is None


@pytest.mark.unit
def test_a_unit_test_that_quotes_the_sentence_does_not_stop_the_run():
    assert reason_to_stop(Report(True, PRINTED, keywords=("unit",))) is None


@pytest.mark.unit
def test_the_reason_names_the_sentence_and_the_first_test():
    reason = reason_to_stop(Report(True, PRINTED, "tests/test_mouse.py::test_click"))
    assert "coverage_ladder is not declared" in reason and "tests/test_mouse.py::test_click" in reason


# ── pytest itself, serial and parallel ─────────────────────────────────────

N = 20


def _project(root: Path, failing_text: str) -> None:
    (root / "conftest.py").write_text(textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from engine_refusal import StopAtTheFirstRefusal

        def pytest_configure(config):
            config.pluginmanager.register(StopAtTheFirstRefusal(config), "engine-refusal-stop")
        """ % str(TESTS)), encoding="utf-8")
    for i in range(N):
        body = ("import time, pytest\npytestmark = pytest.mark.e2e\n\n"
                "def test_%d():\n    time.sleep(0.4)\n" % i)
        if i == 1:
            body = ("import pytest\npytestmark = pytest.mark.e2e\n\ndef test_%d():\n"
                    "    raise RuntimeError(%r)\n" % (i, failing_text))
        (root / ("test_t%02d.py" % i)).write_text(body, encoding="utf-8")


def _run(root: Path, *extra: str) -> str:
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-rN", "--rootdir", str(root)] + list(extra),
                          cwd=root, capture_output=True, text=True, timeout=180)
    return done.stdout + done.stderr


def _ran(output: str) -> int:
    import re
    m = re.search(r"(\d+) failed", output)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) passed", output)
    return failed + (int(m.group(1)) if m else 0)


@pytest.mark.integration
def test_a_serial_run_stops_at_the_refusal(tmp_path):
    _project(tmp_path, SENTENCE)
    out = _run(tmp_path)
    assert "stopped at the first refusal of the engine" in out, out
    assert "coverage_ladder is not declared" in out, out
    assert _ran(out) <= 3, "the run went on after the refusal:\n" + out


@pytest.mark.integration
def test_a_parallel_run_stops_at_the_refusal(tmp_path):
    pytest.importorskip("xdist")
    _project(tmp_path, SENTENCE)
    out = _run(tmp_path, "-n", "2", "--dist", "load")
    assert "stopped at the first refusal of the engine" in out, out
    assert _ran(out) < N - 5, "the run went on after the refusal:\n" + out


@pytest.mark.integration
def test_a_failure_that_is_not_a_refusal_does_not_stop_the_run(tmp_path):
    _project(tmp_path, "an ordinary bug")
    out = _run(tmp_path)
    assert _ran(out) == N, out
    assert "stopped at the first refusal" not in out
