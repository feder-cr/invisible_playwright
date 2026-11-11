"""Point Playwright's OWN test suite at our client, and count what passes.

⛔ THIS IS THE BASELINE THE CONTRACT WORK NEEDS, and taking it first is the
whole point. `tests/playwright-upstream/` holds Playwright's suite - 154 files,
2173 tests, 41596 lines - vendored as a reference copy and, until now, never run
against anything of ours: its `conftest.py` imports the real `playwright`
package, so it has only ever measured Microsoft's client.

The plan it serves: replacing the channel between our client and our server
removes the boundary `diff_protocol.py` compares across, so the judgement has to
move up a level, from the PROTOCOL to the CONTRACT. This suite is that judge -
but only if we know, before touching anything, which of those 2173 pass TODAY.
A baseline taken afterwards proves nothing: every failure would be arguable.

⛔ THE REFERENCE COPY IS NOT EDITED. Not tidiness - `tests/test_fork.py`
deliberately excludes that folder from every check so it can be read as-is, and
a suite we have quietly patched is a suite that agrees with us. The redirection
happens here, in a pytest plugin loaded BEFORE the suite's conftest: every
`playwright.X` name is bound to the already-imported `invisible_playwright._pw.X`
object.

⛔ AND THE ALIASES ARE PRE-REGISTERED, ONE OBJECT PER MODULE. Setting only
`sys.modules["playwright"]` would let the import machinery find the submodules
by itself through `_pw/`'s `__path__` - and register a SECOND module object for
the same file, so `isinstance(x, playwright.sync_api.Page)` would be false for a
Page the suite had just been handed. Two objects for one class is the kind of
failure that reads as a product defect for a whole afternoon.

    python scripts/upstream_baseline.py <firefox-binary>
    python scripts/upstream_baseline.py <firefox-binary> --which sync
    python scripts/upstream_baseline.py <firefox-binary> --shard 2/6

A shard owns whole test FILES, chosen by the time each file took in a recorded
run (tests/gates/upstream_weights.json), so the jobs finish at about the same
time. After a run, refresh the weights from its reports (a shard that was
killed left no .xml, but its --record file, .jsonl, is read the same way):

    python scripts/upstream_baseline.py --write-weights --run-id <id> upstream-Linux-*.xml upstream-Windows-*.xml upstream-*.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import pathlib
import platform
import re
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = ROOT / "tests" / "playwright-upstream"
#: Seconds each test file took, per OS, in a recorded CI run.
WEIGHTS = ROOT / "tests" / "gates" / "upstream_weights.json"
#: The folder with `sitecustomize.py` that puts the redirect into every child
#: interpreter (see `_pin_launch` in the plugin below).
SITE = ROOT / "scripts" / "upstream_site"

#: Every `playwright.*` module the suite imports, counted from its own source.
#: ⛔ Read from the suite rather than listed by hand: a name added upstream must
#: show up as a missing alias, not as a silent fallback to the real package.
def wanted_modules() -> list:
    found = set()
    for f in SUITE.rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.findall(r"(?:from|import)\s+(playwright[\w.]*)", text):
            found.add(m)
    return sorted(found)


def parse_shard(text: str) -> tuple:
    """"K/N" -> (K, N), 1-based, or a ValueError that says what was wrong."""
    m = re.fullmatch(r"(\d+)/(\d+)", text.strip())
    if not m:
        raise ValueError("a shard is written K/N, for example 2/4: %r" % text)
    k, n = int(m.group(1)), int(m.group(2))
    if not 1 <= k <= n:
        raise ValueError("shard %d/%d is out of range: K runs from 1 to N"
                         % (k, n))
    return k, n


# ── how the files are spread over the jobs ──────────────────────────────────
#
# The first version handed a file to the shard its path hashed to. That is
# balanced by count and not by time, and time is what a job is bounded by: in
# the first CI run the same four shards took 3.6, 7 and 14 minutes, and the
# fourth did not finish in 16. The time of this suite is not spread evenly over
# its tests: a test that fails by waiting for a 30 s timeout is run four times
# (the suite's conftest turns on three reruns), so ~120 s each, and 9 tests of
# 558 were 63% of the time. The best predictor of a file's time is that file's
# time in an earlier run, so that is what is committed and what the assignment
# uses.

def current_os() -> str:
    """"Linux" or "Windows"; anything else is planned as the slower Windows."""
    name = platform.system()
    return name if name in ("Linux", "Windows") else "Windows"


def file_of(classname: str) -> str:
    """Suite-relative posix path of the file a junit `classname` lives in.

    `tests.sync.test_page_clock.TestRunFor` -> `tests/sync/test_page_clock.py`:
    the class, when there is one, is not part of the path.
    """
    parts = []
    for part in classname.split("."):
        parts.append(part)
        if part.startswith("test_"):
            break
    return "/".join(parts) + ".py"


def load_weights(path=None) -> dict:
    return json.loads(pathlib.Path(path or WEIGHTS).read_text(encoding="utf-8"))


def planned_seconds(weights: dict, os_name: str, rels) -> dict:
    """file -> seconds a job spends on it (its tests plus the per-file cost).

    A file the weights do not know gets the MEDIAN of the ones they do: no
    better guess exists for a file nobody has timed, and the mean is dragged up
    by the few files that wait for timeouts.
    """
    known = weights["seconds"][os_name]
    median = statistics.median(known.values()) if known else 0.0
    per_file = weights["overhead_per_file"][os_name]
    return {r: known.get(r, median) + per_file for r in rels}


def assign(seconds: dict, count: int) -> dict:
    """file -> shard index (0-based): heaviest file first, into the lightest shard.

    Greedy longest-processing-time. Fully determined by the (file, seconds)
    pairs: ties in weight are broken by path, ties in load by shard index, so
    every job of a run computes the same split on its own.
    """
    load = [0.0] * count
    owner = {}
    for rel in sorted(seconds, key=lambda r: (-seconds[r], r)):
        k = min(range(count), key=lambda i: (load[i], i))
        owner[rel] = k
        load[k] += seconds[rel]
    return owner


def shard_loads(seconds: dict, count: int) -> list:
    """Predicted seconds of each shard."""
    load = [0.0] * count
    for rel, k in assign(seconds, count).items():
        load[k] += seconds[rel]
    return load


def suite_files(target: str) -> list:
    found = sorted(SUITE.joinpath(target).rglob("test_*.py"))
    return [str(f.relative_to(SUITE)).replace("\\", "/") for f in found]


def test_key(classname: str, name: str) -> str:
    """A test inside its file, as pytest writes it after the file: `name`, or
    `Class::name` for a test in a class."""
    parts = classname.split(".")
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            return "::".join(parts[i + 1:] + [name])
    return name


def split_parts(weights: dict, os_name: str, rel: str):
    """(parts, {test key: part}, seconds of each part) of a SPLIT file, or None.

    A file that alone takes more than a shard is listed under `splits` in the
    weights, with the seconds of each of its tests and the number of parts. The
    tests go heaviest first into the lightest part, ties broken by name, so
    every job computes the same parts. A test the table does not know (a new
    one upstream) is placed by the plugin from a hash of its name.
    """
    entry = weights.get("splits", {}).get(rel)
    if not entry:
        return None
    count = entry["parts"]
    tests = entry["seconds"][os_name]
    load = [0.0] * count
    mapping = {}
    for key in sorted(tests, key=lambda k: (-tests[k], k)):
        j = min(range(count), key=lambda i: (load[i], i))
        mapping[key] = j
        load[j] += tests[key]
    return count, mapping, load


def planned_units(weights: dict, os_name: str, rels) -> dict:
    """unit -> seconds: a file, or `file#K/M` for the K-th of M parts of a split
    file. These are what is dealt to the shards."""
    secs = planned_seconds(weights, os_name, rels)
    per_file = weights["overhead_per_file"][os_name]
    units = {}
    for rel in rels:
        split = split_parts(weights, os_name, rel)
        if split is None:
            units[rel] = secs[rel]
            continue
        count, _, load = split
        for j in range(count):
            units["%s#%d/%d" % (rel, j + 1, count)] = load[j] + per_file
    return units


def shard_plan(target: str, shard: tuple, os_name=None, weights=None) -> tuple:
    """(files, split) of `shard`: the test files it runs, and for each file it
    runs only some tests of, {file: {parts, keep, map}} for the plugin."""
    rels = suite_files(target)
    w = weights if weights is not None else load_weights()
    name = os_name or current_os()
    owner = assign(planned_units(w, name, rels), shard[1])
    mine = [u for u, k in owner.items() if k == shard[0] - 1]
    files = sorted({u.split("#")[0] for u in mine})
    split = {}
    for unit in mine:
        if "#" not in unit:
            continue
        rel, part = unit.split("#")
        count, mapping, _ = split_parts(w, name, rel)
        entry = split.setdefault(
            rel, {"parts": count, "keep": [], "map": mapping})
        entry["keep"].append(int(part.split("/")[0]) - 1)
    return files, split


def shard_files(target: str, shard: tuple, os_name=None, weights=None) -> list:
    """The test files under `target` that belong to `shard`, suite-relative."""
    return shard_plan(target, shard, os_name, weights)[0]


# ── the weights, from junit reports ─────────────────────────────────────────

def junit_seconds(path) -> tuple:
    """(file -> seconds of its testcases, seconds outside any testcase).

    A test that was rerun has one testcase per attempt, and all of them count:
    the time is real. The second number is the suite time the report states
    minus what its testcases add up to: collection, session fixtures, module
    imports.
    """
    root = ET.parse(str(path)).getroot()
    per_file = {}
    for case in root.iter("testcase"):
        f = file_of(case.get("classname", ""))
        per_file[f] = per_file.get(f, 0.0) + float(case.get("time") or 0)
    suite = root.find("testsuite")
    stated = float(suite.get("time") or 0) if suite is not None else 0.0
    return per_file, max(0.0, stated - sum(per_file.values()))


def junit_tests(path) -> dict:
    """file -> {test key: seconds, every attempt counted} of a junit report."""
    out: dict = {}
    for case in ET.parse(str(path)).getroot().iter("testcase"):
        classname = case.get("classname", "")
        per_test = out.setdefault(file_of(classname), {})
        key = test_key(classname, case.get("name", ""))
        per_test[key] = per_test.get(key, 0.0) + float(case.get("time") or 0)
    return out


def os_of_report(path) -> str:
    name = pathlib.Path(path).name.lower()
    for candidate in ("linux", "windows"):
        if candidate in name:
            return candidate.capitalize()
    raise ValueError("cannot tell the OS of %s: name it ...Linux... or "
                     "...Windows..., or pass --weights-os" % path)


def record_seconds(path) -> tuple:
    """(file -> seconds, file in progress when the record ends) from a record.

    The record is the JSON lines `scripts/upstream_record.py` appends while a
    shard runs; it exists even when the shard was killed. Files run one after
    the other, so every file but the last one named is complete; the last one
    may have been cut off, and its seconds are a lower bound. A line cut in
    half by the kill is ignored.
    """
    per_file: dict = {}
    last = None
    for line in pathlib.Path(path).read_text(encoding="utf-8",
                                             errors="replace").splitlines():
        try:
            row = json.loads(line)
            f = row["id"].split("::")[0].replace("\\", "/")
            per_file[f] = per_file.get(f, 0.0) + float(row["duration"])
        except (ValueError, KeyError, TypeError):
            continue
        last = f
    return per_file, last


def _ratio(secs_a: dict, secs_b: dict, skip_a: set, skip_b: set):
    """Sum of A over sum of B on the files both systems really measured."""
    both = [f for f in secs_a if f in secs_b and f not in skip_a
            and f not in skip_b]
    total_b = sum(secs_b[f] for f in both)
    return sum(secs_a[f] for f in both) / total_b if total_b else None


def fill_from_other_os(data: dict) -> None:
    """Give a file that one system never measured the other system's time.

    The two systems run different shards, so a file that is unmeasured on one
    is usually measured on the other. Its time there, times the ratio of the
    two systems over the files both measured, is a better guess than any
    model. The file stays under `estimated` (it was not measured on this OS),
    and is named under `scaled` so a later report can replace it.
    """
    systems = [n for n in ("Linux", "Windows") if n in data["seconds"]]
    if len(systems) < 2:
        return
    est = {n: set(data["estimated"].get(n, [])) for n in systems}
    for a, b in ((systems[0], systems[1]), (systems[1], systems[0])):
        ratio = _ratio(data["seconds"][a], data["seconds"][b], est[a], est[b])
        scaled = set()
        if ratio:
            for f, t in data["seconds"][b].items():
                if f in est[b]:
                    continue
                if f not in data["seconds"][a] or f in est[a]:
                    data["seconds"][a][f] = round(t * ratio, 1)
                    est[a].add(f)
                    scaled.add(f)
        data.setdefault("scaled", {})[a] = sorted(scaled)
        data["estimated"][a] = sorted(est[a])
        data["seconds"][a] = dict(sorted(data["seconds"][a].items()))


def write_weights(reports: list, run_id: str, date: str, os_name=None,
                  path=None) -> dict:
    """Fold junit reports (and records) into the weights file; return it.

    Files seen in a junit report take the measured time; files in no report
    keep what they had. A record (`*.jsonl`) of a shard with no junit gives its
    complete files the same way, and the file it ended on a lower bound that
    is kept as an estimate (listed under `lower_bound`) unless a measured
    value is already there. The per-file cost is re-measured from the junit
    reports (averaged with the last figure). What neither OS measured is then filled from the other OS.
    """
    target = pathlib.Path(path or WEIGHTS)
    if target.exists():
        data = json.loads(target.read_text(encoding="utf-8"))
    else:
        data = {"format": 1, "runs": {}, "overhead_per_file": {},
                "seconds": {}, "estimated": {}}
    data.setdefault("lower_bound", {})
    by_os = {}
    for r in reports:
        by_os.setdefault(os_name or os_of_report(r), []).append(str(r))
    for name, paths in by_os.items():
        secs = data["seconds"].setdefault(name, {})
        est = set(data["estimated"].get(name, []))
        low = set(data["lower_bound"].get(name, []))
        outside, files = 0.0, 0
        measured = set()
        # Summed over the reports of this call: the parts of a split file are
        # in different shards, and the file took what its parts took together.
        total: dict = {}
        keyed: dict = {}
        for p in (p for p in paths if not p.endswith(".jsonl")):
            per_file, extra = junit_seconds(p)
            for f, t in per_file.items():
                total[f] = total.get(f, 0.0) + t
            for f, tests in junit_tests(p).items():
                for key, t in tests.items():
                    keyed.setdefault(f, {})[key] = (
                        keyed.get(f, {}).get(key, 0.0) + t)
            outside += extra
            files += len(per_file)
        for f, t in total.items():
            secs[f] = round(t, 1)
            est.discard(f)
            low.discard(f)
            measured.add(f)
        for f, entry in data.get("splits", {}).items():
            if f in keyed:
                table = entry["seconds"].setdefault(name, {})
                table.update({k: round(t, 1) for k, t in keyed[f].items()})
        for p in (p for p in paths if p.endswith(".jsonl")):
            per_file, last = record_seconds(p)
            for f, t in per_file.items():
                if f in measured:
                    continue
                if f != last:
                    secs[f] = round(t, 1)
                    est.discard(f)
                    low.discard(f)
                    measured.add(f)
                elif f in est or f not in secs:
                    secs[f] = round(max(t, secs.get(f, 0.0)), 1)
                    est.add(f)
                    low.add(f)
        if files:
            # Averaged with the previous figure: it moves a lot from shard to
            # shard (4 to 13 s per file), and one run should not set it alone.
            now = outside / files
            before = data["overhead_per_file"].get(name)
            data["overhead_per_file"][name] = round(
                now if before is None else (before + now) / 2, 1)
        data["estimated"][name] = sorted(est)
        data["lower_bound"][name] = sorted(low)
        old = data["runs"].get(name)
        entry = {"run_id": str(run_id), "date": date}
        also = list(old.get("also", [])) if old else []
        if old and old["run_id"] != str(run_id):
            also.append(old["run_id"])
        if also:
            entry["also"] = also
        data["runs"][name] = entry
        data["seconds"][name] = dict(sorted(secs.items()))
    fill_from_other_os(data)
    target.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8",
                      newline="\n")
    return data


PLUGIN = '''
"""Loaded with -p, before the suite's conftest, so the redirection is in place
by the time anything imports `playwright`."""
import sys, pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import invisible_playwright._pw as _pw

_NAMES = __NAMES__


class _Absent:
    """A module our client does not have, that says so when touched."""

    def __init__(self, name, why):
        self.__name__ = name
        self._why = why

    def __getattr__(self, attr):
        # ⛔ IT HANDS BACK A CALLABLE AND RAISES ON THE CALL, not on the
        # attribute. `from playwright._impl._driver import
        # compute_driver_executable` is a module-level line in the suite's
        # conftest, so raising here kills COLLECTION and takes all 657 tests
        # with it - for a name used inside exactly one fixture. Failing at the
        # call means the baseline loses only the tests that actually reach it,
        # which is the honest count.
        why = self._why
        name = self.__name__

        def gone(*a, **k):
            raise RuntimeError(
                "%s.%s does not exist in invisible_playwright: %s. This suite "
                "is Playwright's own and tests components this package "
                "removed - the Node driver went on 2026-08-28 - so a test that "
                "reaches this name is testing something we deliberately do not "
                "ship, not something broken." % (name, attr, why))

        gone.__name__ = attr
        return gone


def _absent(name, why):
    return _Absent(name, why)

sys.modules["playwright"] = _pw
for _name in _NAMES:
    if _name == "playwright":
        continue
    _tail = _name.split(".", 1)[1]
    try:
        _mod = __import__("invisible_playwright._pw." + _tail,
                          fromlist=["_"])
    except ImportError as _failure:
        # ⛔ A STAND-IN THAT EXPLAINS, not a silent skip and not a crash at
        # collection. Our client genuinely lacks some modules - `_impl._driver`
        # went with the Node driver - and the suite imports them at the top of
        # its conftest while using them in ONE fixture. Refusing here would
        # lose all 657 tests to a trace-viewer path; falling back to the real
        # `playwright` would measure Microsoft's client and call it ours. So
        # the name exists, and touching it says exactly what was removed.
        _mod = _absent(_name, str(_failure))
    sys.modules[_name] = _mod


def _pin_launch():
    """Give every launch OUR engine.

    The suite calls `browser_type.launch(**launch_arguments)` and those
    arguments carry only `headless`: upstream downloads its browsers, we pin
    one. Our launch REFUSES without an `executable_path` - correctly, it is the
    seal's whole job - so the binary is injected here rather than by editing
    the reference copy.

    ⛔ AT MODULE LEVEL, NOT ONLY IN A PYTEST HOOK, and the binary comes from
    the environment. A test that starts a child with `multiprocessing` gets a
    fresh interpreter on Windows (spawn, not fork): nothing of pytest's state
    is in it, so a redirect installed only by a hook is missing there, and the
    child imports the stock `playwright` and looks for its own Firefox. Every
    interpreter of the job imports this module first (`upstream_site/
    sitecustomize.py`), so the redirect holds in the children too. Idempotent:
    the pytest hook calls it again and it installs nothing twice.
    """
    import os
    binary = os.environ.get("INVPW_BINARY_PATH")
    if not binary:
        return
    from invisible_playwright._pw._impl import _browser_type as _bt

    if getattr(_bt.BrowserType.launch, "_invpw_pinned", False):
        return
    original = _bt.BrowserType.launch

    async def launch(self, *args, **kwargs):
        # ⛔ `executablePath`, camelCase: the impl layer takes the wire
        # spelling, and `executable_path` would be swallowed by nothing and
        # silently ignored - the launch would refuse exactly as before, and
        # the patch would look installed while doing nothing.
        if not kwargs.get("executablePath"):
            kwargs["executablePath"] = binary
        return await original(self, *args, **kwargs)

    launch._invpw_pinned = True
    _bt.BrowserType.launch = launch
    print("[baseline] every launch pinned to %s" % binary)


_pin_launch()


def pytest_sessionstart(session):
    _pin_launch()
'''


def job_env(binary: str, environ=None) -> dict:
    """The environment pytest and every interpreter it starts run with."""
    import os
    env = dict(os.environ if environ is None else environ)
    env["INVPW_BINARY_PATH"] = binary
    # ⛔ THIS CHECKOUT'S `src` TOO, or the suite measures whatever wrapper is
    # INSTALLED: `ROOT` alone only makes `scripts._upstream_alias` importable,
    # and `invisible_playwright` then resolves to the editable install, which
    # from a worktree is somebody else's tree on somebody else's commit. A
    # branch measured that way reports main's numbers as its own.
    # SITE first: its `sitecustomize` installs the redirect in every child.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SITE), str(ROOT), str(ROOT / "src")])
    return env


def write_plugin() -> pathlib.Path:
    out = ROOT / "scripts" / "_upstream_alias.py"
    names = wanted_modules()
    out.write_bytes(PLUGIN.replace("__NAMES__", repr(names)).encode("utf-8"))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("binary", nargs="?",
                   help="the engine binary (not needed with --write-weights)")
    p.add_argument("--which", default="sync", choices=("sync", "async", "all"),
                   help="the sync arm first: it is the one our own API mirrors")
    p.add_argument("-k", dest="selection", default=None)
    p.add_argument("--timeout", default="60")
    p.add_argument("--junit-xml", dest="junit", default=None,
                   help="write a JUnit XML too: it is the only per-test record "
                        "this suite produces, and `upstream_regressions.py` "
                        "needs it")
    p.add_argument("--shard", default=None, metavar="K/N",
                   help="run only the test FILES that shard K of N owns (K from "
                        "1): the files are split by the time each took in a "
                        "recorded run (tests/gates/upstream_weights.json), "
                        "heaviest first into the lightest shard, so CI can "
                        "spread one suite over N jobs of about the same length "
                        "and a file always lands in the same one")
    p.add_argument("--record", default=None, metavar="FILE",
                   help="append one JSON line per test report to FILE as the "
                        "tests run (scripts/upstream_record.py): a shard that "
                        "is killed before pytest writes its JUnit XML still "
                        "leaves this, and --write-weights reads it")
    p.add_argument("--per-file", action="store_true",
                   help="one pytest process per file: mandatory on the async "
                        "arm, see `_per_file`")
    p.add_argument("--weights-os", choices=("Linux", "Windows"), default=None,
                   help="whose weights plan the shards (default: this OS); "
                        "with --write-weights, the OS of the reports "
                        "(default: read from their file names)")
    p.add_argument("--write-weights", nargs="+", metavar="JUNIT", default=None,
                   help="do not run anything: fold these junit reports (and "
                        "--record files, *.jsonl, of shards that left no "
                        "junit) into tests/gates/upstream_weights.json "
                        "(seconds per test file) and exit")
    p.add_argument("--run-id", default="local",
                   help="with --write-weights: the CI run the reports came from")
    p.add_argument("--date", default=None,
                   help="with --write-weights: its date (default: today, UTC)")
    a = p.parse_args()
    if a.write_weights:
        date = a.date or datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%d")
        data = write_weights(a.write_weights, a.run_id, date, a.weights_os)
        for name in sorted(data["seconds"]):
            print("%s: %d files, %.0f s in all, %.1f s per file outside the "
                  "tests, run %s" % (
                      name, len(data["seconds"][name]),
                      sum(data["seconds"][name].values()),
                      data["overhead_per_file"][name],
                      data["runs"][name]["run_id"]))
        return 0
    if not a.binary:
        p.error("the engine binary is required (or use --write-weights)")
    shard = None
    if a.shard is not None:
        try:
            shard = parse_shard(a.shard)
        except ValueError as e:
            p.error(str(e))

    plugin = write_plugin()
    print("aliased modules: %s" % ", ".join(wanted_modules()))
    print("plugin: %s" % plugin)

    target = {"sync": "tests/sync", "async": "tests/async",
              "all": "tests"}[a.which]
    # Without --shard the target stays the directory, exactly as before. With
    # it, the files this shard owns replace the directory on the command line.
    split = {}
    if shard:
        targets, split = shard_plan(target, shard, a.weights_os)
        if not targets:
            # An empty shard would pass as "0 tests, exit 5" in some jobs and
            # as a green nothing in others: say it is a configuration error.
            p.error("shard %d/%d owns no test file under %s"
                    % (shard[0], shard[1], target))
        print("shard %d/%d: %d files, %d of them in part (planned with the %s "
              "weights)" % (shard[0], shard[1], len(targets), len(split),
                            a.weights_os or current_os()))
    if not shard or a.per_file:
        targets = [target]
    argv = [sys.executable, "-m", "pytest", *targets,
            "-o", "addopts=", "-p", "scripts._upstream_alias",
            "-p", "scripts.upstream_record", "-p", "scripts.upstream_split",
            "--browser", "firefox", "-p", "no:cacheprovider",
            "--timeout", a.timeout, "--timeout-method", "thread",
            "-q", "--no-header", "-rN"]
    if a.selection:
        argv += ["-k", a.selection]
    if a.junit:
        # ⛔ An absolute path: pytest runs with `cwd=SUITE`, so a relative one
        # lands inside the vendored copy, which nothing is allowed to write to.
        argv += ["--junitxml", str(pathlib.Path(a.junit).resolve())]

    env = job_env(a.binary)
    if split:
        # which tests of a split file this shard runs: scripts/upstream_split.py
        env["INVPW_SPLIT"] = json.dumps(split)
    if a.record:
        # ⛔ Absolute (pytest runs with `cwd=SUITE`) and started empty: a record
        # left by an earlier run would be read as this one's.
        record = pathlib.Path(a.record).resolve()
        record.write_text("", encoding="utf-8")
        env["INVPW_RECORD"] = str(record)

    if not a.per_file:
        print("running: %s" % " ".join(argv[3:]))
        r = subprocess.run(argv, cwd=SUITE, env=env)
        return r.returncode

    return _per_file(argv, target, env, shard, a.weights_os)


def _per_file(argv: list, target: str, env: dict, shard=None,
              weights_os=None) -> int:
    """One pytest process per FILE, and the reason is structural.

    The async arm cannot be measured in a single process. `pytest-asyncio` runs
    the whole session on ONE event loop, and a test that blocks it never lets
    it go: `--timeout-method thread` prints the stacks and then the session
    DIES, with no summary. Windows has neither of the two ways out that would
    do elsewhere - `--timeout-method signal` and `--forked`.

    Measured 2026-08-29: the whole run stopped at 13%, and reproduced in
    isolation two tests of the same file are enough - the first passes, the
    second hangs, the run dies. Counted, the tests that never got to speak were
    more than twelve hundred.

    One process per file means a hang costs THAT file and not the run. The
    price is a browser restarted per file, so the async arm is slower than the
    sync one by construction: that is what being measurable at all costs.
    """
    files = sorted((SUITE / target).glob("test_*.py"))
    if shard:
        owned = set(shard_files(target, shard, weights_os))
        files = [f for f in files
                 if str(f.relative_to(SUITE)).replace("\\", "/") in owned]
    print("per-file: %d files, one process each" % len(files),
          flush=True)
    totals = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    died = []
    for i, f in enumerate(files, 1):
        rel = str(f.relative_to(SUITE)).replace("\\", "/")
        argv_file = [x if x != target else rel for x in argv]
        r = subprocess.run(argv_file, cwd=SUITE, env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        output = (r.stdout or "") + (r.stderr or "")
        count = _count(output)
        for k in totals:
            totals[k] += count.get(k, 0)
        # ⛔ A FILE WITH NO SUMMARY IS NOT A FILE WITH ZERO FAULTS: it is a
        # file that died. Counting it as zero is the easiest false green there
        # is to write into an aggregator.
        if not count:
            died.append(rel)
        # ⛔ `flush=True`: redirected to a file, Python buffers stdout, and a
        # loop that runs for an hour writing nothing is indistinguishable from
        # one that has hung. The first real run of this mode had to be followed
        # by counting processes and CPU time, and when it was stopped its
        # partial result died inside the buffer.
        print("  %3d/%d  %-58s %s" % (i, len(files), rel,
                                      _summarise(count) if count
                                      else "DIED (no summary)"),
              flush=True)
    print()
    print("TOTAL  passed=%(passed)d failed=%(failed)d error=%(error)d "
          "skipped=%(skipped)d" % totals)
    if died:
        print("files that DIED (no summary, so not counted): %d" % len(died))
        for m in died:
            print("   " + m)
    return 1 if died else 0


def _count(text: str) -> dict:
    """The numbers on pytest's summary line, or {} when there is none."""
    last = None
    for line in text.splitlines():
        if re.search(r"\d+ (passed|failed|error|skipped)", line):
            last = line
    if not last:
        return {}
    out = {}
    for number, word in re.findall(r"(\d+) (passed|failed|errors?|skipped)",
                                   last):
        out["error" if word.startswith("error") else word] = int(number)
    return out


def _summarise(c: dict) -> str:
    return " ".join("%s=%d" % (k, v) for k, v in sorted(c.items()) if v)


if __name__ == "__main__":
    sys.exit(main())
