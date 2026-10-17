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
"""
from __future__ import annotations

import argparse
import importlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = ROOT / "tests" / "playwright-upstream"

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


def pytest_sessionstart(session):
    """Give every launch OUR engine.

    The suite calls `browser_type.launch(**launch_arguments)` and those
    arguments carry only `headless`: upstream downloads its browsers, we pin
    one. Our launch REFUSES without an `executable_path` - correctly, it is the
    seal's whole job - so the binary is injected here rather than by editing
    the reference copy.
    """
    import os
    binary = os.environ.get("INVPW_BINARY_PATH")
    if not binary:
        return
    from invisible_playwright._pw._impl import _browser_type as _bt

    original = _bt.BrowserType.launch

    async def launch(self, *args, **kwargs):
        # ⛔ `executablePath`, camelCase: the impl layer takes the wire
        # spelling, and `executable_path` would be swallowed by nothing and
        # silently ignored - the launch would refuse exactly as before, and
        # the patch would look installed while doing nothing.
        if not kwargs.get("executablePath"):
            kwargs["executablePath"] = binary
        return await original(self, *args, **kwargs)

    _bt.BrowserType.launch = launch
    print("[baseline] every launch pinned to %s" % binary)
'''


def write_plugin() -> pathlib.Path:
    out = ROOT / "scripts" / "_upstream_alias.py"
    names = wanted_modules()
    out.write_bytes(PLUGIN.replace("__NAMES__", repr(names)).encode("utf-8"))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("binary")
    p.add_argument("--which", default="sync", choices=("sync", "async", "all"),
                   help="the sync arm first: it is the one our own API mirrors")
    p.add_argument("-k", dest="selection", default=None)
    p.add_argument("--timeout", default="60")
    p.add_argument("--junit-xml", dest="junit", default=None,
                   help="write a JUnit XML too: it is the only per-test record "
                        "this suite produces, and `upstream_regressions.py` "
                        "needs it")
    p.add_argument("--per-file", action="store_true",
                   help="one pytest process per file: mandatory on the async "
                        "arm, see `_per_file`")
    a = p.parse_args()

    plugin = write_plugin()
    print("aliased modules: %s" % ", ".join(wanted_modules()))
    print("plugin: %s" % plugin)

    target = {"sync": "tests/sync", "async": "tests/async",
              "all": "tests"}[a.which]
    argv = [sys.executable, "-m", "pytest", target,
            "-o", "addopts=", "-p", "scripts._upstream_alias",
            "--browser", "firefox", "-p", "no:cacheprovider",
            "--timeout", a.timeout, "--timeout-method", "thread",
            "-q", "--no-header", "-rN"]
    if a.selection:
        argv += ["-k", a.selection]
    if a.junit:
        # ⛔ An absolute path: pytest runs with `cwd=SUITE`, so a relative one
        # lands inside the vendored copy, which nothing is allowed to write to.
        argv += ["--junitxml", str(pathlib.Path(a.junit).resolve())]

    import os
    env = dict(os.environ)
    env["INVPW_BINARY_PATH"] = a.binary
    # ⛔ THIS CHECKOUT'S `src` TOO, or the suite measures whatever wrapper is
    # INSTALLED: `ROOT` alone only makes `scripts._upstream_alias` importable,
    # and `invisible_playwright` then resolves to the editable install, which
    # from a worktree is somebody else's tree on somebody else's commit. A
    # branch measured that way reports main's numbers as its own.
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "src")])

    if not a.per_file:
        print("running: %s" % " ".join(argv[3:]))
        r = subprocess.run(argv, cwd=SUITE, env=env)
        return r.returncode

    return _per_file(argv, target, env)


def _per_file(argv: list, target: str, env: dict) -> int:
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
