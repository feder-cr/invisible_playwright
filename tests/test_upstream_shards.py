"""The upstream suite is split over CI jobs by file, and judged from the pieces.

`scripts/upstream_baseline.py --shard K/N` decides which test files a job runs,
and `scripts/upstream_regressions.py` merges the jobs' reports. Neither needs a
browser, and each property is checked on the input it exists for: a file that
two jobs both run or that none runs, a shard that vanished and left the rest
looking green, a report that is missing.
"""
from __future__ import annotations

import json
import re
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


def _baseline_files() -> list:
    """The suite-relative file of every recorded test id."""
    mods = {k.split("::")[0] for k in BASELINE}
    return sorted(m.replace(".", "/") + ".py" for m in mods)


@pytest.mark.parametrize("text, expected", [("1/4", (1, 4)), (" 4/4 ", (4, 4)),
                                            ("1/1", (1, 1))])
def test_a_shard_is_written_k_over_n(text, expected):
    assert base.parse_shard(text) == expected


@pytest.mark.parametrize("text", ["0/4", "5/4", "2", "a/b", "2/0", "-1/4", ""])
def test_a_malformed_or_out_of_range_shard_is_refused(text):
    with pytest.raises(ValueError):
        base.parse_shard(text)


def test_the_hash_is_the_crc32_of_the_posix_path():
    # Pinned to the algorithm, not just to determinism: `hash()` is also
    # deterministic inside one process and differs between two, which is the
    # failure that matters here.
    rel = "tests/sync/test_page.py"
    assert base.shard_of(rel, 4) == zlib.crc32(rel.encode()) % 4
    assert base.shard_of(rel, 4) == 2  # frozen value: a new hash moves files


def test_a_windows_path_lands_where_the_posix_one_does():
    assert (base.shard_of("tests\\sync\\test_page.py", 4)
            == base.shard_of("tests/sync/test_page.py", 4))


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_every_recorded_file_belongs_to_exactly_one_shard(count):
    files = _baseline_files()
    assert len(files) > 50
    owners = {f: [k for k in range(1, count + 1)
                  if base.in_shard(f, (k, count))] for f in files}
    bad = {f: o for f, o in owners.items() if len(o) != 1}
    assert not bad, "a file must run in one shard, no more and no fewer: %s" % bad


def test_with_four_shards_none_is_empty():
    files = _baseline_files()
    sizes = [sum(base.in_shard(f, (k, 4)) for f in files) for k in range(1, 5)]
    assert min(sizes) > 0, sizes


def test_shard_files_selects_from_the_suite_on_disk(tmp_path, monkeypatch):
    sync = tmp_path / "tests" / "sync"
    sync.mkdir(parents=True)
    names = ["test_%s.py" % c for c in "abcdefghij"]
    for n in names:
        (sync / n).write_text("", encoding="utf-8")
    (sync / "conftest.py").write_text("", encoding="utf-8")
    (sync / "helper.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(base, "SUITE", tmp_path)

    picked = [base.shard_files("tests/sync", (k, 3)) for k in (1, 2, 3)]
    flat = [f for part in picked for f in part]
    assert sorted(flat) == sorted("tests/sync/" + n for n in names)
    assert len(flat) == len(set(flat))
    # the same call gives the same answer: a job re-run must own the same files
    assert picked == [base.shard_files("tests/sync", (k, 3)) for k in (1, 2, 3)]


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


def test_the_floor_of_the_real_baseline_loses_a_shard_but_not_a_rename():
    n = len(BASELINE)
    floor = reg.floor_of(n, 0.95)
    files = _baseline_files()
    per_shard = [sum(1 for k in BASELINE
                     if base.in_shard(k.split("::")[0].replace(".", "/") + ".py",
                                      (s, 4)))
                 for s in range(1, 5)]
    assert sum(per_shard) == n and len(files) > 50
    # losing any whole shard drops under the floor ...
    assert all(n - lost < floor for lost in per_shard), (n, floor, per_shard)
    # ... and a handful of renamed tests does not.
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

def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_shard_list_matches_the_count_it_passes_and_expects():
    text = _workflow()
    listed = re.search(r"^\s+shard: \[([\d, ]+)\]", text, re.MULTILINE)
    assert listed, "the matrix must list its shards"
    shards = [int(x) for x in listed.group(1).split(",")]
    n = len(shards)
    assert shards == list(range(1, n + 1))
    # every `K/N` the jobs pass and every report the verdict collects use that N
    assert set(re.findall(r'--shard "\$\{\{ matrix\.shard \}\}/(\d+)"', text)) == {str(n)}
    assert "matrix.shard }}/%d" % n in text
    assert "for k in %s; do" % " ".join(str(s) for s in shards) in text


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
