"""The release-triple hunt (`.github/workflows/e2e-hunt.yml`, scripts/hunt_report.py,
scripts/hunt_launch_close.py): its pure parts and the shape of the workflow.

The hunt is only worth the runner time if (a) the suite it repeats is exactly the one
`e2e.yml` runs, (b) every run is recorded and a caught failure does not turn the job red,
and (c) the failure messages come out whole. The first is checked against the two workflow
files, the other two on logs written the way pytest writes them.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hunt_launch_close as launch  # noqa: E402
import hunt_report as report  # noqa: E402

pytestmark = pytest.mark.unit

HUNT = (ROOT / ".github" / "workflows" / "e2e-hunt.yml").read_text(encoding="utf-8")
E2E = (ROOT / ".github" / "workflows" / "e2e.yml").read_text(encoding="utf-8")

PLAN = dict(which="all", suite_jobs=6, suite_runs_each=5, onecpu_runs=2, webgl_rounds=13,
            webgl_procs=4, launch_jobs=3, launch_cycles_each=100)

# ── a log as run_e2e.py writes it: two pytest passes, a rerun, a failure with a long message ──
LOG = """\
[run_e2e] binary=/x/firefox
tests/test_a.py::test_one PASSED [ 10%]
[gw1] [ 18%] RERUN tests/test_b.py::test_flaky
tests/test_b.py::test_flaky PASSED [ 20%]
=================================== FAILURES ===================================
__________________ test_popup[in-process-open] __________________
[gw3] linux -- Python 3.11.18 /x/python
tests/test_popup.py:169: in test_popup
    assert run.popups, "stock announces the popup"
E   AssertionError: stock announces the popup
E     order of events: click +0.00s, popup target +0.21s, ready never
E   assert []
_____________ test_launch _____________
tests/test_launch.py:215: in test_launch
E   invisible_playwright._pw._impl._errors.Error: BrowserType.launch: [Errno 32] Broken pipe
=========================== short test summary info ============================
FAILED tests/test_popup.py::test_popup[in-process-open] - AssertionError: stock announces the popup
assert []
FAILED tests/test_launch.py::test_launch - invisible_playwright._pw._impl._errors.Error: BrowserType.launch: [Errno 32] Broken pipe
============ 2 failed, 640 passed, 32 skipped, 1 rerun in 1238.26s (0:20:38) ============
[run_e2e] -m 'e2e and wall_clock', browsers at once: 1 (serial)
=============== 20 passed, 1845 deselected in 110.29s (0:01:50) ================
"""
GREEN_LOG = """\
=============== 640 passed, 32 skipped in 1000.00s (0:16:40) ================
=============== 20 passed, 1845 deselected in 110.29s (0:01:50) ================
"""


def rec(log, *, run=1, exit_code=1, cpus="all", seconds=1300):
    return report.run_record(log, run=run, of=5, label="suite x5 #1", cpus=cpus,
                             exit_code=exit_code, seconds=seconds)


# ── the plan ─────────────────────────────────────────────────────────────────

def test_all_plans_30_suite_runs_a_one_cpu_leg_3_launch_jobs_and_the_webgl_hunt():
    plan = report.make_plan(**PLAN)
    assert (plan["suite"], plan["webgl"], plan["launch"]) == ("true", "true", "true")
    legs = json.loads(plan["suite_matrix"])
    ordinary = [leg for leg in legs if leg["cpus"] == "all"]
    assert len(ordinary) == 6 and sum(leg["runs"] for leg in ordinary) == 30
    (one,) = [leg for leg in legs if leg["cpus"] == "1"]
    assert one["taskset"] == "taskset -c 0" and one["runs"] == 2 and "1-CPU" in one["label"]
    assert all(leg["taskset"] == "" for leg in ordinary)
    assert len({leg["slug"] for leg in legs}) == len(legs), "an artifact name per leg"
    launch_legs = json.loads(plan["launch_matrix"])
    assert [leg["cycles"] for leg in launch_legs] == [100, 100, 100]
    assert [leg["start"] for leg in launch_legs] == [1, 101, 201]


def test_the_job_ceiling_follows_the_runs_and_is_never_the_limit_of_a_run():
    legs = json.loads(report.make_plan(**dict(PLAN, suite_runs_each=5))["suite_matrix"])
    assert legs[0]["timeout"] >= 5 * 25 + 30          # a run is 18-20 minutes
    assert legs[-1]["timeout"] > legs[0]["timeout"]   # one CPU is slower
    assert all(leg["timeout"] <= 355 for leg in legs)


@pytest.mark.parametrize("which, expected", [
    ("suite", ("true", "false", "false")),
    ("webgl,launch", ("false", "true", "true")),
    ("webgl", ("false", "true", "false")),
    (" Launch , suite ", ("true", "false", "true")),
    ("all", ("true", "true", "true")),
])
def test_which_chooses_the_hunts_of_one_dispatch(which, expected):
    plan = report.make_plan(**dict(PLAN, which=which))
    assert (plan["suite"], plan["webgl"], plan["launch"]) == expected
    if expected[0] == "false":
        assert plan["suite_matrix"] == "[]"
    if expected[2] == "false":
        assert plan["launch_matrix"] == "[]"


@pytest.mark.parametrize("which", ["", "suites", "webgl,nope", ","])
def test_a_which_that_names_no_hunt_is_refused_before_any_runner_is_spent(which):
    with pytest.raises(ValueError):
        report.make_plan(**dict(PLAN, which=which))


def test_zero_one_cpu_runs_drops_that_leg_only():
    legs = json.loads(report.make_plan(**dict(PLAN, onecpu_runs=0))["suite_matrix"])
    assert len(legs) == 6 and all(leg["cpus"] == "all" for leg in legs)


# ── reading a log ────────────────────────────────────────────────────────────

def test_the_two_passes_are_added_and_the_failed_ids_are_named():
    p = report.parse_pytest_log(LOG)
    assert (p["passed"], p["failed"], p["skipped"], p["rerun"]) == (660, 2, 32, 1)
    assert p["summaries"] == 2
    assert p["failed_ids"] == ["tests/test_popup.py::test_popup[in-process-open]",
                               "tests/test_launch.py::test_launch"]
    assert p["rerun_ids"] == ["tests/test_b.py::test_flaky"]


def test_a_failure_message_is_kept_whole_with_its_order_of_events():
    p = report.parse_pytest_log(LOG)
    message = p["failures"]["tests/test_popup.py::test_popup[in-process-open]"]
    assert message.splitlines() == ["AssertionError: stock announces the popup",
                                    "  order of events: click +0.00s, popup target +0.21s, ready never",
                                    "assert []"]
    assert "[Errno 32] Broken pipe" in p["failures"]["tests/test_launch.py::test_launch"]


def test_a_message_missing_from_the_failures_section_falls_back_to_the_short_summary():
    log = ("=== short test summary info ===\n"
           "FAILED tests/t.py::test_x - AssertionError: boom\nassert 1 == 2\n"
           "=== 1 failed in 1.00s ===\n")
    p = report.parse_pytest_log(log)
    assert p["failures"]["tests/t.py::test_x"] == "AssertionError: boom\nassert 1 == 2"


def test_digits_do_not_make_two_messages_different():
    assert report.normalise("pids [63248] -> [63248, 63386] at 0x7fcf60 in 2.5 s") == \
        report.normalise("pids [1] -> [1, 22] at 0xab in 31.25 s")


# ── a run's verdict: caught or broken ────────────────────────────────────────

def test_a_red_run_with_a_summary_is_a_caught_failure():
    r = rec(LOG)
    assert r["verdict"] == "red" and r["failed"] == 2
    assert "tests/test_launch.py::test_launch" in report.run_line(r)


def test_a_green_run_is_green_and_a_run_that_never_reached_a_summary_is_broken():
    assert rec(GREEN_LOG, exit_code=0)["verdict"] == "green"
    assert rec("Traceback: pip failed\n", exit_code=2)["verdict"] == "no-verdict"
    assert rec("no summary here\n", exit_code=1)["verdict"] == "no-verdict"      # killed, not red
    assert rec(GREEN_LOG, exit_code=5)["verdict"] == "no-verdict"                 # nothing collected


def test_the_job_summary_is_red_only_for_a_broken_run(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    (tmp_path / "run_01.json").write_text(json.dumps(rec(LOG, run=1)))
    assert report.main(["suite-summary", str(tmp_path)]) == 0
    (tmp_path / "run_02.json").write_text(json.dumps(rec("x", run=2, exit_code=2)))
    assert report.main(["suite-summary", str(tmp_path)]) == 1
    assert report.main(["suite-summary", str(tmp_path / "nothing")]) == 1


# ── the tally ────────────────────────────────────────────────────────────────

def test_the_tally_counts_runs_green_runs_and_each_test_with_one_whole_message_per_distinct_message():
    other = LOG.replace("+0.21s", "+9.99s")                       # same message but for its numbers
    different = LOG.replace("ready never", "ready +1.00s, dropped")  # a message of its own
    records = [rec(LOG, run=1), rec(other, run=2), rec(different, run=3),
               rec(GREEN_LOG, run=4, exit_code=0), rec(GREEN_LOG, run=5, exit_code=0)]
    text = report.suite_tally(records)
    assert "runs total: 5" in text
    assert "runs fully green: 2" in text
    assert "tests/test_popup.py::test_popup[in-process-open]: failed in 3 of 5 runs" in text
    assert text.count("message (seen") == 2 + 1                   # popup: 2 distinct; launch: 1
    assert "order of events: click +0.00s, popup target +0.21s, ready never" in text
    assert "ready +1.00s, dropped" in text
    assert "tests/test_b.py::test_flaky: rerun in 3 of 5 runs" in text


def test_the_one_cpu_leg_is_on_its_own_line_and_is_not_in_the_rate():
    records = [rec(GREEN_LOG, run=1, exit_code=0), rec(GREEN_LOG, run=2, exit_code=0),
               rec(LOG, run=1, cpus="1"), rec(LOG, run=2, cpus="1")]
    text = report.suite_tally(records)
    ordinary, one_cpu = text.split("## 1-CPU job")
    assert "runs total: 2" in ordinary and "runs fully green: 2" in ordinary
    assert "test_popup" not in ordinary
    assert "runs total: 2" in one_cpu and "runs fully green: 0" in one_cpu
    assert "test_popup[in-process-open]: failed in 2 of 2 runs" in one_cpu


# ── the webgl hunt ───────────────────────────────────────────────────────────

WEBGL_FAIL = """\
=================================== FAILURES ===================================
_________________ test_webgl_getParameter_deterministic _________________
tests/test_fingerprint_consistency.py:386: in test_webgl_getParameter_deterministic
E   AssertionError: WebGL getParameter check failed: reason='gl null' webgl2=False errors=['Could not create a WebGL context, FEATURE_FAILURE_WEBGL_EXHAUSTED_DRIVERS'] round=None param=None
E   assert False
=========================== short test summary info ============================
FAILED tests/test_fingerprint_consistency.py::test_webgl_getParameter_deterministic - AssertionError
============ 1 failed in 12.00s ============
"""
WEBGL_PASS = "tests/test_fingerprint_consistency.py::test_webgl_getParameter_deterministic PASSED\n=== 1 passed in 9.00s ===\n"
TEST = "tests/test_fingerprint_consistency.py::test_webgl_getParameter_deterministic"


def wrec(log, round_, proc, code):
    return report.webgl_record(log, round_=round_, proc=proc, exit_code=code, test_id=TEST)


def test_a_webgl_process_is_pass_fail_with_the_whole_reason_or_no_verdict():
    assert wrec(WEBGL_PASS, 1, 1, 0)["status"] == "pass"
    failed = wrec(WEBGL_FAIL, 1, 2, 1)
    assert failed["status"] == "fail"
    assert "errors=['Could not create a WebGL context, FEATURE_FAILURE_WEBGL_EXHAUSTED_DRIVERS']" in failed["message"]
    skipped = wrec("=== 1 skipped in 0.1s ===\n", 1, 3, 0)
    assert skipped["status"] == "no-verdict"
    assert wrec("killed", 1, 4, 137)["status"] == "no-verdict"


def test_the_webgl_tally_says_n_failed_of_n_and_groups_the_reasons():
    other = WEBGL_FAIL.replace("EXHAUSTED_DRIVERS", "EXHAUSTED_DRIVERS_2")
    records = [wrec(WEBGL_PASS, 1, 1, 0), wrec(WEBGL_FAIL, 1, 2, 1), wrec(WEBGL_FAIL, 2, 1, 1),
               wrec(other, 2, 2, 1)]
    text = report.webgl_tally(records)
    assert "runs: 4" in text and "failed (caught): 3 of 4" in text
    assert "2 times (round 1 proc 2, round 2 proc 1), whole message:" in text
    assert "1 time (round 2 proc 2)" in text
    assert "EXHAUSTED_DRIVERS_2" in text


# ── scripts/hunt_launch_close.py ─────────────────────────────────────────────

ENGINE = "\n".join(["line %d" % i for i in range(1, 30)] + ["[Parent] write: BROKEN PIPE"]
                   + ["tail %d" % i for i in range(5)])


def test_the_marker_is_found_in_any_case_with_its_lines_around_it_whole():
    (excerpt,) = launch.find_marker(ENGINE, context=2)
    assert excerpt.splitlines() == ["line 28", "line 29", "[Parent] write: BROKEN PIPE", "tail 0", "tail 1"]
    assert launch.find_marker("all quiet\nnothing\n") == []
    assert launch.find_marker("Error: [Errno 32] Broken pipe") != []


def test_two_hits_close_together_are_one_excerpt_and_far_apart_are_two():
    near = "a\nbroken pipe\nb\nBroken Pipe\nc"
    assert len(launch.find_marker(near, context=2)) == 1
    far = "broken pipe\n" + "x\n" * 30 + "broken pipe"
    assert len(launch.find_marker(far, context=2)) == 2


def test_browser_processes_are_counted_by_executable_not_by_name(tmp_path):
    binary = tmp_path / "firefox"
    binary.write_text("")
    other = tmp_path / "other"
    other.write_text("")
    processes = [(str(binary), ["firefox", "-contentproc"]), ("", [str(binary), "-no-remote"]),
                 (str(other), [str(other)]), ("/usr/bin/python3", ["python3", "firefox"])]
    assert launch.count_browser_processes(processes, str(binary)) == 2
    assert launch.count_browser_processes([], str(binary)) == 0


def _cycle(n, *, status=0, captured=None, leftover=0, engine_captured=True):
    return launch.cycle_record(cycle=n, headless=bool(n % 2), status=status, seconds=3.21,
                               captured=captured or {"engine.log": "quiet", "stderr.log": ""},
                               leftover=leftover, engine_captured=engine_captured)


def test_a_cycle_with_the_marker_in_the_engine_log_says_where():
    r = _cycle(1, status=1, captured={"engine.log": ENGINE, "stderr.log": "Error: [Errno 32] Broken pipe"})
    assert r["broken_pipe"] and r["broken_pipe_in"] == ["engine.log", "stderr.log"]
    assert r["ok"] is False and set(r["excerpts"]) == {"engine.log", "stderr.log"}
    assert _cycle(2)["broken_pipe"] is False and _cycle(2)["ok"] is True


def test_the_launch_tally_counts_cycles_failures_broken_pipes_and_leftovers():
    records = [_cycle(1), _cycle(2, status=1, captured={"engine.log": ENGINE}), _cycle(3, leftover=2),
               _cycle(4, status="timeout", engine_captured=False)]
    t = launch.tally(records)
    assert t == {"cycles": 4, "failures": 2, "broken_pipe": 1, "leftover_cycles": 1,
                 "leftover_processes": 2, "engine_not_captured": 1}
    text = launch.render_summary(records, "#1")
    assert "[Parent] write: BROKEN PIPE" in text and "Cycle 2" in text


def test_the_script_without_a_binary_ends_non_zero_and_does_not_run_cycles(tmp_path, capsys):
    assert launch.main(["--binary", str(tmp_path / "missing"), "--out", str(tmp_path)]) == 2
    assert "binary not found" in capsys.readouterr().err


# ── the workflow ─────────────────────────────────────────────────────────────

def _job(name):
    m = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name), HUNT, re.M | re.S)
    assert m, "job %s is missing from e2e-hunt.yml" % name
    return m.group(1)


def test_the_three_hunts_and_the_tally_exist_and_the_old_loop_is_the_default():
    for name in ("plan", "suite", "suite-tally", "webgl", "launch-close"):
        _job(name)
    inputs = HUNT.split("permissions:")[0]
    assert re.search(r"hunt:\n(?:.*\n)*?\s+default: \"suite-loop\"\n(?:.*\n)*?\s+- release-triple", inputs)
    assert "if: inputs.hunt == 'suite-loop'" in _job("hunt")
    for name in ("plan",):
        assert "inputs.hunt == 'release-triple'" in _job(name)
    for name in ("which", "suite_jobs", "suite_runs_each", "suite_onecpu_runs", "webgl_rounds",
                 "webgl_procs", "launch_jobs", "launch_cycles_each"):
        assert re.search(r"^      %s:\n" % name, HUNT, re.M), name


def test_hunt_a_runs_the_command_of_e2e_yml():
    (ci_command,) = re.findall(r"^\s+run: (xvfb-run -a python -u scripts/run_e2e\.py \"\$FF\")$", E2E, re.M)
    suite = _job("suite")
    assert re.search(r"\$PRE %s > \"\$log\" 2>&1" % re.escape(ci_command), suite)
    assert not re.search(r"run_e2e\.py[^\n]*\s-n\b", suite), "the workers are the suite's own: four"
    assert re.search(r"\bseq 1 \"\$RUNS\"", suite) and "RUNS: ${{ matrix.runs }}" in suite
    # it never stops at the first red: the loop has no break and no exit on the code
    loop = suite.split("for n in")[1].split("done")[0]
    assert "break" not in loop and not re.search(r"(?m)^\s*exit", loop)


def test_hunt_a_has_a_one_cpu_leg_that_is_taskset_zero_and_checks_the_workers_start():
    assert "PRE: ${{ matrix.taskset }}" in _job("suite")
    assert "taskset -c 0" in report.make_plan(**PLAN)["suite_matrix"]
    pre = re.search(r"Preflight of the 1-CPU leg\n\s+if: matrix\.taskset != ''\n(.*?)- name:", _job("suite"), re.S)
    assert pre and "taskset -c 0" in pre.group(1) and "-n 4" in pre.group(1)


def test_hunt_b_runs_the_webgl_test_in_rounds_of_processes_started_together():
    webgl = _job("webgl")
    assert "tests/test_fingerprint_consistency.py::test_webgl_getParameter_deterministic" in webgl
    assert ") &\n" in webgl and 'wait "${pids[@]}"' in webgl
    assert "default: \"13\"" in HUNT and "default: \"4\"" in HUNT


def test_hunt_c_runs_the_script_in_jobs_of_100_cycles():
    launch_job = _job("launch-close")
    assert "scripts/hunt_launch_close.py" in launch_job and (ROOT / "scripts" / "hunt_launch_close.py").exists()
    assert "--cycles \"$CYCLES\"" in launch_job
    assert re.search(r"launch_cycles_each:\n(?:.*\n)*?\s+default: \"100\"", HUNT)


def test_every_job_that_makes_evidence_uploads_it_even_when_it_failed():
    for name in ("suite", "webgl", "launch-close"):
        job = _job(name)
        assert re.search(r"Upload the evidence\n\s+if: always\(\)\n\s+uses: actions/upload-artifact@", job), name
    assert "if: always() && needs.plan.outputs.suite == 'true'" in _job("suite-tally")


def test_the_hunt_stays_on_ubuntu_24_04_read_only_and_cancels_its_predecessor():
    for name in ("plan", "suite", "suite-tally", "webgl", "launch-close"):
        assert "runs-on: ubuntu-24.04" in _job(name)
    assert re.search(r"^permissions:\n  contents: read\n", HUNT, re.M)
    assert "group: ${{ github.workflow }}-${{ github.ref }}\n  cancel-in-progress: true" in HUNT
    assert re.search(r"^env:\n  GITHUB_TOKEN: \$\{\{ github\.token \}\}\n", HUNT, re.M)
    assert "contents: read\n      actions: read" in _job("suite-tally")


def test_every_action_is_pinned_by_a_sha_the_file_already_used():
    original = {"actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
                "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"}
    used = set(re.findall(r"uses: (\S+@\S+)", HUNT))
    assert used == original


def test_a_caught_failure_does_not_make_a_job_red():
    suite = _job("suite")
    # the suite step records the exit code and goes on; only the summary step can be red
    assert "code=$?" in suite and "set -e\n" in suite
    assert "Red here = a run without a verdict" in suite
    assert "ends 0 whatever the cycles did" in _job("launch-close")


def test_no_local_path_and_no_product_name_in_the_hunt_files():
    for text in (HUNT, (ROOT / "scripts" / "hunt_report.py").read_text(encoding="utf-8"),
                 (ROOT / "scripts" / "hunt_launch_close.py").read_text(encoding="utf-8")):
        assert not re.search(r"[A-Za-z]:[\\/](?:Users|ff)\b|/Users/|C:\\\\", text)


# ── the popups hunt (which=popups) ───────────────────────────────────────────

SHA_A = "a90dadc204ac2de807038531c3f03a80729328cf"
SHA_B = "b" * 40
ATTACH_LOG = LOG.replace("AssertionError: stock announces the popup",
                         "AssertionError: stock announces the popup (its url is empty) before it closes")


def test_popups_is_chosen_by_name_and_never_part_of_all():
    plan = report.make_plan(**dict(PLAN, popup_shas=SHA_A))
    assert plan["popups"] == "false" and plan["popup_matrix"] == "[]"
    plan = report.make_plan(**dict(PLAN, which="popups", popup_shas=SHA_A))
    assert (plan["suite"], plan["webgl"], plan["launch"], plan["popups"]) == ("false",) * 3 + ("true",)


def test_popups_plans_jobs_for_every_sha_in_parallel_with_the_runs_each():
    plan = report.make_plan(**dict(PLAN, which="popups", popup_shas="%s, %s,%s" % (SHA_A, SHA_B, SHA_A),
                                   popup_jobs_each=5, popup_runs_each=2))
    legs = json.loads(plan["popup_matrix"])
    assert len(legs) == 10 and sum(leg["runs"] for leg in legs) == 20      # 10 runs per sha, a repeated sha once
    assert {leg["sha"] for leg in legs} == {SHA_A, SHA_B}
    assert len({leg["slug"] for leg in legs}) == 10
    assert all(leg["timeout"] >= 2 * 30 + 30 for leg in legs)


def test_popups_can_run_together_with_another_hunt():
    plan = report.make_plan(**dict(PLAN, which="popups,webgl", popup_shas=SHA_A))
    assert (plan["popups"], plan["webgl"], plan["suite"]) == ("true", "true", "false")


@pytest.mark.parametrize("kwargs", [dict(popup_shas=""), dict(popup_shas=" , "),
                                    dict(popup_shas=SHA_A, popup_jobs_each=0),
                                    dict(popup_shas=SHA_A, popup_runs_each=0)])
def test_popups_without_a_commit_or_runs_is_refused_before_any_runner_is_spent(kwargs):
    with pytest.raises(ValueError):
        report.make_plan(**dict(PLAN, which="popups", **kwargs))


def test_the_attachment_failure_is_told_from_the_others():
    attach = "AssertionError: stock announces the popup (its url is empty) before it closes\nassert []"
    assert report.classify_popup_failure("t", attach) == "attachment"
    assert report.classify_popup_failure("t", "assert []\n + where [] = <_Run object>.popups") == "attachment"
    assert report.classify_popup_failure("t", "Error: BrowserType.launch: [Errno 32] Broken pipe") == "other"
    assert report.classify_popup_failure("t", "") == "other"


def _popup_rec(log, sha, run, label="popups aaaaaaa #1 x2", exit_code=1):
    return report.run_record(log, run=run, of=2, label=label, cpus="1", exit_code=exit_code,
                             seconds=600, sha=sha)


def test_the_popups_tally_is_per_commit_with_whole_messages_and_one_line_per_red_run():
    attachment_only = ATTACH_LOG.replace("FAILED tests/test_launch.py::test_launch", "PASSED x")
    records = [_popup_rec(attachment_only.replace("\n_____________ test_launch _____________", "\n"), SHA_A, 1),
               _popup_rec(LOG, SHA_A, 2),
               _popup_rec(GREEN_LOG, SHA_B, 1, label="popups bbbbbbb #1 x2", exit_code=0),
               _popup_rec(GREEN_LOG, SHA_B, 2, label="popups bbbbbbb #1 x2", exit_code=0)]
    text = report.popups_tally(records)
    first, second = text.split("## commit %s" % SHA_B)
    assert "runs total: 2" in first and "runs with at least one red: 2" in first
    assert "ready never" in first                                          # the whole message, with its events
    assert "order of events: click +0.00s, popup target +0.21s" in first
    assert "run 2/2: other (also 1 attachment): " in first                 # the second red run has a launch failure too
    assert "run 1/2: " in first
    assert "runs total: 2" in second and "runs with at least one red: 0" in second and "- none" in second


def test_a_run_with_only_the_attachment_failure_is_classified_as_that():
    log = ("=== FAILURES ===\n___ test_a ___\nE   AssertionError: stock announces the popup (its url is empty)\n"
           "E   assert []\n=== short test summary info ===\nFAILED tests/t.py::test_a - AssertionError\n"
           "=== 1 failed, 20 passed in 5.00s ===\n")
    text = report.popups_tally([_popup_rec(log, SHA_A, 1)])
    assert "run 1/2: attachment / run.popups empty: tests/t.py::test_a" in text


def test_the_popups_job_checks_out_the_commit_but_takes_this_file_from_the_dispatch_ref():
    job = _job("popups")
    assert "ref: \"${{ matrix.sha }}\"" in job and "path: code" in job and "path: hunt-tools" in job
    assert "./code[dev]" in job and "hunt-tools/scripts/hunt_report.py" in job
    assert "tests/test_popup_destinations_e2e.py tests/test_popups_reach_the_client_e2e.py" in job
    # the suite's own entry (its options are the commit's), on one CPU, four workers, in a row
    assert re.search(r"taskset -c 0 xvfb-run -a python -u scripts/run_e2e\.py \"\$FF\"", job)
    assert not re.search(r"run_e2e\.py[^\n]*\s-n\b", job)
    assert "-n 4" in job.split("Preflight")[1].split("- name:")[0]
    assert re.search(r"Upload the evidence\n\s+if: always\(\)\n\s+uses: actions/upload-artifact@", job)
    assert "break" not in job and "--sha \"$SHA\"" in job
    assert "if: always() && needs.plan.outputs.popups == 'true'" in _job("popups-tally")
    assert "popup_shas" in HUNT.split("permissions:")[0]
    assert "origin/${x}^{commit}" in _job("plan") and "fetch-depth: 0" in _job("plan")
