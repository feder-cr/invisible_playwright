"""scripts/ci_verdict.py is the one judge of the mandatory suites: it must fail on every hole (a skip, a failure, too few
tests collected, a required file that never ran, a list that lost a row or a minimum against main) and only then. The
junit files here are written by hand in tmp_path; the last tests read the real ci/required-suites.json."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ROOT / "ci" / "required-suites.json"


def _load():
    spec = importlib.util.spec_from_file_location("ci_verdict_under_test", ROOT / "scripts" / "ci_verdict.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verdict = _load()

FILE_A = "tests/test_a.py"
FILE_B = "tests/test_b.py"
NODE = "tests/test_c.py::test_one"


def _suite(**over):
    row = {"id": "s.one", "giro": "G2", "os": ["linux", "windows"], "min_tests": 4,
           "tests": [FILE_A, FILE_B, NODE], "marker": "unit", "needs_root": False, "exact": True,
           "note": ""}
    row.update(over)
    return row


def _write_required(path, rows):
    path.write_text("[\n" + ",\n".join("  " + json.dumps(r) for r in rows) + "\n]\n", encoding="utf-8")
    return path


def _case(file, name, tag=None, classname=None):
    classname = file[:-3].replace("/", ".") if classname is None else classname
    inner = {None: "", "skipped": '<skipped message="s"/>', "failure": '<failure message="f">x</failure>',
             "error": '<error message="e">x</error>'}[tag]
    return '<testcase classname="%s" name="%s" time="0.1">%s</testcase>' % (classname, name, inner)


def _junit(path, cases):
    path.write_text('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="%d">%s'
                    "</testsuite></testsuites>" % (len(cases), "".join(cases)), encoding="utf-8")
    return path


GREEN = [_case(FILE_A, "test_x"), _case(FILE_A, "test_y[p1]"), _case(FILE_B, "test_z"), _case("tests/test_c.py", "test_one")]


SHA = "a" * 40


def _judge(tmp_path, cases, rows=None, extra=(), suite="s.one", os_name="linux", with_binary=True, commit=SHA):
    required = _write_required(tmp_path / "required.json", rows or [_suite()])
    junit = _junit(tmp_path / "junit.xml", cases) if cases is not None else tmp_path / "absent.xml"
    binary = tmp_path / "firefox.bin"
    binary.write_bytes(b"engine")
    out = tmp_path / "result" / ("%s.%s.json" % (suite, os_name))
    argv = ["judge", "--required", str(required), "--suite", suite, "--os", os_name, "--junit", str(junit),
            "--out", str(out), *extra]
    if with_binary:
        argv += ["--binary", str(binary)]
    if commit:
        argv += ["--commit", commit]
    code = verdict.main(argv)
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_everything_green_exits_0_and_writes_the_result(tmp_path, capsys):
    code, result = _judge(tmp_path, GREEN)
    assert code == 0, capsys.readouterr().out
    assert result["suite"] == "s.one" and result["os"] == "linux" and result["commit"] == SHA
    assert result["verdict"] == "green"
    assert (result["collected"], result["passed"], result["failed"], result["skipped"], result["errors"]) == (4, 4, 0, 0, 0)
    assert result["missing_tests"] == [] and result["problems"] == []


def test_the_binary_hash_is_the_sha256_of_the_file(tmp_path):
    import hashlib
    _code, result = _judge(tmp_path, GREEN)
    assert result["binary_sha256"] == hashlib.sha256(b"engine").hexdigest()


def test_judge_without_binary_is_a_wrong_use(tmp_path, capsys):
    code, result = _judge(tmp_path, GREEN, with_binary=False)
    assert code == 2 and result is None
    assert "--binary" in capsys.readouterr().err


def test_a_binary_that_does_not_exist_is_red_never_a_null_hash_green(tmp_path, capsys):
    required = _write_required(tmp_path / "required.json", [_suite()])
    junit = _junit(tmp_path / "junit.xml", GREEN)
    out = tmp_path / "r.json"
    code = verdict.main(["judge", "--required", str(required), "--suite", "s.one", "--os", "linux", "--junit", str(junit),
                         "--binary", str(tmp_path / "nowhere.bin"), "--commit", SHA, "--out", str(out)])
    result = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and result["binary_sha256"] is None and result["verdict"] == "red"
    assert "does not exist" in capsys.readouterr().out


def test_a_skipped_test_is_red(tmp_path, capsys):
    cases = GREEN[:-1] + [_case("tests/test_c.py", "test_one", "skipped")]
    code, result = _judge(tmp_path, cases)
    out = capsys.readouterr().out
    assert code == 1 and result["skipped"] == 1
    assert "skipped" in out and NODE in out


def test_a_failed_test_is_red(tmp_path, capsys):
    cases = GREEN + [_case(FILE_B, "test_bad", "failure")]
    code, result = _judge(tmp_path, cases)
    out = capsys.readouterr().out
    assert code == 1 and result["failed"] == 1
    assert "failed" in out and FILE_B in out


def test_an_error_is_red(tmp_path):
    cases = GREEN + [_case(FILE_B, "test_bad", "error")]
    code, result = _judge(tmp_path, cases)
    assert code == 1 and result["errors"] == 1


def test_fewer_tests_than_the_minimum_is_red(tmp_path, capsys):
    code, result = _judge(tmp_path, GREEN, rows=[_suite(min_tests=5)])
    assert code == 1 and result["collected"] == 4
    assert "fewer than the minimum 5" in capsys.readouterr().out


def test_a_required_file_that_did_not_run_is_red(tmp_path, capsys):
    cases = [c for c in GREEN if "test_b" not in c]
    code, result = _judge(tmp_path, cases, rows=[_suite(min_tests=3)])
    assert code == 1 and result["missing_tests"] == [FILE_B]
    assert FILE_B in capsys.readouterr().out


def test_a_required_node_id_that_did_not_run_is_red(tmp_path):
    cases = [c for c in GREEN if "test_one" not in c]
    code, result = _judge(tmp_path, cases, rows=[_suite(min_tests=3)])
    assert code == 1 and result["missing_tests"] == [NODE]


def test_a_parametrized_node_id_is_matched_without_its_parameters(tmp_path):
    cases = [_case(FILE_A, "test_x"), _case("tests/test_c.py", "test_one[a]"), _case("tests/test_c.py", "test_one[b]")]
    rows = [_suite(tests=[FILE_A, NODE], min_tests=3)]
    assert _judge(tmp_path, cases, rows=rows)[0] == 0
    cases[2] = _case("tests/test_c.py", "test_one[b]", "failure")
    assert _judge(tmp_path, cases, rows=rows)[0] == 1


def test_a_missing_junit_is_red(tmp_path, capsys):
    code, result = _judge(tmp_path, None)
    assert code == 1 and result["collected"] == 0
    assert "junit" in capsys.readouterr().out


def test_every_problem_is_printed_one_per_line(tmp_path, capsys):
    cases = [_case(FILE_A, "test_x", "failure"), _case(FILE_A, "test_y", "skipped")]
    code, _result = _judge(tmp_path, cases)
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("VERDICT RED")]
    assert code == 1 and len(lines) >= 5, lines


def test_a_row_the_suite_does_not_have_for_that_os_is_red(tmp_path, capsys):
    code, _result = _judge(tmp_path, GREEN, rows=[_suite(os=["linux"])], os_name="windows")
    assert code == 1 and "does not run on windows" in capsys.readouterr().out


def test_a_class_in_the_junit_is_part_of_the_node_id():
    assert verdict.node_id("tests.test_x.TestThing", "test_y") == "tests/test_x.py::TestThing::test_y"
    assert verdict.node_id("tests.test_x", "test_y[p]") == "tests/test_x.py::test_y[p]"


# --- against main ------------------------------------------------------------------------------------------------
def _main_file(tmp_path, rows):
    return _write_required(tmp_path / "main.json", rows)


def test_a_list_that_only_grew_is_green(tmp_path):
    main = _main_file(tmp_path, [_suite(min_tests=3, tests=[FILE_A])])
    code, _r = _judge(tmp_path, GREEN, rows=[_suite(), _suite(id="s.two")], extra=["--main-required", str(main)])
    assert code == 0


def test_a_suite_of_main_that_is_gone_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite(), _suite(id="s.gone")])
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", str(main)])
    assert code == 1 and "s.gone" in capsys.readouterr().out


def test_a_lost_test_row_against_main_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite(tests=[FILE_A, FILE_B, NODE, "tests/test_d.py"])])
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", str(main)])
    assert code == 1 and "tests/test_d.py" in capsys.readouterr().out


def test_a_higher_minimum_on_main_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite(min_tests=9)])
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", str(main)])
    assert code == 1 and "went down" in capsys.readouterr().out


def test_a_main_list_that_does_not_exist_is_red_unless_the_gate_declares_main_has_none(tmp_path, capsys):
    missing = str(tmp_path / "no" / "required.json")
    code, result = _judge(tmp_path, GREEN, extra=["--main-required", missing])
    assert code == 1 and result["verdict"] == "red"
    assert "it is not known what main has" in capsys.readouterr().out


def test_a_main_list_declared_absent_is_said_and_passed(tmp_path, capsys):
    missing = str(tmp_path / "no" / "required.json")
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", missing, "--main-has-no-list"])
    assert code == 0 and "main was checked out and has none" in capsys.readouterr().out


def test_declaring_main_has_none_while_the_file_is_there_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite()])
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", str(main), "--main-has-no-list"])
    assert code == 1 and "says main has none" in capsys.readouterr().out


def test_main_has_no_list_without_a_main_path_is_red(tmp_path):
    assert _judge(tmp_path, GREEN, extra=["--main-has-no-list"])[0] == 1


def test_an_exact_turned_off_against_main_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite(exact=True)])
    code, _r = _judge(tmp_path, GREEN, rows=[_suite(exact=False)], extra=["--main-required", str(main)])
    assert code == 1 and "no longer exact" in capsys.readouterr().out


def test_a_reduced_os_against_main_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite()])
    code, _r = _judge(tmp_path, GREEN, rows=[_suite(os=["linux"])], extra=["--main-required", str(main)])
    assert code == 1 and "no longer runs on windows" in capsys.readouterr().out


def test_a_changed_marker_against_main_is_red(tmp_path, capsys):
    main = _main_file(tmp_path, [_suite(marker="e2e")])
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", str(main)])
    assert code == 1 and "marker of s.one changed" in capsys.readouterr().out


def test_a_main_list_older_than_the_format_is_still_compared(tmp_path):
    old = {k: v for k, v in _suite(min_tests=3, tests=[FILE_A]).items() if k != "exact"}
    main = _main_file(tmp_path, [old])
    assert _judge(tmp_path, GREEN, extra=["--main-required", str(main)])[0] == 0


# --- args --------------------------------------------------------------------------------------------------------
def test_args_prints_the_files_and_node_ids_one_per_line(tmp_path, capsys):
    required = _write_required(tmp_path / "required.json", [_suite()])
    assert verdict.main(["args", "--required", str(required), "--suite", "s.one", "--os", "linux"]) == 0
    assert capsys.readouterr().out.splitlines() == [FILE_A, FILE_B, NODE]


def test_args_with_the_marker_puts_it_first(tmp_path, capsys):
    required = _write_required(tmp_path / "required.json", [_suite(marker="e2e")])
    assert verdict.main(["args", "--required", str(required), "--suite", "s.one", "--os", "windows", "--with-marker"]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["-m", "e2e"]


def test_args_for_windows_of_a_linux_only_suite_is_an_error(tmp_path, capsys):
    required = _write_required(tmp_path / "required.json", [_suite(os=["linux"])])
    assert verdict.main(["args", "--required", str(required), "--suite", "s.one", "--os", "windows"]) != 0
    captured = capsys.readouterr()
    assert captured.out == "" and "does not run on windows" in captured.err


# --- the commit: the wrapper's, never GITHUB_SHA -------------------------------------------------------------------
def test_the_commit_is_not_taken_from_github_sha(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)  # in a called workflow this is the commit of the ENGINE's repository
    code, result = _judge(tmp_path, GREEN, commit="b" * 40)
    assert code == 0 and result["commit"] == "b" * 40
    monkeypatch.chdir(tmp_path)  # not a git checkout
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    code, result = _judge(tmp_path, GREEN, commit=None)
    assert code == 1 and result["commit"] is None
    assert "commit of the wrapper is not known" in capsys.readouterr().out


def test_without_commit_it_is_git_head_of_the_current_directory(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "wrapper"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)
    monkeypatch.chdir(repo)
    code, result = _judge(tmp_path, GREEN, commit=None)
    assert code == 0 and result["commit"] == head


# --- the seal of the gate ------------------------------------------------------------------------------------------
def test_a_readable_seal_is_noted_as_having_no_binary_hash(tmp_path, capsys):
    seal = tmp_path / "gate.seal.json"
    seal.write_text('{"schema": 2, "tag": "t", "assets": {}, "build_id": "1"}', encoding="utf-8")
    code, result = _judge(tmp_path, GREEN, extra=["--seal", str(seal)])
    assert code == 0 and result["seal_binary_hash"] == "absent"
    assert "carries no sha256 of the engine binary" in capsys.readouterr().out


def test_a_seal_that_cannot_be_read_is_red(tmp_path, capsys):
    code, _r = _judge(tmp_path, GREEN, extra=["--seal", str(tmp_path / "no.seal.json")])
    assert code == 1 and "cannot be read" in capsys.readouterr().out


# --- exact, every test of a file, xunit2 ---------------------------------------------------------------------------
def test_an_exact_suite_with_more_tests_than_min_tests_is_red(tmp_path, capsys):
    cases = GREEN + [_case(FILE_B, "test_new")]
    code, result = _judge(tmp_path, cases)
    assert code == 1 and result["collected"] == 5
    assert "exact" in capsys.readouterr().out


def test_a_suite_that_is_not_exact_allows_more_tests(tmp_path):
    cases = GREEN + [_case(FILE_B, "test_new")]
    assert _judge(tmp_path, cases, rows=[_suite(exact=False)])[0] == 0


def test_a_deselected_test_is_caught_by_the_exact_count(tmp_path, capsys):
    cases = [c for c in GREEN if "test_y" not in c]  # test_y[p1] deselected: not in the junit at all
    code, _r = _judge(tmp_path, cases)
    assert code == 1 and "fewer than the minimum 4" in capsys.readouterr().out


def test_a_file_with_one_test_that_did_not_pass_is_red_even_if_another_passed(tmp_path, capsys):
    cases = [_case(FILE_A, "test_x"), _case(FILE_A, "test_y[p1]", "skipped"), _case(FILE_B, "test_z"),
             _case("tests/test_c.py", "test_one")]
    code, result = _judge(tmp_path, cases)
    assert code == 1 and result["missing_tests"] == [FILE_A]
    assert "the file %s has tests that did not pass (skipped)" % FILE_A in capsys.readouterr().out


def test_a_junit_written_as_xunit1_is_red_even_if_it_keeps_the_classname(tmp_path, capsys):
    xunit1 = _case(FILE_A, "test_x").replace(' time=', ' file="tests\test_a.py" line="3" time=')
    code, _r = _judge(tmp_path, [xunit1, *GREEN[1:]])
    assert code == 1 and "not xunit2" in capsys.readouterr().out


def test_a_junit_without_classname_is_red(tmp_path, capsys):
    cases = [_case(FILE_A, "test_x", classname=""), *GREEN[1:]]  # what junit_family=xunit1 leaves
    code, _r = _judge(tmp_path, cases)
    out = capsys.readouterr().out
    assert code == 1 and "not xunit2" in out and "junit_family=xunit2" in out


# --- the list is validated -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad, why", [
    (dict(os=[]), "os must be"), (dict(os=["macos"]), "os must be"), (dict(os="linux"), "os must be"),
    (dict(os=["linux", "linux"]), "os must be"),
    (dict(tests=[]), "tests must be"), (dict(tests=[1]), "tests must be"), (dict(tests=[FILE_A, FILE_A]), "duplicates"),
    (dict(min_tests=0), "min_tests"), (dict(min_tests="4"), "min_tests"), (dict(min_tests=True), "min_tests"),
    (dict(exact="yes"), "exact"), (dict(marker=""), "marker"), (dict(id=""), "no id"),
    (dict(needs_root="linux"), "needs_root"), (dict(needs_root=["macos"]), "needs_root"),
    (dict(os=["linux"], needs_root=["windows"]), "needs_root"),
])
def test_a_malformed_row_is_exit_2_with_the_reason_in_all_three_commands(tmp_path, capsys, bad, why):
    required = _write_required(tmp_path / "required.json", [_suite(**bad)])
    assert verdict.main(["args", "--required", str(required), "--suite", "s.one", "--os", "linux"]) == 2
    assert why in capsys.readouterr().err
    code, result = _judge(tmp_path, GREEN, rows=[_suite(**bad)])
    assert code == 2 and result["verdict"] == "red" and why in capsys.readouterr().out
    assert verdict.main(["complete", "--required", str(required), "--results", str(tmp_path)]) == 2
    assert why in capsys.readouterr().err


def test_duplicate_ids_and_a_row_that_is_not_an_object_are_exit_2(tmp_path, capsys):
    required = _write_required(tmp_path / "required.json", [_suite(), _suite()])
    assert verdict.main(["args", "--required", str(required), "--suite", "s.one", "--os", "linux"]) == 2
    assert "more than once" in capsys.readouterr().err
    required.write_text('[1, "x"]', encoding="utf-8")
    assert verdict.main(["args", "--required", str(required), "--suite", "s.one", "--os", "linux"]) == 2
    assert "is not an object" in capsys.readouterr().err


# --- complete ------------------------------------------------------------------------------------------------------
def _results(tmp_path, rows):
    """Run judge for every (suite, os) of rows into tmp_path/results/<artifact>/, as the gate downloads them."""
    required = _write_required(tmp_path / "required.json", rows)
    for row in rows:
        for os_name in row["os"]:
            binary = tmp_path / ("engine-%s.bin" % os_name)
            binary.write_bytes(("engine-" + os_name).encode())
            junit = _junit(tmp_path / ("j-%s-%s.xml" % (row["id"], os_name)), GREEN)
            out = tmp_path / "results" / ("result-%s-%s" % (row["id"], os_name)) / ("%s.%s.json" % (row["id"], os_name))
            code = verdict.main(["judge", "--required", str(required), "--suite", row["id"], "--os", os_name,
                                 "--junit", str(junit), "--binary", str(binary), "--commit", SHA, "--out", str(out)])
            assert code == 0
    return required, tmp_path / "results"


def _complete(required, results):
    return verdict.main(["complete", "--required", str(required), "--results", str(results)])


def _edit(results, name, **fields):
    path = next(results.rglob(name))
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data), encoding="utf-8")


ROWS = [_suite(), _suite(id="s.two", os=["linux"])]


def test_complete_is_green_when_every_leg_reported_green_at_one_commit(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    capsys.readouterr()
    assert _complete(required, results) == 0
    assert "COMPLETE GREEN: 3 result(s)" in capsys.readouterr().out


def test_complete_fails_on_a_leg_that_never_reported(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    next(results.rglob("s.one.windows.json")).unlink()
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "s.one/windows has no result" in capsys.readouterr().out


def test_complete_fails_on_a_missing_results_folder(tmp_path, capsys):
    required = _write_required(tmp_path / "required.json", ROWS)
    assert _complete(required, tmp_path / "nothing") == 1
    out = capsys.readouterr().out
    assert "does not exist" in out and "s.two/linux has no result" in out


def test_complete_fails_on_a_red_result(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    _edit(results, "s.two.linux.json", verdict="red", problems=["1 test(s) failed"])
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "s.two/linux is not green" in capsys.readouterr().out


def test_complete_fails_on_a_result_with_no_verdict(tmp_path):
    required, results = _results(tmp_path, ROWS)
    _edit(results, "s.two.linux.json", verdict=None)
    assert _complete(required, results) == 1


def test_complete_fails_on_a_result_of_a_suite_that_is_not_in_the_list(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    (results / "result-s.ghost-linux").mkdir()
    (results / "result-s.ghost-linux" / "g.json").write_text(
        json.dumps({"suite": "s.ghost", "os": "linux", "verdict": "green"}), encoding="utf-8")
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "s.ghost/linux, which is not in the required list" in capsys.readouterr().out


def test_complete_fails_on_a_result_of_an_os_the_suite_does_not_run_on(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    (results / "result-s.two-windows").mkdir()
    (results / "result-s.two-windows" / "w.json").write_text(
        json.dumps({"suite": "s.two", "os": "windows", "verdict": "green"}), encoding="utf-8")
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "s.two/windows, which is not in the required list" in capsys.readouterr().out


def test_complete_fails_on_the_same_leg_reported_twice(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    src = next(results.rglob("s.two.linux.json"))
    (results / "again").mkdir()
    (results / "again" / "copy.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "s.two/linux has two results" in capsys.readouterr().out


def test_complete_fails_on_legs_at_different_commits(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    _edit(results, "s.one.windows.json", commit="c" * 40)
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "different commits" in capsys.readouterr().out


def test_complete_fails_when_two_suites_of_one_os_ran_with_different_binaries(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    _edit(results, "s.two.linux.json", binary_sha256="f" * 64)
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "linux legs ran with different binaries" in capsys.readouterr().out


def test_complete_fails_on_a_result_with_no_binary_hash(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    _edit(results, "s.two.linux.json", binary_sha256=None)
    capsys.readouterr()
    assert _complete(required, results) == 1
    assert "no binary sha256" in capsys.readouterr().out


def test_complete_allows_different_binaries_for_different_os(tmp_path):
    required, results = _results(tmp_path, ROWS)  # engine-linux.bin and engine-windows.bin differ
    assert _complete(required, results) == 0


def test_complete_fails_on_an_unreadable_json_and_on_a_json_that_is_not_a_result(tmp_path, capsys):
    required, results = _results(tmp_path, ROWS)
    (results / "junk.json").write_text("{not json", encoding="utf-8")
    (results / "other.json").write_text("[1]", encoding="utf-8")
    capsys.readouterr()
    assert _complete(required, results) == 1
    out = capsys.readouterr().out
    assert "junk.json cannot be read" in out and "other.json is not a result" in out


# --- the real list-----------------------------------------------------------------------------------------------
def _real():
    return json.loads(REQUIRED.read_text(encoding="utf-8"))


def test_the_real_list_is_well_formed_for_the_judge_and_every_row_is_exact():
    rows = _real()
    assert verdict.validate_list(rows) == []
    assert [r["id"] for r in rows if r["exact"] is not True] == []


def test_the_real_list_has_unique_ids_and_sane_rows():
    rows = _real()
    ids = [r["id"] for r in rows]
    assert rows and len(ids) == len(set(ids)), ids
    for r in rows:
        assert set(r["os"]) and set(r["os"]) <= {"linux", "windows"}, r["id"]
        assert isinstance(r["min_tests"], int) and r["min_tests"] > 0, r["id"]
        assert r["tests"] and len(r["tests"]) == len(set(r["tests"])), r["id"]
        assert r["giro"] in {"G1", "G2"}, r["id"]
        # needs_root is a bool (every OS of the row) or the list of the OSes that need root
        root = r["needs_root"]
        assert isinstance(root, bool) or (isinstance(root, list) and set(root) <= set(r["os"])), r["id"]


def test_every_required_file_exists_and_every_node_id_is_defined_in_it():
    for r in _real():
        for entry in r["tests"]:
            path, _sep, name = entry.partition("::")
            file = ROOT / path
            assert file.is_file(), "%s: %s does not exist" % (r["id"], path)
            if name:
                text = file.read_text(encoding="utf-8")
                base = name.split("[", 1)[0]
                assert re.search(r"^\s*(async\s+)?def %s\(" % re.escape(base), text, re.M), \
                    "%s: %s has no def %s" % (r["id"], path, base)


def test_a_required_suite_runs_with_the_marker_it_names():
    for r in _real():
        assert r["marker"] in {"unit", "e2e"}, r["id"]
        if r["marker"] == "unit":
            for entry in r["tests"]:
                assert "pytestmark = pytest.mark.unit" in (ROOT / entry.split("::")[0]).read_text(encoding="utf-8") \
                    or "@pytest.mark.unit" in (ROOT / entry.split("::")[0]).read_text(encoding="utf-8"), entry


def test_a_suite_that_needs_root_only_on_linux_still_runs_on_windows():
    doh = [r for r in _real() if r["id"] == "wrapper.e2e.doh"][0]
    assert doh["os"] == ["linux", "windows"] and doh["needs_root"] == ["linux"]
