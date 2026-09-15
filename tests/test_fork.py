"""The Playwright fork holds, and it does not silently dissolve.

WHY THIS GATE IS NEEDED. The way this fork dies is not noisy: it is
someone writing ``from playwright.sync_api import ...`` in a new file. The
``playwright`` package stays installable and importable - we keep it on purpose
among the dev dependencies, so we can compare the two branches - so that
line WORKS. It's just that the code that runs is no longer ours, and no
error says so. With enough lines like that, the fork becomes a dead folder
that weighs 9 MB and does nothing.

The same checks also apply to the end user: a wheel missing
``_driver/`` or ``_pw/`` installs just fine and dies on first launch.
"""

import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_playwright"
TESTS = pathlib.Path(__file__).resolve().parent

#: The vendored client's own files import themselves under their full name,
#: so they are not violations. Same for Microsoft's upstream suite, which is a
#: reference copy and should be read as-is.
ESCLUSI = ("_pw", "_driver", "playwright-upstream", "vendor")

RIGA_VIETATA = re.compile(r"^\s*(from playwright[\s.]|import playwright[\s.])")


def _files_to_check():
    for root in (SRC, TESTS):
        for f in root.rglob("*.py"):
            if any(p in ESCLUSI for p in f.parts):
                continue
            yield f


def test_no_one_imports_the_installed_playwright():
    """The line that dissolves the fork, and that raises no error."""
    violators = []
    for f in _files_to_check():
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if RIGA_VIETATA.match(line):
                violators.append("%s:%d  %s" % (f.name, n, line.strip()))
    assert not violators, (
        "these files import the INSTALLED playwright package instead of the "
        "vendored client in _pw/. It works, and that is exactly why it's a "
        "defect: the code that runs is not the one we think we are shipping.\n  "
        + "\n  ".join(violators))


def test_the_vendored_client_is_the_one_imported():
    from invisible_playwright._pw.sync_api import sync_playwright
    module = pathlib.Path(sync_playwright.__module__.replace(".", "/"))
    import invisible_playwright._pw as pw
    assert "invisible_playwright" in str(pathlib.Path(pw.__file__).resolve()), (
        "the imported client is not the one inside our package")


#: Everything the Node driver used to bring, named so its return is loud.
#: ⛔ These are PATHS AND MODULE NAMES, not prose: the point of the test below
#: is that a future change cannot quietly reintroduce any of them.
GONE = [
    "src/invisible_playwright/_driver",
    "src/invisible_playwright/_node.py",
    "src/invisible_playwright/_pw/_impl/_driver.py",
]


def test_the_node_driver_is_gone():
    """⛔ THE TESTS THAT USED TO STAND HERE ASSERTED THE OPPOSITE.

    Three of them checked that `_driver/package/cli.js`, `coreBundle.js` and
    `utilsBundle.js` existed - the fork's whole point was being able to change
    that bundle. The driver was removed on 2026-08-28, so the assertion turns
    over: what has to hold now is that none of it comes back by accident.

    It came out on evidence, not on the code looking finished: 188 e2e passed
    on BOTH transports, protocol parity on methods, parameter names, object
    types, initializer fields, events and parentage, and the realness gates
    green on the Python path for the first time.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    back = [name for name in GONE if (root / name).exists()]
    assert not back, (
        "the Node driver is back in the tree: %s. If that is deliberate, this "
        "test is the place to say so - and `THIRD_PARTY_FORK.md` has to regain "
        "the redistribution notices that went with it." % back)


def test_no_module_imports_the_removed_driver():
    """A dangling import does not fail at import time if it sits inside a
    function, which is exactly where these lived."""
    offenders = []
    for f in _files_to_check():
        # ⛔ This file names those modules on purpose - it is the one saying
        # they must not come back - so scanning itself would make the check
        # permanently red, which is the fastest way to get a gate switched off.
        if f.name == "test_fork.py":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if "_impl._driver" in text or "invisible_playwright._node" in text:
            offenders.append(f.name)
    assert not offenders, (
        "these still import something the deletion removed: %s" % offenders)


def test_the_apache_licence_the_fork_still_owes_is_shipped():
    """⛔ Apache-2.0 is not a formality: it is the condition for redistributing.

    It used to be checked on `_driver/package/{LICENSE,NOTICE,
    ThirdPartyNotices.txt}`. Those went with the code they covered, and the
    obligation did NOT go with them: `_pw/` is still Playwright's Python
    client, still carries Microsoft's copyright headers, and is still
    redistributed - which is why `pyproject.toml` still says
    `MIT AND Apache-2.0`.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    licence = root / "src" / "invisible_playwright" / "_pw" / "LICENSE"
    assert licence.is_file(), (
        "the Apache-2.0 licence for the vendored client is missing: the "
        "package redistributes that code and may not do so without it")
    assert "Apache License" in licence.read_text(encoding="utf-8",
                                                 errors="replace")


def test_nothing_declares_a_node_version_any_more():
    """The version used to be declared exactly once, in `_node.py`. Now it is
    declared nowhere, and a number that reappears means a downloader did."""
    import re as _re

    offenders = []
    for f in _files_to_check():
        if f.name == "test_fork.py":
            continue
        if _re.search(r"NODE_VERSION|nodejs\.org", f.read_text(
                encoding="utf-8", errors="replace")):
            offenders.append(f.name)
    assert not offenders, (
        "something is naming a Node version or nodejs.org again: %s. Nothing "
        "in this package downloads or runs node since 2026-08-28." % offenders)


def _injected_sources():
    """The strings the bundle injects into the page, one per physical line.

    They are declared as ``sourceN = '...'`` with SINGLE quotes and with newlines
    written as two characters, so each one occupies a single line of the file.
    """
    # ⛔ THE BUNDLE MAY NOT BE HERE ANY MORE, and that is not a broken tree:
    # the driver was removed on 2026-08-28. The defect this scans for lives in
    # a SINGLE-QUOTED JavaScript string inside that bundle, so with the bundle
    # gone the class is gone with it - our `injected.js` is a plain file, where
    # an apostrophe in a comment is just an apostrophe.
    #
    # It is kept reachable rather than deleted because the check still applies
    # to any bundle somebody points at: `INVPW_DRIVER_TREE` from a git worktree,
    # or a fresh upstream one being considered for extraction.
    import os

    tree = os.environ.get("INVPW_DRIVER_TREE")
    root = (pathlib.Path(tree) if tree
            else pathlib.Path(__file__).resolve().parent.parent)
    bundle = (root / "src" / "invisible_playwright" / "_driver" / "package"
              / "lib" / "coreBundle.js")
    if not bundle.is_file():
        return
    text = bundle.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*(source\d*) = '", line)
        if m:
            yield number, m.group(1), line




def _unprotected_quote(line):
    """Where the single-quoted string closes before the end, or None.

    Returns the text that follows the closing quote, so whoever reads the
    failure immediately sees what was left outside the string.
    """
    backslash = chr(92)
    body = line[line.index("'") + 1:]
    k = 0
    while k < len(body):
        if body[k] == backslash:
            k += 2
            continue
        if body[k] == "'":
            rest = body[k + 1:].strip()
            return None if rest in (";", "", ");") else rest[:80]
        k += 1
    return "the string never closes at all"


def test_the_injected_sources_have_balanced_quotes():
    """An apostrophe in a comment closes the string and breaks the whole bundle.

    Happened on 2026-08-24: an Italian comment added inside ``source4``
    contained ``piu'`` and ``cioe'``. The string closed right there, the file
    became invalid JavaScript and the driver stopped starting at all.

    The defect is not visible to the eye - those lines are hundreds of
    thousands of characters long - and no test that only imports the Python
    package sees it. No Node required: it holds even where the runtime has not
    been downloaded yet.
    """
    found = list(_injected_sources())
    if not found:
        # ⛔ NOT AN ASSERTION FAILURE. With no bundle reachable there is
        # nothing of this shape to check, and the defect class went with it -
        # see `_injected_sources`. Saying "no injected source found" as a
        # failure would turn a deliberate deletion into a permanent red.
        pytest.skip("no driver bundle reachable: nothing of this shape exists "
                    "to check. Point INVPW_DRIVER_TREE at a worktree to run "
                    "it against one.")
    for number, name, line in found:
        rest = _unprotected_quote(line)
        assert rest is None, (
            "coreBundle.js:%d, %s: the injected string closes too early, "
            "what remains after it is %r - almost always an apostrophe inside a "
            "comment" % (number, name, rest))


def test_the_quote_check_sees_a_real_apostrophe():
    """The known-bad mutation, on the exact form that broke the bundle.

    ⛔ THE ITALIAN IN THE THREE FIXTURES BELOW IS DELIBERATE AND MUST NOT BE
    TRANSLATED. The defect IS an Italian word written with a trailing
    apostrophe - `piu'` - inside a JavaScript comment: that apostrophe closes
    the single-quoted string the comment lives in, and the rest of the bundle
    becomes invalid JavaScript. Replace it with English and the mutation stops
    reproducing the bug, so this test would go green on an input that can no
    longer fail - a gate that checks nothing while still printing PASS.

    This is why `[tool.invisible.english]` in `pyproject.toml` names this one
    file in its exclusion list, for the shared gate `invisible_core.english`.
    The exemption is one path, never a folder: a sibling test written in
    Italian by accident is still caught.
    """
    good = "    source4 = '\nmarkTargetElements() {\n  // non dispatcha niente\n}';"
    assert _unprotected_quote(good) is None
    bad = "    source4 = '\nmarkTargetElements() {\n  // non dispatcha piu' niente\n}';"
    assert _unprotected_quote(bad) is not None
    never_closed = "    source4 = '\nqualcosa senza fine"
    assert _unprotected_quote(never_closed) == "the string never closes at all"
