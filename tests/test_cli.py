import subprocess
import sys
from pathlib import Path

import pytest

from invisible_playwright import cli


@pytest.mark.unit
def test_version_subcommand():
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright", "version"],
        capture_output=True, text=True, check=True,
    )
    assert "firefox-" in r.stdout
    assert "invisible_playwright" in r.stdout.lower()


@pytest.mark.unit
def test_help_subcommand():
    r = subprocess.run(
        [sys.executable, "-m", "invisible_playwright", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "fetch" in r.stdout
    assert "version" in r.stdout


@pytest.mark.unit
def test_the_cli_offers_exactly_two_commands():
    """Six subcommands became two, and this is what keeps it there.

    `path`, `clear-cache`, `doctor` and `fetch --force` were each a step somebody
    had to know to take, in a package whose promise is that the browser handles
    itself. Their behaviour did not go: `fetch` checks every cached tree against
    the seal on every run - which is what `doctor` did, and it was the thing most
    worth doing and least likely to be typed - and prints the path as its last
    line, which is what `path` did with the added guarantee that the thing it
    names exists.

    `clear-cache` is the one deliberately NOT folded in: the cache root belongs
    to invisible_core, so pruning trees no seal points at would delete an engine
    this package did not put there. It was written for invisible_firefox, whose
    repository was deleted on 2026-08-18 while the package stayed on the index;
    the reason holds for anything else built on the core. `version` prints the
    location instead.

    Asserted as an exact set, not a subset. A subset check passes while the
    surface grows back one convenience at a time, which is how it got to six.
    """
    import argparse

    parser = cli.build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(actions) == 1, actions
    assert set(actions[0].choices) == {"fetch", "version"}, sorted(actions[0].choices)


@pytest.mark.unit
def test_fetch_takes_no_arguments():
    """The tag went with the commands. The seal decides which engine this build
    runs and `verify_engine` refuses anything else, so a tag on the command line
    could only ever name something that would then be rejected - and `--force` is
    unnecessary once every run verifies: a tree is replaced because it does not
    match, not because a flag was passed."""
    import argparse

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    fetch = sub.choices["fetch"]
    optionals = {o for a in fetch._actions for o in a.option_strings}
    assert optionals == {"-h", "--help"}, optionals
    positionals = [a.dest for a in fetch._actions if not a.option_strings]
    assert not positionals, positionals




@pytest.mark.unit
def test_fetch_prints_the_path_as_its_last_line(tmp_path, monkeypatch, capsys):
    """`$(invisible-playwright fetch)` has to be a path.

    That is the whole replacement for the `path` subcommand, so the path must be
    the last line of stdout and nothing else may follow it. The mismatch report
    goes to stderr for exactly this reason.
    """
    fake = tmp_path / "firefox.exe"
    fake.write_text("x")
    monkeypatch.setattr("invisible_playwright.cli.ensure_binary", lambda: fake)
    monkeypatch.setattr("invisible_core.download.iter_cached_engines", lambda: [])

    rc = cli.main(["fetch"])

    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().splitlines()[-1] == str(fake)


@pytest.mark.unit
def test_a_mismatching_cached_tree_is_reported_but_does_not_stop_the_fetch(
    tmp_path, monkeypatch, capsys,
):
    """This is what `doctor` used to do, and why it is not fatal.

    A tree left over from an older seal is the ordinary case, not a fault:
    `ensure_binary` refuses it by construction and fetches the sealed one, so
    reporting it and carrying on IS the repair. It goes to stderr so it cannot
    end up inside a captured path.
    """
    fake = tmp_path / "firefox.exe"
    fake.write_text("x")
    stale = tmp_path / "firefox-14"
    monkeypatch.setattr("invisible_playwright.cli.ensure_binary", lambda: fake)
    monkeypatch.setattr("invisible_core.download.iter_cached_engines",
                        lambda: [(stale, object())])
    monkeypatch.setattr("invisible_core.seal.engine_problems",
                        lambda ident, seal: ["application.ini Version '150.0.1' != '151.0'"])

    rc = cli.main(["fetch"])

    cap = capsys.readouterr()
    assert rc == 0
    assert "firefox-14" in cap.err and "150.0.1" in cap.err
    assert "firefox-14" not in cap.out
    assert cap.out.strip().splitlines()[-1] == str(fake)


@pytest.mark.unit
def test_no_subcommand_errors():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


@pytest.mark.unit
def test_unknown_subcommand_errors():
    with pytest.raises(SystemExit) as exc:
        cli.main(["bogus"])
    assert exc.value.code != 0


@pytest.mark.unit
def test_a_removed_subcommand_is_refused_rather_than_silently_ignored():
    """`doctor`, `path` and `clear-cache` were real commands people scripted.

    They must fail loudly now, not be swallowed. argparse gives that for free
    with a subparser set, and this is what stops somebody re-adding a permissive
    fallback that turns an unknown command into a no-op exit 0.
    """
    for gone in ("doctor", "path", "clear-cache"):
        with pytest.raises(SystemExit) as exc:
            cli.main([gone])
        assert exc.value.code != 0, gone


@pytest.mark.unit
def test_no_test_invokes_the_cli_with_arguments_it_rejects():
    """A test that calls a removed flag is a red workflow with a green suite.

    Measured 2026-08-04. `test_release_e2e.py` still ran `fetch --force` three
    days after `--force` went with four of the six subcommands in 0.5.0. Every
    run of that file exited 2 on "unrecognized arguments" - and the DEFAULT suite
    stayed green the whole time, because that test is `e2e`-marked and therefore
    deselected. One stale argument, three red workflows: `e2e` on main, and
    `user-install` on two release tags.

    A sibling guard already forbade `--force` in the engine-mismatch MESSAGE
    (`test_seal_wire_version.py`). It checked what the package TELLS a user to
    run and never what the tests themselves run, so the removed flag survived in
    the one place that actually executes it.

    This validates every invocation against the real parser rather than against a
    list of removed names. A list would have to be maintained by the same person
    who forgot to update the call site.
    """
    import argparse
    import pathlib
    import re

    parser = cli.build_parser()
    tests_dir = pathlib.Path(__file__).resolve().parent
    # Matches a subprocess argv of the form -m invisible_playwright <args>.
    # No example of one is written here: the first version put a sample
    # invocation in a comment on this line, and the scan flagged its own comment.
    # That is the defect `18-gate-inventory.md` records about the RFC 5737 gate,
    # which flagged its own docstring the day it was written.
    call = re.compile(
        r'"-m",\s*"invisible_playwright"\s*,\s*((?:"[^"]*"\s*,?\s*)+)')

    problems = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        if "playwright-upstream" in path.parts:
            continue
        # Comments stripped first, for the reason above.
        source = "\n".join(
            line.split("#", 1)[0]
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines())
        for match in call.finditer(source):
            args = re.findall(r'"([^"]*)"', match.group(1))
            args = [a for a in args if a]
            if not args:
                continue
            line = source[:match.start()].count("\n") + 1
            try:
                parser.parse_args(args)
            except SystemExit as exit_:
                # argparse exits 0 for --help and --version, which are valid
                # invocations, and 2 for a parse error. Only the second is a
                # defect. The first version of this gate treated both alike and
                # reported `--help` as broken.
                if exit_.code:
                    problems.append(f"{path.name}:{line} -> {args}")

    assert not problems, (
        "these tests invoke the CLI with arguments its own parser refuses, so "
        "they exit 2 wherever they actually run:\n  " + "\n  ".join(problems)
        + "\nThe surface is `fetch` and `version`, and `fetch` takes nothing.")
