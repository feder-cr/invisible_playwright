"""The install path a stranger takes, driven end to end.

A fresh venv, `pip install invisible-playwright` FROM THE INDEX, the engine
downloaded from the live GitHub release, and a real browser launched against a
real URL. Slow and network-bound, so marked `e2e` and excluded by default:

    pytest tests/test_release_e2e.py -m e2e -o addopts="" -v

Run by `.github/workflows/user-install.yml` on ubuntu AND windows, daily and on
release. `INVPW_E2E_SOURCE=git` (with `INVPW_E2E_REV`) reaches an unpublished
commit instead - a pre-release check, not the default.

TWO THINGS THIS FILE USED TO GET WRONG, both kept in the tests that fixed them:

  it installed from a GIT URL. That was nobody's install path after 2026-07-26,
    and a git install never resolves `invisible-core==X.Y.Z`, so the pin - the
    part most likely to break a release - was outside what this covered;
  it then installed a blessed `playwright==<pin>` by hand, which REPAIRS the
    venv. If the declared range resolves to a client the shipped Juggler cannot
    speak, that line turns the suite green while every fresh install is broken.

And the launch test was skipped on Windows for a reason that was false -
"headless launch requires a display server". It does not on Windows, where
`headless=True` keeps the real rendering pipeline and hides the window through
the binary's own cloak. The primary target was the one platform this never ran
on. It runs there now.

The cache is a temp dir per run (`INVISIBLE_PLAYWRIGHT_CACHE_DIR`), so these
never read or poison the developer's real cache - and never pass because
something was already cached.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import sys

import pytest

# ── venv mechanics, LOCAL ON PURPOSE ────────────────────────────────────────
# These three live in `invisible_core.testing` and every other suite in the
# three repos imports them from there. NOT this file. The job that runs it
# installs pytest on the runner and nothing else, deliberately: what is being
# tested is what a stranger gets from the index, and a second copy of anything
# on the runner's path would mean the assertions read the wrong one.
#
# So `from invisible_core.testing import ...` here is a COLLECTION error -
# ModuleNotFoundError, whole job red, nothing tested. It happened on every leg
# of all three repos on 2026-07-28, the day the helpers were shared, and it
# passes on any development machine because an editable install is always on
# the path. The core's suite now parses these files and refuses the import, so
# the rule does not depend on this comment being read.
def _run(cmd, *, timeout: int = 600, check: bool = True, env=None, cwd=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout, env=env,
                       cwd=str(cwd) if cwd is not None else None)
    if check and r.returncode != 0:
        raise AssertionError(
            "{} exited {}\n--- stdout ---\n{}\n--- stderr ---\n{}".format(
                " ".join(str(c) for c in cmd), r.returncode,
                r.stdout[-3000:], r.stderr[-3000:]))
    return r


def _venv_python(venv_dir):
    bindir = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return Path(venv_dir) / bindir / name


def _make_venv(target, *, upgrade_pip: bool = True):
    target = Path(target)
    _run([sys.executable, "-m", "venv", target], timeout=300)
    py = _venv_python(target)
    assert py.exists(), "no venv python at {}".format(py)
    if upgrade_pip:
        _run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], timeout=300)
    return py


run_checked = _run
venv_python = _venv_python
make_venv = _make_venv


# NOT imported at module level. This file is collected in an environment that
# deliberately does NOT have the package installed - the package under test
# goes into a venv the fixtures build, from the index, and a second copy on the
# runner's path would mean the assertions read the wrong one. Importing it here
# made collection fail outright (exit 4, "No module named invisible_playwright")
# on both CI runners while passing locally, where a checkout is always
# importable.
#
# Reading it from the VENV is also the more honest question: what a user gets
# is what the installed package says, not what this checkout says.
def _upstream_version(py: Path) -> str:
    out = subprocess.run(
        [str(py), "-c",
         "from invisible_playwright.constants import FIREFOX_UPSTREAM_VERSION as v; print(v)"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, (
        "could not read FIREFOX_UPSTREAM_VERSION from the installed package: "
        + (out.stdout or "") + " " + (out.stderr or ""))
    return out.stdout.strip().splitlines()[-1]

REPO_URL = "https://github.com/feder-cr/invisible_playwright.git"
REV = os.environ.get("INVPW_E2E_REV", "main")


# ---------- helpers --------------------------------------------------------- #


# ---------- fixtures -------------------------------------------------------- #


# The venv mechanics live in `invisible_core.testing`, and they are on the index
# as of the core release this package's pin now names - the pin moved in the same
# change, which is the order that was got wrong on 2026-07-28 and is written into
# CLAUDE.md's pre-push gate: publish the core, move the pin, then use the name.
#
# (No version number above, deliberately. A sibling guard,
# `test_the_expected_core_version_is_written_only_in_pyproject`, forbids a second
# copy of it anywhere in this repo - and it caught the first draft of this very
# comment, which is exactly the behaviour it is for.)
_run = run_checked
_venv_python = venv_python
_make_venv = make_venv


@pytest.fixture(scope="module")
def workspace() -> Path:
    """A single temp dir reused across the module so we don't re-create the
    venv + re-download the 110 MB tarball for every individual test."""
    root = Path(tempfile.mkdtemp(prefix="invpw-e2e-"))
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def clean_venv(workspace: Path) -> Path:
    """A fresh venv, pip upgraded. Returns its python executable path.

    Inside `workspace` rather than a temp dir of its own: the engine tarball
    and a private cache dir live there too, and re-downloading 110 MB per test
    is what the shared workspace exists to avoid.
    """
    return _make_venv(workspace / "venv")


@pytest.fixture(scope="module")
def isolated_cache_env(workspace: Path) -> dict:
    """Environment dict pointing the wrapper at a private cache dir so this
    test never reads or pollutes the developer's real cache."""
    cache = workspace / "cache"
    cache.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["INVISIBLE_PLAYWRIGHT_CACHE_DIR"] = str(cache)
    env["XDG_CACHE_HOME"] = str(cache)
    return env


# ---------- tests ----------------------------------------------------------- #


@pytest.mark.e2e
def test_clean_install_the_way_a_user_does_it(clean_venv: Path):
    """`pip install invisible-playwright`, from the index, into an empty venv.

    THIS USED TO INSTALL FROM A GIT URL, and that stopped being anybody's
    install path on 2026-07-26 when the three packages went to PyPI. A test
    that exercises a route no user takes cannot fail in the way users fail: the
    index resolves `invisible-core==X.Y.Z` and a git install does not resolve
    it at all, so the entire pin mechanism - the thing most likely to break a
    release - was outside what this file covered.

    Set ``INVPW_E2E_SOURCE=git`` to test an unpublished commit instead; that is
    a pre-release check, not the default, and it says so in the failure.
    """
    source = os.environ.get("INVPW_E2E_SOURCE", "index")
    if source == "git":
        target = f"git+{REPO_URL}@{REV}"
    else:
        target = "invisible-playwright"
    _run([str(clean_venv), "-m", "pip", "install", "--no-cache-dir", target],
         timeout=900)

    # NOT followed by a manual `pip install playwright==<pin>`. It used to be,
    # and that line was hiding the thing this test exists to find: if the
    # declared range resolves to a client the shipped Juggler cannot speak,
    # installing the blessed pin afterwards repairs the venv and the test goes
    # green while every real user is broken. What a user gets is what pip
    # resolves from the declaration, so that is what is asserted.
    out = _run(
        [str(clean_venv), "-c",
         "import invisible_playwright as ip; "
         "from importlib.metadata import version; "
         "print('OK', ip.__name__, version('invisible-core'), version('playwright'))"],
        timeout=60,
    )
    assert "OK invisible_playwright" in out.stdout, out.stdout

    # pip's own consistency check. It is only meaningful because the core is
    # declared as an exact version rather than a direct URL: a direct reference
    # carries no version, so there is nothing here to compare and this reports
    # clean on a broken environment.
    _run([str(clean_venv), "-m", "pip", "check"], timeout=120)


@pytest.mark.e2e
def test_the_resolved_playwright_is_inside_the_range_the_package_declares(
        clean_venv: Path):
    """What pip resolves, not what we would have chosen.

    The wrapper declares a bounded range because 1.61 added a protocol field an
    older Juggler rejects. A range whose upper end drifts past what the shipped
    binary speaks breaks `new_context` for every fresh install, and the only
    place that shows up is here - the developer's own environment has the
    blessed pin in it already.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    got = _run([str(clean_venv), "-c",
                "from importlib.metadata import version, requires; "
                "print(version('playwright')); "
                "print([r for r in requires('invisible-playwright') "
                "if r.startswith('playwright')][0])"], timeout=60)
    resolved, declaration = got.stdout.strip().splitlines()[:2]
    spec = Requirement(declaration).specifier
    assert Version(resolved) in spec, (
        f"pip resolved playwright {resolved}, which the package's own "
        f"declaration ({declaration}) does not allow - the two disagree and "
        f"the user gets the resolver's answer")


@pytest.mark.e2e
def test_version_command_reports_wrapper_and_binary(clean_venv: Path):
    """`python -m invisible_playwright --version` runs and reports both the
    wrapper version and the BINARY_VERSION it'll try to fetch."""
    out = _run(
        [str(clean_venv), "-m", "invisible_playwright", "--version"],
        timeout=30,
    )
    text = out.stdout + out.stderr
    assert "firefox-" in text, f"BINARY_VERSION not reported: {text!r}"


@pytest.mark.e2e
def test_fetch_against_live_release(clean_venv: Path, isolated_cache_env: dict):
    """Hit the LIVE GitHub release: download tarball + checksums.txt, parse,
    SHA256-verify, extract. This is the regression sentinel for #15.

    If checksums.txt is shipped in `*`-prefixed (binary) format and the parser
    keeps the `*` in the key, this raises
        RuntimeError: no SHA256 for {asset} in checksums.txt
    """
    out = _run(
        [str(clean_venv), "-m", "invisible_playwright", "fetch", "--force"],
        env=isolated_cache_env,
        timeout=900,  # 110 MB download + extract on slow connections
    )
    output = out.stdout + out.stderr
    # Anti-regression for #15: this exact string would surface if the parser
    # broke again. Spell it out so a future failure is grep-able to the issue.
    assert "no SHA256 for" not in output, (
        "Issue #15 regression: parser couldn't find SHA for the asset.\n"
        f"Output:\n{output[-2000:]}"
    )
    assert "SHA256 mismatch" not in output, (
        "Tarball SHA doesn't match the published checksums.txt - "
        "either the upload was corrupted or the release was re-packed "
        "without updating checksums.txt."
    )


@pytest.mark.e2e
def test_binary_executes_after_fetch(clean_venv: Path, isolated_cache_env: dict):
    """After fetch, the binary cache contains a launchable Firefox."""
    out = _run(
        [str(clean_venv), "-c",
         "from invisible_playwright.download import ensure_binary; "
         "p = ensure_binary(); print('BINARY', p)"],
        env=isolated_cache_env,
        timeout=60,
    )
    binary_line = [l for l in out.stdout.splitlines() if l.startswith("BINARY ")]
    assert binary_line, f"ensure_binary() didn't print path: {out.stdout!r}"
    binary_path = Path(binary_line[0].split(" ", 1)[1])
    assert binary_path.exists(), f"binary missing: {binary_path}"

    # `firefox --version` exit code is enough; output format differs across
    # platforms (Win shows nothing on stdout, Linux prints to stdout).
    # On Linux invoke via WSL when running from Windows.
    if os.name == "nt" and binary_path.suffix == "":
        # Linux binary path on Windows host - skip launch, the previous
        # ensure_binary() already proved cache landed correctly.
        pytest.skip("Cross-platform binary launch from Windows requires WSL.")
    r = subprocess.run([str(binary_path), "--version"],
                       capture_output=True, text=True, timeout=30)
    text = (r.stdout + r.stderr).lower()
    upstream = _upstream_version(clean_venv)
    assert "firefox" in text and upstream in text, (
        f"binary --version didn't report Firefox {upstream}: "
        f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}"
    )


@pytest.mark.e2e
def test_playwright_launch_against_real_site(clean_venv: Path,
                                             isolated_cache_env: dict):
    """Full stack: launch the patched Firefox via the wrapper, navigate to a
    real URL, evaluate JS. Catches Juggler protocol drift, profile-generation
    bugs, locale handling regressions, prefs typos.

    NO LONGER SKIPPED ON WINDOWS, and the reason it was is worth recording:
    the skip said "headless launch path requires display server". That is not
    true on Windows and never was - `headless=True` there keeps the real
    rendering pipeline and hides the window through the binary's own cloak
    (`zoom.stealth.cloak_windows`), so no display server is involved. Linux is
    the platform that needs one, and it gets Xvfb.

    So the primary target was the one platform this never ran on, for a stated
    reason that was false. The two most recent defects in this package were
    Windows-only by construction - firefox.exe is a launcher stub that spawns
    the real browser and exits - and neither could have been caught here.
    """

    script = (
        "from invisible_playwright import InvisiblePlaywright\n"
        "with InvisiblePlaywright(headless=True, seed=42) as browser:\n"
        "    ctx = browser.new_context()\n"
        "    page = ctx.new_page()\n"
        "    page.goto('https://example.com', timeout=30000)\n"
        "    title = page.title()\n"
        "    ua = page.evaluate('navigator.userAgent')\n"
        "    print('TITLE=' + title)\n"
        "    print('UA=' + ua)\n"
    )
    out = _run([str(clean_venv), "-c", script],
               env=isolated_cache_env, timeout=180)
    assert "TITLE=Example Domain" in out.stdout, (
        f"page.title() didn't return expected text:\n{out.stdout[-1000:]}"
    )
    major = _upstream_version(clean_venv).split(".")[0]
    assert "UA=" in out.stdout and f"Firefox/{major}" in out.stdout, (
        f"navigator.userAgent doesn't report Firefox/{major} - UA spoofing "
        f"regression?\n{out.stdout[-1000:]}"
    )


# ---------- meta: verify the test markers themselves work ------------------- #


@pytest.mark.e2e
def test_e2e_marker_is_excluded_by_default():
    """Sanity check on pyproject.toml's `addopts = '-m not e2e'` - this test
    only runs when `-m e2e` is passed explicitly. If you're reading this in
    a normal pytest run, the addopts filter is broken."""
    assert True


# ── PyPI and GitHub Releases must not drift apart ──────

@pytest.mark.e2e
def test_the_published_version_has_a_github_release():
    """Every version on the index needs a tag and a release carrying it.

    Added 2026-07-26, when the three packages had been on PyPI for a day with
    ZERO tags and ZERO releases between them. Not cosmetic: the release page is
    where a reader looks for what changed, `git describe` has nothing to say,
    and there is no commit anybody can point at as "this is the source of the
    version you have".

    NO VENV, deliberately. The first version of this took `clean_venv` and read
    the version out of the installed package, which made it depend on ANOTHER
    test in the same file having installed it first - it passed alone in the one
    repository whose fixture installs, and failed in the two whose fixture does
    not. A test whose result depends on what ran before it is not measuring what
    it claims. Both facts here are public: the index says what the latest
    version is, and the releases API says whether it has one.

    One-directional on purpose. A release for a version not yet on the index is
    a normal intermediate state during a publish; an index version with no
    release is the thing that gets forgotten, because nothing downstream breaks.
    """
    import json
    import urllib.error
    import urllib.request

    with urllib.request.urlopen(
            "https://pypi.org/pypi/invisible-playwright/json", timeout=30) as resp:
        version = json.load(resp)["info"]["version"]

    url = f"https://api.github.com/repos/feder-cr/invisible_playwright/releases/tags/v{version}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            pytest.fail(
                f"the index serves invisible-playwright {version} and there is no GitHub "
                f"release tagged v{version}. Create it at the commit that built "
                f"that version - not at HEAD, which has moved on")
        if exc.code in (403, 429):
            pytest.skip(f"GitHub API rate-limited this check ({exc.code})")
        raise
    assert payload.get("draft") is False, (
        f"the release for v{version} is still a DRAFT, so nobody can see it")
    assert (payload.get("body") or "").strip(), (
        f"the release for v{version} has an empty body - a release page with no "
        f"notes is a tag with extra steps")
