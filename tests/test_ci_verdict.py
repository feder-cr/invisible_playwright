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
           "tests": [FILE_A, FILE_B, NODE], "marker": "unit", "needs_root": False, "note": ""}
    row.update(over)
    return row


def _write_required(path, rows):
    path.write_text("[\n" + ",\n".join("  " + json.dumps(r) for r in rows) + "\n]\n", encoding="utf-8")
    return path


def _case(file, name, tag=None):
    classname = file[:-3].replace("/", ".")
    inner = {None: "", "skipped": '<skipped message="s"/>', "failure": '<failure message="f">x</failure>',
             "error": '<error message="e">x</error>'}[tag]
    return '<testcase classname="%s" name="%s" time="0.1">%s</testcase>' % (classname, name, inner)


def _junit(path, cases):
    path.write_text('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="%d">%s'
                    "</testsuite></testsuites>" % (len(cases), "".join(cases)), encoding="utf-8")
    return path


GREEN = [_case(FILE_A, "test_x"), _case(FILE_A, "test_y[p1]"), _case(FILE_B, "test_z"), _case("tests/test_c.py", "test_one")]


def _judge(tmp_path, cases, rows=None, extra=(), suite="s.one", os_name="linux", with_binary=True):
    required = _write_required(tmp_path / "required.json", rows or [_suite()])
    junit = _junit(tmp_path / "junit.xml", cases) if cases is not None else tmp_path / "absent.xml"
    binary = tmp_path / "firefox.bin"
    binary.write_bytes(b"engine")
    out = tmp_path / "result" / ("%s.%s.json" % (suite, os_name))
    argv = ["judge", "--required", str(required), "--suite", suite, "--os", os_name, "--junit", str(junit),
            "--out", str(out), *extra]
    if with_binary:
        argv += ["--binary", str(binary)]
    code = verdict.main(argv)
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_everything_green_exits_0_and_writes_the_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    code, result = _judge(tmp_path, GREEN)
    assert code == 0, capsys.readouterr().out
    assert result["suite"] == "s.one" and result["os"] == "linux" and result["commit"] == "a" * 40
    assert (result["collected"], result["passed"], result["failed"], result["skipped"], result["errors"]) == (4, 4, 0, 0, 0)
    assert result["missing_tests"] == [] and result["problems"] == []


def test_the_binary_hash_is_the_sha256_of_the_file(tmp_path):
    import hashlib
    _code, result = _judge(tmp_path, GREEN)
    assert result["binary_sha256"] == hashlib.sha256(b"engine").hexdigest()


def test_a_missing_binary_is_a_null_hash_not_a_problem(tmp_path):
    code, result = _judge(tmp_path, GREEN, with_binary=False)
    assert code == 0 and result["binary_sha256"] is None


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


def test_a_main_list_that_does_not_exist_is_said_and_passed(tmp_path, capsys):
    code, _r = _judge(tmp_path, GREEN, extra=["--main-required", str(tmp_path / "no" / "required.json")])
    assert code == 0 and "main has no required list yet" in capsys.readouterr().out


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


# --- the real list -----------------------------------------------------------------------------------------------
def _real():
    return json.loads(REQUIRED.read_text(encoding="utf-8"))


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
