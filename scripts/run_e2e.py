#!/usr/bin/env python3
"""Run the FULL e2e suite (every test that opens the browser) against a binary.

The 127 ``@pytest.mark.e2e`` tests are excluded from the default `pytest` run
(`addopts = -m 'not slow and not e2e'`) because they need a real Firefox binary
and a display, and they skip themselves when no binary is available. That makes
them easy to forget - and "we can't afford for something to not work". This is
the gate that runs them all, deliberately, against a chosen binary.

It is the MANDATORY pre-release e2e gate: run it green against the freshly-built
release binary BEFORE un-drafting a firefox-N (alongside the fppro + WebRTC
realness gates). It is NOT in the public CI drive-gate - the hosted runners are
content-process unstable under a heavy headless interaction sequence (see
70-known-bugs / 60-ci-release-pipeline); this runs locally on reliable hardware.

Flake-resilience: under full-suite load a couple of interaction tests (dblclick,
hover/mouseenter) can flake even though they pass 3/3 in isolation, so failures
are reran up to twice on the known transient signatures. A genuinely broken
binary fails all attempts. The webrtc e2e fake a TCP-only SOCKS locally (no
proxy/secrets), so the whole suite is offline.

Usage:
    python scripts/run_e2e.py <firefox-binary>
    python scripts/run_e2e.py            # uses $INVPW_BINARY_PATH
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_RERUN_SIGNATURES = "Timeout|context was destroyed|was detached|not visible|because of a navigation|TargetClosed"


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("INVPW_BINARY_PATH")
    if not binary:
        print("usage: run_e2e.py <firefox-binary>  (or set INVPW_BINARY_PATH)", file=sys.stderr)
        return 2
    if not Path(binary).exists():
        print(f"ERROR: binary not found: {binary}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    # One setting drives the whole suite: conftest's firefox_binary fixture and
    # the webrtc e2e both resolve from these.
    env["INVPW_BINARY_PATH"] = binary
    env["STEALTHFOX_E2E_BINARY"] = binary

    repo = Path(__file__).resolve().parent.parent
    cmd = [
        sys.executable, "-m", "pytest",
        "-m", "e2e",
        "-o", "addopts=",            # override the default 'not e2e' deselection
        "--reruns", "2", "--reruns-delay", "1",
        "--only-rerun", _RERUN_SIGNATURES,
        # A DEADLINE is not a flake, and retrying one multiplies it by three.
        # `Timeout` in the signatures above was written for Playwright's
        # TimeoutError, but it also matches `subprocess.TimeoutExpired` -
        # measured, 2 tests produced 4 reruns - and the release/upgrade e2e
        # spend their time in `pip install` calls whose timeouts run to 300s
        # each. Retried twice that is 900s for one test, and the two files sum
        # to 10050s of timeout budget against a 2400s job. That is how the job
        # was killed at 40 minutes twice with nothing to show for it.
        "--rerun-except", "TimeoutExpired",   # a subprocess deadline
        "--rerun-except", "Timeout >",        # pytest-timeout's own deadline
        # And a per-test deadline, so a hang FAILS WITH A NAME instead of eating
        # the job. 420s is 4.5x the slowest legitimate test measured on CI
        # (test_webgl_readpixels_no_masking_signature, 94.4s; the second slowest
        # is 11.4s and the whole suite is 318s), and 1/6 of the job budget.
        # METHOD=thread, not the signal default, and that is the whole point.
        # Measured 2026-08-13 on the run of this very change: the suite wedged in
        # `test_hover_triggers_mouseenter` and the 420s signal deadline came and
        # went with NOTHING at 19.3 minutes, 21 minutes, 40. SIGALRM is delivered
        # to the main thread and Python runs the handler at the next bytecode
        # boundary; Playwright's sync API is blocked in a greenlet waiting on the
        # driver socket, so that boundary never arrives. A watchdog THREAD does
        # not need the hung thread to cooperate: it dumps every thread's stack
        # and ends the process. The suite does not carry on, which is the price,
        # and in exchange a hang stops being anonymous.
        "--timeout", "420",
        "--timeout-method", "thread",
        "-p", "no:cacheprovider",
        # -v, not -q, and the reason is a hang we could not name twice.
        # Under -q pytest emits one character per test and the line only
        # reaches the log when it is full, so a run that dies mid-line says
        # nothing about where it died. Measured 2026-08-12 on two runs of the
        # SAME commit: both printed the identical `.s....[ 50%]` line at 78
        # seconds, one then finished in 4:47 and the other was killed by the
        # 40-minute timeout with no second line - so the hang was somewhere in
        # tests 73-141 and that is as close as the log could get. Same symptom
        # on 2026-08-04. PYTHONUNBUFFERED was already set and is not the
        # missing piece: the output does stream, the GRANULARITY was wrong.
        # One line per test costs 141 lines and names the last one that ran.
        "-v", "--tb=short",
    ] + sys.argv[2:]
    print(f"[run_e2e] binary={binary}")
    print(f"[run_e2e] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=repo, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
