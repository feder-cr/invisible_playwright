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

# The variables that must NOT cross into a venv this file builds.
#
# A venv inherits the environment, and these two walk straight through it into
# the thing under test. Measured 2026-08-11:
#
#   PYTHONPATH           pointing at the workbench sources makes the venv import
#                        the workbench core instead of the installed one. 5
#                        failures here and 2 in test_upgrade_e2e.py on Linux,
#                        every one of them "requires invisible-core==18.13.0 /
#                        installed 18.14.0". The product was not involved.
#   INVISIBLE_SEAL_FILE  pointing at a LOCAL seal makes the four tests that
#                        download the published release fail with "the active
#                        seal is a LOCAL seal with no published assets". 4
#                        failures, on Windows AND Linux, that went away by
#                        re-running the same file without it: 9 of 9 passed.
#   PYTHONHOME           same mechanism as PYTHONPATH and worse - it redirects
#                        the standard library, so the venv python is broken
#                        before it runs a line. Not measured here; added because
#                        it cannot be right and would be unreadable if it hit.
#
# Sixteen failures in one day, none of them the product. And the dangerous
# direction is the other one: a GREEN that comes from an environment no user
# has. These tests are the only thing that checks the install path, so their
# verdict must not depend on the shell that launched them.
_NON_ERMETICHE = ("PYTHONPATH", "INVISIBLE_SEAL_FILE", "PYTHONHOME")


def _clean_env(base=None):
    """L'ambiente da passare a un sottoprocesso, senza le variabili che filtrano.

    Torna anche cosa ha tolto, cosi' il messaggio di errore puo' dirlo: una
    variabile rimossa in silenzio e' la stessa classe di difetto di una
    ereditata in silenzio, solo con il segno cambiato.
    """
    env = dict(os.environ if base is None else base)
    tolte = [k for k in _NON_ERMETICHE if k in env]
    for k in tolte:
        env.pop(k, None)
    return env, tolte


# ── venv mechanics, LOCAL ON PURPOSE ────────────────────────────────────────
# These three live in `invisible_core.testing` and every other suite that
# shares the core imports them from there - three repos when this was written,
# two (this package and invisible_core) since invisible_firefox was deleted on
# 2026-08-18. NOT this file. The job that runs it installs pytest on the
# runner and nothing else, deliberately: what is being tested is what a
# stranger gets from the index, and a second copy of anything on the runner's
# path would mean the assertions read the wrong one.
#
# So `from invisible_core.testing import ...` here is a COLLECTION error -
# ModuleNotFoundError, whole job red, nothing tested. It happened on every leg
# of all three repos on 2026-07-28, the day the helpers were shared, and it
# passes on any development machine because an editable install is always on
# the path. The core's suite now parses these files and refuses the import, so
# the rule does not depend on this comment being read.
def _run(cmd, *, timeout: int = 600, check: bool = True, env=None, cwd=None):
    # `env=None` significava EREDITA, che e' esattamente il difetto. Adesso il
    # default e' l'ambiente ripulito; passare `env=` esplicitamente lo ripulisce
    # comunque, perche' un chiamante che costruisce un ambiente parte quasi
    # sempre da `os.environ`.
    env, tolte = _clean_env(env)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout, env=env,
                       cwd=str(cwd) if cwd is not None else None)
    if check and r.returncode != 0:
        nota = ""
        if tolte:
            nota = ("\n--- ambiente ---\nrimosse prima di lanciare: {}\n"
                    "(sono le variabili che entrerebbero nel venv e lo farebbero "
                    "misurare qualcosa che non e' cio' che riceve un utente)"
                    .format(", ".join(tolte)))
        raise AssertionError(
            "{} exited {}\n--- stdout ---\n{}\n--- stderr ---\n{}{}".format(
                " ".join(str(c) for c in cmd), r.returncode,
                r.stdout[-3000:], r.stderr[-3000:], nota))
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
    # `_clean_env()` e non l'ambiente ereditato: questa e' la lettura che decide
    # quale COPIA del pacchetto risponde, ed e' proprio quella che `PYTHONPATH`
    # dirotta sui sorgenti del banco.
    out = subprocess.run(
        [str(py), "-c",
         "from invisible_playwright.constants import FIREFOX_UPSTREAM_VERSION as v; print(v)"],
        capture_output=True, text=True, timeout=60, env=_clean_env()[0])
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
        # NOT `fetch --force`: that flag went with four of the six subcommands
        # in 0.5.0, and this line kept it, so every run of this file exited 2 on
        # "unrecognized arguments" while the default suite stayed green - the test
        # is e2e-marked, so only the two workflows that actually run it saw the
        # red. One stale argument, three red workflows.
        #
        # Nothing is lost by dropping it. `fetch` verifies every cached tree
        # against the seal on every run, which is what --force was for, and this
        # test builds a fresh venv with an isolated cache root, so there is no
        # warm tree to force past.
        [str(clean_venv), "-m", "invisible_playwright", "fetch"],
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
    # Anche qui `_clean_env()`, e non perche' Firefox legga `PYTHONPATH`: non lo
    # legge. Perche' cosi' in questo file NON c'e' un solo sottoprocesso lanciato
    # con l'ambiente ereditato, quindi non c'e' un'eccezione da ricordare ne' un
    # esempio da ricopiare. Costa niente.
    r = subprocess.run([str(binary_path), "--version"],
                       capture_output=True, text=True, timeout=30,
                       env=_clean_env()[0])
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
    """EVERY version on the index needs a tag and a release carrying it.

    Added 2026-07-26, when the three packages had been on PyPI for a day with
    ZERO tags and ZERO releases between them. Not cosmetic: the release page is
    where a reader looks for what changed, `git describe` has nothing to say,
    and there is no commit anybody can point at as "this is the source of the
    version you have".

    IT CHECKED ONE VERSION UNTIL 2026-08-02. It read `info.version` - the latest
    - so the moment a release was published without its page, the next release
    made the omission invisible: the check moved on to the new version and the
    old gap stayed behind it. Measured that day: ELEVEN published versions across
    the three packages had no release, and eight had no tag at all, three of them
    published AFTER the backfill this test was written to protect. The docstring
    said "every version" from the first line while the code checked one, which is
    the same defect this project found three times in two days - a claim written
    beside the code rather than from it.

    It now walks the whole index. That is one API call per version, ~36 across
    the three packages, and it runs in the install-e2e job rather than the unit
    suite for exactly that reason.

    NO VENV, deliberately. The first version of this took `clean_venv` and read
    the version out of the installed package, which made it depend on ANOTHER
    test in the same file having installed it first - it passed alone in the one
    repository whose fixture installs, and failed in the two whose fixture does
    not. A test whose result depends on what ran before it is not measuring what
    it claims. Both facts here are public: the index says which versions exist,
    and the releases API says whether each has one.

    One-directional on purpose. A release for a version not yet on the index is
    a normal intermediate state during a publish; an index version with no
    release is the thing that gets forgotten, because nothing downstream breaks.

    Yanked versions are skipped: a yanked release is one the project has
    withdrawn, and demanding a release page for something nobody should install
    would be asking for the opposite of what the yank said.
    """
    import json
    import os
    import urllib.error
    import urllib.request

    with urllib.request.urlopen(
            "https://pypi.org/pypi/invisible-playwright/json", timeout=30) as resp:
        index = json.load(resp)["releases"]

    live = sorted((version for version, files in index.items()
                   if files and not all(f.get("yanked") for f in files)),
                  key=lambda v: tuple(int(p) for p in v.split(".")))
    assert live, "the index serves no usable version of invisible-playwright"

    # Authenticated when a token happens to be around, which on a GitHub runner
    # it always is. Unauthenticated the API allows 60 calls an hour PER IP, and
    # this walk wants one per version across three repositories: measured
    # 2026-08-02, the walk hit 403 on its eleventh call. The token is optional,
    # never required, and never printed.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    missing, drafts, empty = [], [], []
    checked = 0
    cut_short = None
    for version in live:
        url = ("https://api.github.com/repos/feder-cr/invisible_playwright"
                   f"/releases/tags/v{version}")
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                missing.append(version)
                checked += 1
                continue
            if exc.code in (403, 429):
                # NOT an unconditional skip. The first version of this walk
                # skipped here, which threw away every violation already in
                # hand: the mutation that deleted an OLD release passed, because
                # a rate-limit two versions later abandoned the whole test. A
                # gate that discards its own findings on an unrelated error is
                # worse than no gate, because it reports PASS.
                cut_short = (version, exc.code)
                break
            raise
        checked += 1
        if payload.get("draft") is not False:
            drafts.append(version)
        if not (payload.get("body") or "").strip():
            empty.append(version)

    problems = []
    # The LEDGER is the fourth record, and it costs no API call: it is a file in
    # this repository. `version_gate check` compares it to the index too, but
    # only on a release - so a version published outside the gate stays invisible
    # until the NEXT release is refused by it, which is how the gap from
    # 2026-08-02 was found. Here it surfaces on every run of this job.
    ledger_path = Path(__file__).resolve().parents[1] / "PUBLISHED.json"
    recorded = {}
    if ledger_path.is_file():
        recorded = {e["version"]: e for e in
                    json.loads(ledger_path.read_text(encoding="utf-8"))["released"]}

    # ⛔ LA VERSIONE CHE QUESTO ALBERO DICHIARA E' ESENTE, e solo lei.
    #
    # Il registro non puo' arrivare su main insieme alla pubblicazione: publish.yml
    # pubblica su PyPI e POI prova a spingere PUBLISHED.json, ma main e' protetto e
    # la spinta diretta viene rifiutata sempre, per costruzione. La voce finisce su
    # un ramo ledger/<versione> la cui PR resta bloccata da sola ([B136]), quindi
    # fra la pubblicazione e il merge a mano l'indice ha la versione e il registro
    # no. Senza questa riga il caso e' ROSSO su un rilascio andato a buon fine, ed
    # e' la QUARTA occorrenza della stessa forma trovata il 2026-08-18: un
    # controllo scritto guardando lo stato a regime che diventa rosso esattamente
    # quando serve.
    #
    # L'esenzione non apre un buco permanente, e la ragione e' che si richiude da
    # sola. Se quel ramo del registro non viene mai unito, al rilascio successivo
    # la versione rimasta indietro non e' piu' quella dichiarata dall'albero:
    # ricade in unrecorded e il caso torna rosso. E' lo stesso modello che il
    # commento qui sopra descrive per version_gate - una pubblicazione fuori dal
    # cancello resta invisibile fino al rilascio dopo, che la rifiuta.
    import tomllib
    in_volo = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml")
        .read_text(encoding="utf-8"))["project"]["version"]

    unrecorded = [v for v in live if recorded and v not in recorded and v != in_volo]
    thin = {v: [k for k in ("published_at", "requires_dist", "wheel_filename",
                            "sdist_filename", "wheel", "sdist")
                if k not in recorded[v]]
            for v in live if v in recorded}
    thin = {v: missing for v, missing in thin.items() if missing}

    if unrecorded:
        problems.append(
            f"on the index and NOT in PUBLISHED.json: {unrecorded}. Those were "
            f"published outside the gate, so nothing records what shipped under "
            f"them; back-fill from the artifacts the index serves, never from the "
            f"tree, which has moved on")
    if thin:
        problems.append(f"ledger entries missing fields: {thin}")

    if missing:
        problems.append(
            f"on the index with NO release: {missing}. Create each at the commit "
            f"that built that version - not at HEAD, which has moved on. PyPI's "
            f"upload_time and the commit carrying the version are how to find it")
    if drafts:
        problems.append(f"still a DRAFT, so nobody can see it: {drafts}")
    if empty:
        problems.append(f"empty body, which is a tag with extra steps: {empty}")

    assert not problems, (
        f"invisible-playwright has {len(live)} usable versions on the index, {checked} were "
        f"checked, and " + "; ".join(problems))

    if cut_short:
        version, code = cut_short
        if checked == 0:
            # "Green because it never ran" is the shape this project met three
            # times in two days. A skip that reads "all clean so far" after
            # checking NOTHING is that shape wearing a pass.
            pytest.skip(f"GitHub API answered {code} on the FIRST call, so NONE "
                        f"of {len(live)} versions were checked and this proves "
                        f"nothing. Set GITHUB_TOKEN to raise the rate limit")
        pytest.skip(f"GitHub API answered {code} at v{version} after {checked} of "
                    f"{len(live)} versions, clean up to there. Set GITHUB_TOKEN to "
                    f"raise the rate limit")


@pytest.mark.e2e
def test_the_changelog_documents_every_version_it_claims_to_cover():
    """A changelog that stops four releases ago is worse than none.

    Found 2026-08-02 with the file sitting at [0.4.7] while 0.4.8, 0.4.9, 0.5.0
    and 0.6.0 were on the index - and 0.5.0 is the release that cut the CLI from
    six commands to two, which is the single most breaking change this package
    has shipped. A reader upgrading across it had nothing to read.

    This is the sibling of the release-page walk above and the same defect
    shape: a record that is written by hand, consulted by users, and gated by
    nothing drifts silently, because nothing downstream breaks when it does.

    Bounded from BELOW by the file itself. The changelog does not reach back to
    the first release and does not need to; what it must not do is stop short of
    the present. So the rule is: every version on the index at or above the
    oldest one documented must have a heading, and no heading may name a version
    the index has never served.

    Dates are compared too, because a heading dated by the person writing it
    rather than by the upload is a small lie that a reader can check: this file
    dated 0.6.0 a day late on its first reconstruction, and PyPI is the record
    that settles it.
    """
    import json
    import re
    import urllib.request

    changelog = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(
        encoding="utf-8")
    documented = {m.group(1): m.group(2) for m in re.finditer(
        r"(?m)^##\s*\[(\d+\.\d+\.\d+)\]\s*-\s*(\d{4}-\d{2}-\d{2})\s*$", changelog)}
    assert documented, "no `## [X.Y.Z] - YYYY-MM-DD` heading found at all"

    with urllib.request.urlopen(
            "https://pypi.org/pypi/invisible-playwright/json", timeout=30) as resp:
        releases = json.load(resp)["releases"]
    published = {v: min(f["upload_time_iso_8601"] for f in files)[:10]
                 for v, files in releases.items() if files}

    def key(v):
        return tuple(int(p) for p in v.split("."))

    floor = min(documented, key=key)
    expected = {v for v in published if key(v) >= key(floor)}

    # A documented version the index never served is not automatically wrong:
    # this package had nine releases on GitHub before it went to PyPI on
    # 2026-07-26, and their entries are real history. Only a heading ABOVE the
    # first published version can be an invention - below it, the index simply
    # has nothing to say. The first draft of this gate flagged all fifteen.
    first_published = min(published, key=key)

    # La versione che QUESTO albero dichiara e' il rilascio IN CORSO, non
    # un'invenzione. Senza questa riga il gate rendeva impossibile una PR di
    # rilascio verde: la voce del changelog deve stare nel commit che alza la
    # versione, l'indice non la serve ancora, e il gate la chiamava inventata.
    # Misurato il 2026-08-18 sulla PR #80, dove era l'UNICO rosso rimasto e
    # nessuna sequenza di passi poteva evitarlo. Togliere la voce per far tacere
    # il gate avrebbe ricreato esattamente il buco che questo gate esiste per
    # impedire, e che aveva gia' lasciato 0.7.1 senza voce per un giorno.
    # Ogni ALTRA versione documentata e mai pubblicata resta un errore, e le
    # date restano confrontate: quando il rilascio atterra, la sua data deve
    # coincidere con quella dell'upload come per tutte le altre.
    import tomllib
    in_volo = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml")
        .read_text(encoding="utf-8"))["project"]["version"]

    missing = sorted(expected - set(documented), key=key)
    invented = sorted((v for v in set(documented) - set(published)
                       if key(v) > key(first_published) and v != in_volo), key=key)
    misdated = sorted((v, documented[v], published[v]) for v in
                      set(documented) & set(published) if documented[v] != published[v])

    problems = []
    if missing:
        problems.append(
            f"published and undocumented: {missing}. The changelog starts at "
            f"{floor}, so everything from there up is in scope")
    if invented:
        problems.append(f"documented and never published: {invented}")
    if misdated:
        problems.append("dated differently from the upload: " + ", ".join(
            f"{v} says {said} the index says {real}" for v, said, real in misdated))
    assert not problems, "CHANGELOG.md: " + "; ".join(problems)
