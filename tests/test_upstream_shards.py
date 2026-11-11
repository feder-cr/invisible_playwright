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


def _jobs() -> dict:
    """{OS name: {shard: (step limit, job limit)}} as the workflow runs it."""
    entries = re.findall(
        r"- \{ os: (\S+), shard: (\d+), of: (\d+), limit: (\d+), job: (\d+) \}",
        _workflow())
    jobs: dict = {}
    counts: dict = {}
    for runner, shard, of, limit, job in entries:
        name = "Windows" if runner.startswith("windows") else "Linux"
        assert counts.setdefault(name, of) == of, "one OS, one shard count"
        jobs.setdefault(name, {})[int(shard)] = (int(limit), int(job))
    for name, per_shard in jobs.items():
        assert sorted(per_shard) == list(range(1, int(counts[name]) + 1)), name
    return jobs


def _plan() -> dict:
    """{OS name: shard count} as the workflow runs it."""
    return {name: len(per_shard) for name, per_shard in _jobs().items()}


def _universe(os_name: str) -> list:
    """Every file a job could meet: the baseline's, the weights', and one that
    nobody has timed (test_browsercontext_credentials is such a file today)."""
    return sorted(set(_baseline_files()) | set(WEIGHTS["seconds"][os_name])
                  | {"tests/sync/test_never_timed.py"})


def _seconds(os_name: str) -> dict:
    """What is dealt to the shards: files, and parts of the split files."""
    return base.planned_units(WEIGHTS, os_name, _universe(os_name))


def _file_of_unit(unit: str) -> str:
    return unit.split("#")[0]


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
    assert all(0 <= k < count for k in owner.values())
    # every file is there, once, or as all of its parts and nothing else
    assert sorted({_file_of_unit(u) for u in owner}) == files
    for rel, entry in WEIGHTS.get("splits", {}).items():
        parts = sorted(u for u in owner if _file_of_unit(u) == rel)
        assert parts == ["%s#%d/%d" % (rel, j, entry["parts"])
                         for j in range(1, entry["parts"] + 1)]
    whole = [u for u in owner if "#" not in u]
    assert len(whole) == len(set(whole))


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


def _lone(os_name: str) -> list:
    """Shard indexes (1-based) that hold ONE file heavier than the budget.

    A file that alone is over 8 minutes cannot be balanced; it gets its own
    shard and its own limit, and the balance rules apply to the others.
    """
    secs = _seconds(os_name)
    owner = base.assign(secs, _plan()[os_name])
    out = []
    for k in range(_plan()[os_name]):
        mine = [f for f, o in owner.items() if o == k]
        if len(mine) == 1 and secs[mine[0]] > 8 * 60:
            out.append(k + 1)
    return out


def _tests_per_shard(os_name: str) -> list:
    """How many recorded tests each planned shard runs (parts counted)."""
    count = _plan()[os_name]
    owner = base.assign(_seconds(os_name), count)
    counts = [0] * count
    parts = {rel: base.split_parts(WEIGHTS, os_name, rel)
             for rel in WEIGHTS.get("splits", {})}
    for test in BASELINE:
        module, _, name = test.partition("::")
        rel = base.file_of(module)
        key = base.test_key(module, name)
        if rel in parts:
            n, mapping, _ = parts[rel]
            part = mapping.get(key, zlib.crc32(key.encode()) % n)
            unit = "%s#%d/%d" % (rel, part + 1, n)
        else:
            unit = rel
        counts[owner[unit]] += 1
    return counts


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_the_planned_shards_are_balanced_and_fit_the_budget(os_name):
    loads = base.shard_loads(_seconds(os_name), _plan()[os_name])
    lone = _lone(os_name)
    rest = [x for k, x in enumerate(loads, 1) if k not in lone]
    assert min(rest) > 0
    assert max(rest) / min(rest) < 1.5, [round(x) for x in loads]
    # 8 minutes of tests at most for every shard that can be balanced
    assert max(rest) <= 8 * 60, [round(x) for x in loads]


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_each_step_limit_is_about_twice_the_predicted_time_of_its_shard(os_name):
    loads = base.shard_loads(_seconds(os_name), _plan()[os_name])
    lone = _lone(os_name)
    rest_max = max(x for k, x in enumerate(loads, 1) if k not in lone)
    for k, (limit, job) in _jobs()[os_name].items():
        own = loads[k - 1]
        assert limit * 60 >= 1.6 * own, (os_name, k, limit, own / 60)
        # a shard that can be balanced shares the limit of the heaviest of them
        ceiling = own if k in lone else rest_max
        assert limit * 60 <= 2.5 * ceiling, (os_name, k, limit, ceiling / 60)
        assert job >= limit + 5, (os_name, k, limit, job)


def test_a_unit_that_cannot_fit_a_shard_is_alone_in_one_and_none_is_left():
    for os_name in ("Linux", "Windows"):
        secs = _seconds(os_name)
        too_big = sorted(f for f, t in secs.items() if t > 8 * 60)
        owner = base.assign(secs, _plan()[os_name])
        for f in too_big:
            assert [g for g, k in owner.items() if k == owner[f]] == [f], f
        assert len(_lone(os_name)) == len(too_big)
        # the files that alone took more than a shard are cut in parts now
        assert not too_big, "split this file by test id: %s" % too_big


def test_the_plan_fits_in_sixteen_jobs_and_names_both_systems():
    plan = _plan()
    assert set(plan) == {"Linux", "Windows"}
    assert sum(plan.values()) <= 16


def test_the_old_hash_split_would_have_been_far_off_these_weights():
    # The reason the weights exist: 4 shards by crc32 of the path, planned with
    # the same numbers, are 12 / 26 / 6 / 2 minutes on Linux.
    secs = base.planned_seconds(WEIGHTS, "Linux", _universe("Linux"))
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
    # 30 s of suite - 25 s of testcases = 5 s outside, over 2 files = 2.5 s,
    # averaged with the 1.0 s it had
    assert data["overhead_per_file"]["Linux"] == 1.8
    assert json.loads(target.read_text(encoding="utf-8")) == data
    # a later run is remembered next to the latest
    again = base.write_weights([one], "888", "2026-12-02", path=target)
    assert again["runs"]["Linux"] == {"run_id": "888", "date": "2026-12-02",
                                      "also": ["777"]}


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


def _record(path: Path, *rows: tuple, torn: bool = False) -> str:
    """A record as upstream_record.py writes it: (nodeid, seconds) per report."""
    lines = [json.dumps({"id": i, "when": "call", "outcome": "passed",
                         "duration": d, "at": 1.0}) for i, d in rows]
    text = "\n".join(lines) + "\n"
    if torn:
        text += '{"id": "tests/sync/test_torn.py::t", "dura'   # killed mid-line
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_record_hook_appends_a_line_per_report_and_only_when_asked(
        tmp_path, monkeypatch):
    import upstream_record as rec

    class Report:
        nodeid = "tests/sync/test_a.py::test_x[firefox]"
        when = "call"
        outcome = "rerun"
        duration = 1.23456

    out = tmp_path / "r.jsonl"
    monkeypatch.delenv("INVPW_RECORD", raising=False)
    rec.pytest_runtest_logreport(Report())
    assert not out.exists()
    monkeypatch.setenv("INVPW_RECORD", str(out))
    rec.pytest_runtest_logreport(Report())
    rec.pytest_runtest_logreport(Report())
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == [Report.nodeid] * 2
    assert rows[0]["outcome"] == "rerun" and rows[0]["duration"] == 1.235


def test_a_record_gives_complete_files_and_a_lower_bound_for_the_last(tmp_path):
    rec = _record(tmp_path / "upstream-Linux-3.jsonl",
                  ("tests/sync/test_a.py::t1", 10.0),
                  ("tests/sync/test_a.py::t1", 10.0),
                  ("tests/sync/test_b.py::t2", 5.0),
                  ("tests/sync/test_c.py::t3", 50.0), torn=True)
    per_file, last = base.record_seconds(rec)
    assert per_file == {"tests/sync/test_a.py": 20.0, "tests/sync/test_b.py": 5.0,
                        "tests/sync/test_c.py": 50.0}
    assert last == "tests/sync/test_c.py"       # the torn line is ignored


def test_write_weights_reads_the_record_of_a_shard_that_left_no_junit(tmp_path):
    target = tmp_path / "w.json"
    target.write_text(json.dumps({
        "format": 1, "runs": {}, "overhead_per_file": {"Linux": 1.0},
        "seconds": {"Linux": {"tests/sync/test_a.py": 1.0,
                              "tests/sync/test_b.py": 1.0,
                              "tests/sync/test_c.py": 1.0,
                              "tests/sync/test_d.py": 7.0}},
        "estimated": {"Linux": ["tests/sync/test_a.py", "tests/sync/test_b.py",
                                "tests/sync/test_c.py"]},
    }), encoding="utf-8")
    rec = _record(tmp_path / "upstream-Linux-3.jsonl",
                  ("tests/sync/test_a.py::t1", 10.0),
                  ("tests/sync/test_b.py::t2", 5.0),
                  ("tests/sync/test_c.py::t3", 50.0),
                  ("tests/sync/test_d.py::t4", 99.0))
    # a file the junit of the same OS covers wins over the record
    junit = _timed_junit(tmp_path / "upstream-Linux-1.xml", 3.0,
                         ("tests.sync.test_a", "t1", 2.0))
    data = base.write_weights([junit, rec], "9", "2026-12-01", path=target)
    secs = data["seconds"]["Linux"]
    assert secs["tests/sync/test_a.py"] == 2.0       # the junit
    assert secs["tests/sync/test_b.py"] == 5.0       # complete in the record
    assert secs["tests/sync/test_c.py"] == 50.0      # complete: d came after it
    # d is the file the record ends on, but it already has a measured value,
    # which a cut-off file must not lower or replace
    assert secs["tests/sync/test_d.py"] == 7.0
    assert data["estimated"]["Linux"] == []
    assert data["lower_bound"]["Linux"] == []


def test_the_file_a_killed_shard_was_in_is_a_lower_bound_not_a_measurement(
        tmp_path):
    target = tmp_path / "w.json"
    target.write_text(json.dumps({
        "format": 1, "runs": {}, "overhead_per_file": {"Linux": 1.0},
        "seconds": {"Linux": {"tests/sync/test_c.py": 20.0}},
        "estimated": {"Linux": ["tests/sync/test_c.py"]},
    }), encoding="utf-8")
    rec = _record(tmp_path / "upstream-Linux-3.jsonl",
                  ("tests/sync/test_b.py::t2", 5.0),
                  ("tests/sync/test_c.py::t3", 50.0))
    data = base.write_weights([rec], "9", "2026-12-01", path=target)
    assert data["seconds"]["Linux"]["tests/sync/test_b.py"] == 5.0
    assert data["seconds"]["Linux"]["tests/sync/test_c.py"] == 50.0
    assert data["estimated"]["Linux"] == ["tests/sync/test_c.py"]
    assert data["lower_bound"]["Linux"] == ["tests/sync/test_c.py"]
    # a later junit that covers it replaces the bound
    junit = _timed_junit(tmp_path / "upstream-Linux-3.xml", 80.0,
                         ("tests.sync.test_c", "t3", 70.0))
    data = base.write_weights([junit], "10", "2026-12-02", path=target)
    assert data["seconds"]["Linux"]["tests/sync/test_c.py"] == 70.0
    assert data["estimated"]["Linux"] == [] and data["lower_bound"]["Linux"] == []


def test_a_file_one_system_never_timed_takes_the_other_systems_time_scaled(
        tmp_path):
    target = tmp_path / "w.json"
    lin = _timed_junit(tmp_path / "upstream-Linux-1.xml", 10.0,
                       ("tests.sync.test_a", "t", 10.0),
                       ("tests.sync.test_only_linux", "t", 4.0))
    win = _timed_junit(tmp_path / "upstream-Windows-1.xml", 25.0,
                       ("tests.sync.test_a", "t", 20.0),
                       ("tests.sync.test_only_windows", "t", 6.0))
    data = base.write_weights([lin, win], "1", "2026-12-01", path=target)
    # ratio Windows/Linux over the file both measured: 2.0
    assert data["seconds"]["Windows"]["tests/sync/test_only_linux.py"] == 8.0
    assert data["seconds"]["Linux"]["tests/sync/test_only_windows.py"] == 3.0
    assert data["estimated"]["Windows"] == ["tests/sync/test_only_linux.py"]
    assert data["scaled"]["Linux"] == ["tests/sync/test_only_windows.py"]
    # and a measurement on that system replaces the scaled figure
    win2 = _timed_junit(tmp_path / "upstream-Windows-2.xml", 12.0,
                        ("tests.sync.test_only_linux", "t", 9.0))
    data = base.write_weights([win2], "2", "2026-12-02", path=target)
    assert data["seconds"]["Windows"]["tests/sync/test_only_linux.py"] == 9.0
    assert data["estimated"]["Windows"] == []


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_what_the_weights_call_scaled_or_a_bound_is_listed_as_estimated(os_name):
    estimated = set(WEIGHTS["estimated"][os_name])
    assert set(WEIGHTS["scaled"][os_name]) <= estimated
    assert set(WEIGHTS["lower_bound"][os_name]) <= estimated


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_the_split_files_are_cut_by_the_seconds_of_their_tests(os_name):
    assert set(WEIGHTS["splits"]) == {
        "tests/sync/test_request_intercept.py",
        "tests/sync/test_browsercontext_request_intercept.py"}
    for rel, entry in WEIGHTS["splits"].items():
        count, mapping, load = base.split_parts(WEIGHTS, os_name, rel)
        assert count == entry["parts"] == 2
        assert sorted(mapping) == sorted(entry["seconds"][os_name])
        # the tests add up to the file, and the parts are of about one size
        assert sum(load) == pytest.approx(WEIGHTS["seconds"][os_name][rel],
                                          abs=1.0)
        assert max(load) / min(load) < 1.5, load   # a 2-minute test is not divisible
        # the same call gives the same parts
        assert base.split_parts(WEIGHTS, os_name, rel) == (count, mapping, load)


def test_a_shard_runs_exactly_the_tests_its_parts_own(tmp_path, monkeypatch):
    sync = tmp_path / "tests" / "sync"
    sync.mkdir(parents=True)
    for n in ("test_a.py", "test_big.py", "test_c.py"):
        (sync / n).write_text("", encoding="utf-8")
    monkeypatch.setattr(base, "SUITE", tmp_path)
    weights = {
        "overhead_per_file": {"Linux": 1.0},
        "seconds": {"Linux": {"tests/sync/test_a.py": 50.0,
                              "tests/sync/test_big.py": 120.0,
                              "tests/sync/test_c.py": 50.0}},
        "splits": {"tests/sync/test_big.py": {"parts": 2, "seconds": {"Linux": {
            "t1": 50.0, "t2": 50.0, "t3": 10.0, "t4": 10.0}}}},
    }
    seen = {}
    for k in (1, 2, 3):
        files, split = base.shard_plan("tests/sync", (k, 3), "Linux", weights)
        seen[k] = (files, split)
    big = [(k, f, s) for k, (f, s) in seen.items()
           if "tests/sync/test_big.py" in f]
    # the two parts of the big file are in two shards, and each runs only its
    # own tests: together all four, none twice
    assert len(big) == 2
    ran = []
    for k, _, s in big:
        entry = s["tests/sync/test_big.py"]
        ran += [t for t, part in entry["map"].items() if part in entry["keep"]]
    assert sorted(ran) == ["t1", "t2", "t3", "t4"]
    # a whole file is in one shard and has no split entry
    whole = [k for k, (f, s) in seen.items() if "tests/sync/test_a.py" in f]
    assert len(whole) == 1
    assert "tests/sync/test_a.py" not in seen[whole[0]][1]


def test_the_split_hook_keeps_one_part_and_places_unknown_tests_by_hash(
        monkeypatch):
    import upstream_split as split

    class Item:
        def __init__(self, nodeid):
            self.nodeid = nodeid

    class Hook:
        dropped = None

        def pytest_deselected(self, items):
            self.dropped = items

    class Config:
        hook = Hook()

    spec = {"tests/sync/test_big.py": {
        "parts": 2, "keep": [0], "map": {"t1": 0, "t2": 1, "Cls::t3": 0}}}
    names = ["tests/sync/test_big.py::t1", "tests/sync/test_big.py::t2",
             "tests/sync/test_big.py::Cls::t3", "tests/sync/test_big.py::new_t",
             "tests/sync/test_other.py::t9"]
    items = [Item(n) for n in names]
    monkeypatch.delenv("INVPW_SPLIT", raising=False)
    split.pytest_collection_modifyitems(Config(), items)
    assert [i.nodeid for i in items] == names          # off: nothing happens

    monkeypatch.setenv("INVPW_SPLIT", json.dumps(spec))
    items = [Item(n) for n in names]
    config = Config()
    split.pytest_collection_modifyitems(config, items)
    kept = {i.nodeid for i in items}
    dropped = {i.nodeid for i in config.hook.dropped}
    assert kept | dropped == set(names) and not kept & dropped
    assert "tests/sync/test_big.py::t1" in kept
    assert "tests/sync/test_big.py::t2" in dropped
    assert "tests/sync/test_big.py::Cls::t3" in kept
    assert "tests/sync/test_other.py::t9" in kept       # not a split file
    # the new test goes where its name hashes, and to the other part's shard
    # it goes to the other part: exactly one of the two keeps it
    other = dict(spec["tests/sync/test_big.py"], keep=[1])
    monkeypatch.setenv("INVPW_SPLIT", json.dumps(
        {"tests/sync/test_big.py": other}))
    second = [Item("tests/sync/test_big.py::new_t")]
    split.pytest_collection_modifyitems(Config(), second)
    assert ("tests/sync/test_big.py::new_t" in kept) != bool(second)


def test_the_split_option_sets_the_variable_the_hook_reads(tmp_path,
                                                           monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw["env"]))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(base, "SUITE", tmp_path)
    (tmp_path / "tests" / "sync").mkdir(parents=True)
    for n in ("test_a.py", "test_b.py"):
        (tmp_path / "tests" / "sync" / n).write_text("", encoding="utf-8")
    monkeypatch.setattr(base, "write_plugin", lambda: tmp_path / "p.py")
    monkeypatch.setattr(base.subprocess, "run", fake_run)
    weights = tmp_path / "w.json"
    weights.write_text(json.dumps({
        "overhead_per_file": {"Linux": 1.0, "Windows": 1.0},
        "seconds": {"Linux": {"tests/sync/test_a.py": 10.0,
                              "tests/sync/test_b.py": 100.0}},
        "splits": {"tests/sync/test_b.py": {"parts": 2, "seconds": {"Linux": {
            "t1": 50.0, "t2": 50.0}}}}}), encoding="utf-8")
    monkeypatch.setattr(base, "WEIGHTS", weights)
    for k in (1, 2):
        monkeypatch.setattr(sys, "argv", ["upstream_baseline.py", "ff", "--shard",
                                          "%d/2" % k, "--weights-os", "Linux"])
        assert base.main() == 0
        argv, env = seen[-1]
        assert "scripts.upstream_split" in argv
        if "tests/sync/test_b.py" in argv:
            spec = json.loads(env["INVPW_SPLIT"])
            assert spec["tests/sync/test_b.py"]["parts"] == 2
        else:
            assert "INVPW_SPLIT" not in env


def test_the_record_option_sets_the_variable_the_hook_reads(tmp_path,
                                                            monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw["env"]))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(base, "SUITE", tmp_path)
    (tmp_path / "tests" / "sync").mkdir(parents=True)
    (tmp_path / "tests" / "sync" / "test_a.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(base, "write_plugin", lambda: tmp_path / "p.py")
    monkeypatch.setattr(base.subprocess, "run", fake_run)
    out = tmp_path / "shard.jsonl"
    out.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["upstream_baseline.py", "ff", "--record",
                                      str(out)])
    assert base.main() == 0
    argv, env = seen[-1]
    assert "scripts.upstream_record" in argv
    assert env["INVPW_RECORD"] == str(out.resolve())
    assert out.read_text(encoding="utf-8") == ""     # a stale record is cleared


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


def test_the_parts_of_a_split_file_add_up_and_refresh_its_test_table(tmp_path):
    target = tmp_path / "w.json"
    target.write_text(json.dumps({
        "format": 1, "runs": {}, "overhead_per_file": {"Linux": 1.0},
        "seconds": {"Linux": {"tests/sync/test_big.py": 1.0}},
        "estimated": {"Linux": ["tests/sync/test_big.py"]},
        "splits": {"tests/sync/test_big.py": {"parts": 2, "seconds": {
            "Linux": {"t1": 1.0, "t2": 1.0, "t3": 1.0}}}},
    }), encoding="utf-8")
    # the two parts ran in two shards, with a test in a class and a rerun
    one = _timed_junit(tmp_path / "upstream-Linux-1.xml", 40.0,
                       ("tests.sync.test_big", "t1", 10.0),
                       ("tests.sync.test_big", "t1", 10.0))
    two = _timed_junit(tmp_path / "upstream-Linux-2.xml", 30.0,
                       ("tests.sync.test_big.TestX", "t2", 20.0))
    data = base.write_weights([one, two], "5", "2026-12-01", path=target)
    assert data["seconds"]["Linux"]["tests/sync/test_big.py"] == 40.0
    assert data["splits"]["tests/sync/test_big.py"]["seconds"]["Linux"] == {
        "t1": 20.0, "t2": 1.0, "TestX::t2": 20.0, "t3": 1.0}
    assert data["estimated"]["Linux"] == []
    assert base.test_key("tests.sync.test_big.TestX", "t2") == "TestX::t2"
    assert base.test_key("tests.sync.test_big", "t1") == "t1"


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
    for os_name, count in _plan().items():
        per_shard = _tests_per_shard(os_name)
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
    assert "timeout-minutes: ${{ matrix.job }}" in text
    # every job leaves its record next to its junit, and uploads both
    assert '--record "$record"' in text
    assert 'record="upstream-${RUNNER_OS}-${{ matrix.shard }}.jsonl"' in text
    assert "upstream-${{ runner.os }}-${{ matrix.shard }}.jsonl" in text
    assert "upstream-${{ runner.os }}-${{ matrix.shard }}.xml" in text
    # the verdict collects exactly the reports those jobs write
    verdict = re.search(r"for plan in ((?:\w+:\d+ ?)+); do", text)
    assert verdict, "the verdict must loop over the OS:count plan"
    expected = dict(plan)
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
