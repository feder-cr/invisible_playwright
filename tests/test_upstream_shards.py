"""The upstream suite is split over CI jobs by file, and judged from the pieces.

`scripts/upstream_baseline.py --shard K/N` decides which test files a job runs
(by the time each file took in a recorded run), and
`scripts/upstream_regressions.py` merges the jobs' reports. Neither needs a
browser, and each property is checked on the input it exists for: a file that
two jobs both run or that none runs, a shard that is far heavier than the
others, a shard that vanished and left the rest looking green, a report that
is missing.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import zlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import upstream_baseline as base  # noqa: E402
import upstream_regressions as reg  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "upstream.yml"
BASELINE = json.loads((ROOT / "tests" / "gates" / "upstream_baseline.json")
                      .read_text(encoding="utf-8"))
WEIGHTS = base.load_weights()


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _baseline_files() -> list:
    """The suite-relative file of every recorded test id."""
    return sorted({base.file_of(k.split("::")[0]) for k in BASELINE})


def _plan() -> dict:
    """{OS name: (shard count, step limit in minutes)} as the workflow runs it."""
    entries = re.findall(
        r"- \{ os: (\S+), shard: (\d+), of: (\d+), limit: (\d+) \}", _workflow())
    plan: dict = {}
    for runner, shard, of, limit in entries:
        name = "Windows" if runner.startswith("windows") else "Linux"
        got = plan.setdefault(name, {"shards": [], "of": of, "limit": limit})
        assert (got["of"], got["limit"]) == (of, limit), "one OS, one plan"
        got["shards"].append(int(shard))
    for name, got in plan.items():
        assert got["shards"] == list(range(1, int(got["of"]) + 1)), name
    return {n: (int(g["of"]), int(g["limit"])) for n, g in plan.items()}


def _universe(os_name: str) -> list:
    """Every file a job could meet: the baseline's, the weights', and one that
    nobody has timed (test_browsercontext_credentials is such a file today)."""
    return sorted(set(_baseline_files()) | set(WEIGHTS["seconds"][os_name])
                  | {"tests/sync/test_never_timed.py"})


def _seconds(os_name: str) -> dict:
    return base.planned_seconds(WEIGHTS, os_name, _universe(os_name))


@pytest.mark.parametrize("text, expected", [("1/4", (1, 4)), (" 4/4 ", (4, 4)),
                                            ("1/1", (1, 1)), ("8/8", (8, 8))])
def test_a_shard_is_written_k_over_n(text, expected):
    assert base.parse_shard(text) == expected


@pytest.mark.parametrize("text", ["0/4", "5/4", "2", "a/b", "2/0", "-1/4", ""])
def test_a_malformed_or_out_of_range_shard_is_refused(text):
    with pytest.raises(ValueError):
        base.parse_shard(text)


@pytest.mark.parametrize("classname, expected", [
    ("tests.sync.test_page", "tests/sync/test_page.py"),
    ("tests.sync.test_page_clock.TestRunFor", "tests/sync/test_page_clock.py"),
    ("tests.async.test_x", "tests/async/test_x.py")])
def test_a_junit_classname_is_a_file_with_no_class(classname, expected):
    assert base.file_of(classname) == expected


# ── the assignment ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16])
def test_every_file_belongs_to_exactly_one_shard(os_name, count):
    files = _universe(os_name)
    assert len(files) > 50
    owner = base.assign(_seconds(os_name), count)
    assert sorted(owner) == files
    assert all(0 <= k < count for k in owner.values())
    per_shard = [[f for f in files if owner[f] == k] for k in range(count)]
    assert sorted(f for part in per_shard for f in part) == files


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_the_assignment_does_not_depend_on_order_or_on_the_run(os_name):
    secs = _seconds(os_name)
    first = base.assign(secs, 7)
    assert base.assign(dict(reversed(list(secs.items()))), 7) == first
    assert base.assign(dict(sorted(secs.items())), 7) == first
    assert base.assign(dict(secs), 7) == first


def test_equal_weights_are_split_by_path_not_by_luck():
    secs = {"tests/sync/test_%s.py" % c: 10.0 for c in "dcbaef"}
    owner = base.assign(secs, 3)
    # sorted by path, dealt to the lightest shard, the lowest index first
    assert [owner["tests/sync/test_%s.py" % c] for c in "abcdef"] == [
        0, 1, 2, 0, 1, 2]


def test_the_heaviest_file_goes_first_into_the_lightest_shard():
    secs = {"a.py": 50.0, "b.py": 40.0, "c.py": 30.0, "d.py": 20.0, "e.py": 10.0}
    owner = base.assign(secs, 2)
    # 50 | 40 ; 30 -> second (40 < 50) ; 20 -> first (50 < 70) ; 10 -> first
    # (70 = 70 and ties go to the lower index)
    assert owner == {"a.py": 0, "b.py": 1, "c.py": 1, "d.py": 0, "e.py": 0}
    assert base.shard_loads(secs, 2) == [80.0, 70.0]


def test_a_file_missing_from_the_weights_gets_the_median():
    weights = {"overhead_per_file": {"Linux": 2.0, "Windows": 3.0},
               "seconds": {"Linux": {"a.py": 1.0, "b.py": 5.0, "c.py": 100.0},
                           "Windows": {"a.py": 1.0}}}
    got = base.planned_seconds(weights, "Linux", ["a.py", "new.py"])
    assert got == {"a.py": 1.0 + 2.0, "new.py": 5.0 + 2.0}  # median 5, not mean
    assert base.planned_seconds(weights, "Windows", ["zz.py"]) == {"zz.py": 4.0}


def test_the_unknown_file_of_the_committed_weights_is_planned_at_the_median():
    for os_name in ("Linux", "Windows"):
        secs = _seconds(os_name)
        median = statistics.median(WEIGHTS["seconds"][os_name].values())
        per_file = WEIGHTS["overhead_per_file"][os_name]
        assert secs["tests/sync/test_never_timed.py"] == pytest.approx(
            median + per_file)


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_the_committed_weights_cover_the_recorded_suite(os_name):
    known = set(WEIGHTS["seconds"][os_name])
    assert set(_baseline_files()) <= known, sorted(set(_baseline_files()) - known)
    # what is only an estimate says so
    assert set(WEIGHTS["estimated"][os_name]) <= known
    assert WEIGHTS["runs"][os_name]["run_id"] and WEIGHTS["runs"][os_name]["date"]


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_the_planned_shards_are_balanced_and_fit_the_budget(os_name):
    count, limit = _plan()[os_name]
    loads = base.shard_loads(_seconds(os_name), count)
    assert min(loads) > 0
    assert max(loads) / min(loads) < 1.5, [round(x) for x in loads]
    # 8 minutes of tests at most, and the step limit is about twice the heaviest
    assert max(loads) <= 8 * 60, [round(x) for x in loads]
    assert 1.6 <= limit * 60 / max(loads) <= 2.5, (limit, max(loads) / 60)


def test_no_single_file_is_over_the_budget_of_a_shard():
    for os_name in ("Linux", "Windows"):
        assert max(_seconds(os_name).values()) <= 8 * 60, os_name


def test_the_plan_fits_in_sixteen_jobs_and_names_both_systems():
    plan = _plan()
    assert set(plan) == {"Linux", "Windows"}
    assert sum(count for count, _ in plan.values()) <= 16


def test_the_old_hash_split_would_have_been_far_off_these_weights():
    # The reason the weights exist: 4 shards by crc32 of the path, planned with
    # the same numbers, are 12 / 12 / 6 / 3 minutes on Linux.
    secs = _seconds("Linux")
    load = [0.0] * 4
    for f, t in secs.items():
        load[zlib.crc32(f.encode()) % 4] += t
    assert max(load) / min(load) > 3


def test_shard_files_selects_from_the_suite_on_disk(tmp_path, monkeypatch):
    sync = tmp_path / "tests" / "sync"
    sync.mkdir(parents=True)
    names = ["test_%s.py" % c for c in "abcdefghij"]
    for n in names:
        (sync / n).write_text("", encoding="utf-8")
    (sync / "conftest.py").write_text("", encoding="utf-8")
    (sync / "helper.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(base, "SUITE", tmp_path)

    for os_name in ("Linux", "Windows"):
        picked = [base.shard_files("tests/sync", (k, 3), os_name)
                  for k in (1, 2, 3)]
        flat = [f for part in picked for f in part]
        assert sorted(flat) == sorted("tests/sync/" + n for n in names)
        assert len(flat) == len(set(flat))
        # the same call gives the same answer: a job re-run must own the same
        # files
        assert picked == [base.shard_files("tests/sync", (k, 3), os_name)
                          for k in (1, 2, 3)]


def test_without_shard_the_command_line_still_names_the_directory(
        tmp_path, monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(base, "SUITE", tmp_path)
    (tmp_path / "tests" / "sync").mkdir(parents=True)
    (tmp_path / "tests" / "sync" / "test_a.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(base, "write_plugin", lambda: tmp_path / "p.py")
    monkeypatch.setattr(base.subprocess, "run", fake_run)

    monkeypatch.setattr(sys, "argv", ["upstream_baseline.py", "ff"])
    assert base.main() == 0
    assert "tests/sync" in seen[-1]
    assert not any(x.endswith("test_a.py") for x in seen[-1])

    monkeypatch.setattr(sys, "argv", ["upstream_baseline.py", "ff",
                                      "--shard", "1/1"])
    assert base.main() == 0
    assert "tests/sync/test_a.py" in seen[-1]
    assert "tests/sync" not in seen[-1]


# ── refreshing the weights from junit reports ────────────────────────────────

def _timed_junit(path: Path, suite_time: float, *cases: tuple) -> str:
    rows = "".join('<testcase classname="%s" name="%s" time="%s"/>' % c
                   for c in cases)
    path.write_text('<testsuites><testsuite name="p" time="%s">%s</testsuite>'
                    "</testsuites>" % (suite_time, rows), encoding="utf-8")
    return str(path)


def test_write_weights_sums_every_attempt_of_a_file_and_keeps_the_rest(tmp_path):
    target = tmp_path / "w.json"
    target.write_text(json.dumps({
        "format": 1, "runs": {}, "overhead_per_file": {"Linux": 1.0},
        "seconds": {"Linux": {"tests/sync/test_old.py": 9.0,
                              "tests/sync/test_a.py": 99.0}},
        "estimated": {"Linux": ["tests/sync/test_a.py",
                                "tests/sync/test_old.py"]},
    }), encoding="utf-8")
    one = _timed_junit(tmp_path / "upstream-Linux-1.xml", 30.0,
                       ("tests.sync.test_a", "t1", 10.0),
                       ("tests.sync.test_a", "t1", 10.0),   # a rerun
                       ("tests.sync.test_b.TestX", "t2", 5.0))
    data = base.write_weights([one], "777", "2026-12-01", path=target)
    secs = data["seconds"]["Linux"]
    assert secs["tests/sync/test_a.py"] == 20.0
    assert secs["tests/sync/test_b.py"] == 5.0
    assert secs["tests/sync/test_old.py"] == 9.0      # not in this run: kept
    assert data["estimated"]["Linux"] == ["tests/sync/test_old.py"]
    assert data["runs"]["Linux"] == {"run_id": "777", "date": "2026-12-01"}
    # 30 s of suite - 25 s of testcases = 5 s outside, over 2 files
    assert data["overhead_per_file"]["Linux"] == 2.5
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_write_weights_reads_the_os_from_the_file_name(tmp_path):
    target = tmp_path / "w.json"
    lin = _timed_junit(tmp_path / "upstream-Linux-1.xml", 10.0,
                       ("tests.sync.test_a", "t", 4.0))
    win = _timed_junit(tmp_path / "upstream-Windows-1.xml", 12.0,
                       ("tests.sync.test_a", "t", 6.0))
    data = base.write_weights([lin, win], "1", "2026-12-01", path=target)
    assert data["seconds"]["Linux"] == {"tests/sync/test_a.py": 4.0}
    assert data["seconds"]["Windows"] == {"tests/sync/test_a.py": 6.0}
    with pytest.raises(ValueError):
        base.os_of_report("upstream-1.xml")


def test_the_write_weights_option_runs_nothing_and_asks_for_no_binary(
        tmp_path, monkeypatch, capsys):
    target = tmp_path / "w.json"
    monkeypatch.setattr(base, "WEIGHTS", target)
    one = _timed_junit(tmp_path / "upstream-Linux-3.xml", 10.0,
                       ("tests.sync.test_a", "t", 4.0))
    monkeypatch.setattr(base.subprocess, "run",
                        lambda *a, **k: pytest.fail("ran"))
    monkeypatch.setattr(sys, "argv", ["upstream_baseline.py", "--write-weights",
                                      one, "--run-id", "42"])
    assert base.main() == 0
    assert json.loads(target.read_text(encoding="utf-8"))["runs"]["Linux"][
        "run_id"] == "42"
    assert "Linux: 1 files" in capsys.readouterr().out


# ── merging the reports ──────────────────────────────────────────────────────

def _junit(path: Path, *cases: tuple) -> str:
    path.write_text(reg._xml(*cases), encoding="utf-8")
    return str(path)


def _reference(tmp_path, monkeypatch, recorded: dict) -> None:
    ref = tmp_path / "reference.json"
    ref.write_text(json.dumps(recorded), encoding="utf-8")
    monkeypatch.setattr(reg, "REFERENCE", ref)


def _run(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["upstream_regressions.py", *argv])
    return reg.main()


RECORDED = {"a::t1": "passed", "a::t2": "passed", "b::t3": "failed",
            "c::t4": "passed"}


def test_the_reports_of_all_shards_are_judged_together(tmp_path, monkeypatch,
                                                       capsys):
    _reference(tmp_path, monkeypatch, RECORDED)
    one = _junit(tmp_path / "1.xml", ("a", "t1", None), ("a", "t2", None))
    two = _junit(tmp_path / "2.xml", ("b", "t3", "failure"), ("c", "t4", None))
    assert _run(monkeypatch, "--check", one, two) == 0
    assert "none. 4 recorded" in capsys.readouterr().out


def test_a_regression_in_any_shard_fails_the_merge(tmp_path, monkeypatch,
                                                   capsys):
    _reference(tmp_path, monkeypatch, RECORDED)
    one = _junit(tmp_path / "1.xml", ("a", "t1", None), ("a", "t2", None))
    two = _junit(tmp_path / "2.xml", ("b", "t3", "failure"), ("c", "t4", "failure"))
    assert _run(monkeypatch, "--check", one, two) == 1
    assert "REGRESSION  c::t4" in capsys.readouterr().out


def test_a_missing_report_is_not_read_as_no_tests(tmp_path, monkeypatch, capsys):
    _reference(tmp_path, monkeypatch, RECORDED)
    one = _junit(tmp_path / "1.xml", ("a", "t1", None), ("a", "t2", None),
                 ("b", "t3", "failure"), ("c", "t4", None))
    with pytest.raises(SystemExit) as stop:
        _run(monkeypatch, "--check", one, str(tmp_path / "gone.xml"))
    assert stop.value.code == 2
    assert "MISSING REPORT" in capsys.readouterr().out


def test_an_unreadable_report_stops_the_run(tmp_path, monkeypatch, capsys):
    _reference(tmp_path, monkeypatch, RECORDED)
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuites><testsuite>", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        _run(monkeypatch, "--check", str(bad))
    assert stop.value.code == 2
    assert "UNREADABLE REPORT" in capsys.readouterr().out


def test_a_vanished_shard_trips_the_floor_even_with_no_regression(
        tmp_path, monkeypatch, capsys):
    # Without a floor this is green: what was not collected cannot regress.
    _reference(tmp_path, monkeypatch, RECORDED)
    only = _junit(tmp_path / "1.xml", ("a", "t1", None), ("a", "t2", None))
    assert _run(monkeypatch, "--check", only) == 0
    assert _run(monkeypatch, "--check", "--min-fraction", "0.95", only) == 1
    assert "TOO FEW TESTS COLLECTED: 2 of the 4" in capsys.readouterr().out


def test_the_floor_is_met_when_everything_recorded_was_collected(
        tmp_path, monkeypatch):
    _reference(tmp_path, monkeypatch, RECORDED)
    full = _junit(tmp_path / "1.xml", ("a", "t1", None), ("a", "t2", None),
                  ("b", "t3", "failure"), ("c", "t4", None),
                  ("d", "new_test", None))
    assert _run(monkeypatch, "--check", "--min-fraction", "1.0", full) == 0


def test_the_floor_of_the_real_baseline_loses_a_big_shard_but_not_a_rename():
    n = len(BASELINE)
    floor = reg.floor_of(n, 0.95)
    for os_name, (count, _) in _plan().items():
        owner = base.assign(_seconds(os_name), count)
        per_shard = [0] * count
        for k in BASELINE:
            per_shard[owner[base.file_of(k.split("::")[0])]] += 1
        assert sum(per_shard) == n
        # losing a shard that owns more than the 5% the floor allows fails it;
        # a smaller one is caught by its job failing and by its missing report
        assert all(n - lost < floor for lost in per_shard
                   if lost > n - floor), (os_name, n, floor, per_shard)
    # and a handful of renamed tests does not
    assert n - 5 >= floor


def test_windows_differences_are_listed_apart_and_never_change_the_exit(
        tmp_path, monkeypatch, capsys):
    _reference(tmp_path, monkeypatch, {"a::t1": "passed", "b::t3": "failed"})
    linux = tmp_path / "linux.json"
    linux.write_text(json.dumps({"a::t1": "passed", "b::t3": "failed"}),
                     encoding="utf-8")
    win = _junit(tmp_path / "w.xml", ("a", "t1", None), ("b", "t3", None))
    out = tmp_path / "differs.txt"
    code = _run(monkeypatch, "--check", "--compare-to", str(linux),
                "--diff-out", str(out), win)
    assert code == 0
    assert "b::t3: failed -> passed" in out.read_text(encoding="utf-8")
    assert "b::t3" not in capsys.readouterr().out


def test_save_merged_writes_what_was_judged(tmp_path, monkeypatch):
    _reference(tmp_path, monkeypatch, {"a::t1": "passed", "b::t3": "failed"})
    one = _junit(tmp_path / "1.xml", ("a", "t1", None))
    two = _junit(tmp_path / "2.xml", ("b", "t3", "failure"))
    saved = tmp_path / "merged.json"
    assert _run(monkeypatch, "--check", "--save-merged", str(saved), one, two) == 0
    assert json.loads(saved.read_text(encoding="utf-8")) == {
        "a::t1": "passed", "b::t3": "failed"}


# ── the workflow keeps the assumptions the scripts make ──────────────────────

def test_the_workflow_matrix_matches_the_counts_it_passes_and_the_verdict_expects():
    text = _workflow()
    plan = _plan()
    assert set(plan) == {"Linux", "Windows"}
    # every `K/N` the jobs pass takes N from the matrix, not from a literal
    assert set(re.findall(r'--shard "\$\{\{ matrix\.shard \}\}/([^"]+)"',
                          text)) == {"${{ matrix.of }}"}
    assert "timeout-minutes: ${{ matrix.limit }}" in text
    # the verdict collects exactly the reports those jobs write
    verdict = re.search(r"for plan in ((?:\w+:\d+ ?)+); do", text)
    assert verdict, "the verdict must loop over the OS:count plan"
    expected = {os_name: count for os_name, (count, _) in plan.items()}
    got = dict((a, int(b)) for a, b in
               (x.split(":") for x in verdict.group(1).split()))
    assert got == expected
    assert 'seq 1 "$count"' in text


def test_nothing_in_the_workflow_is_allowed_to_fail_quietly():
    text = _workflow()
    assert "continue-on-error" not in text
    assert "|| true" not in text
    assert "secrets." not in text


def test_the_third_party_actions_are_pinned_to_the_shas_the_e2e_uses():
    e2e = (ROOT / ".github" / "workflows" / "e2e.yml").read_text(encoding="utf-8")
    pin = re.compile(r"uses: (actions/[\w-]+)@([0-9a-f]{40}) # (v\d+)")
    known = {(m.group(1), m.group(2)) for m in pin.finditer(e2e)}
    used = re.findall(r"uses: (actions/[\w-]+)@(\S+)", _workflow())
    assert used
    unpinned = [u for u in used if u not in known]
    assert not unpinned, "pin these like e2e.yml does: %s" % unpinned


def test_the_pinned_upstream_commit_is_the_tag_in_the_pin_file():
    text = _workflow()
    sha = re.search(r"UPSTREAM_SHA: ([0-9a-f]{40})", text)
    assert sha, "the clone must be checked against a commit"
    assert re.search(r"playwright_pin\.txt", text)
    pin = (ROOT / "scripts" / "playwright_pin.txt").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin)
    # the tag named in the explanatory comment is the one in the pin file
    assert "tag v%s" % pin in text


def test_the_baseline_keys_are_the_module_names_the_layout_produces():
    # tests/playwright-upstream/tests/sync/test_x.py, rooted at its own
    # pyproject, is what makes pytest name a module `tests.sync.test_x`.
    assert all(k.startswith("tests.sync.") for k in BASELINE)
