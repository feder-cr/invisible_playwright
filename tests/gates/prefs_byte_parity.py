"""The two writers must produce the SAME `user.js`, byte for byte.

⛔ THIS IS THE CRITERION ITEM 1 WAS WRITTEN WITH, and it is stricter than
"both work" on purpose. Two prefs files can each be valid, each configure the
browser, and differ - a header comment, a different order, an integer written
as a string. Every one of those differences is invisible in a running browser
and every one of them means the Python path is configuring a browser the Node
path never configured. The whole point of keeping the driver as a judge is that
this comparison is possible; a looser check throws that away.

⛔ AND IT NEEDS NO BROWSER. Both writers are pure functions of the prefs dict:
the driver's is a JavaScript literal builder inside `coreBundle.js`, ours is
`invisible_core.launch.write_user_js`, the one writer since 0.30.0. The driver's rules are re-implemented here FROM ITS SOURCE,
read at run time rather than copied - so if upstream's writer changes, this gate
changes with it instead of silently comparing against a stale transcription.

    python tests/gates/prefs_byte_parity.py
    python tests/gates/prefs_byte_parity.py --selftest   # 6 mutations, 3 that must not fire
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

BUNDLE = ROOT / "src/invisible_playwright/_driver/package/lib/coreBundle.js"

#: A prefs dict shaped like a real one: the four value kinds that matter, a
#: name with a character JSON has to escape, and a deliberately unsorted order.
#: ⛔ THE ORDER IS PART OF THE FIXTURE. The driver uses `Object.keys()`, so a
#: writer that sorts produces a file that is equally correct and not identical,
#: and that is precisely the class of difference this gate exists to catch.
SAMPLE = {
    "zoom.stealth.screen.width": 1920,
    "app.update.auto": False,
    "layout.css.devPixelsPerPx": "1.25",
    "ui.textScaleFactor": 1.0,
    "general.useragent.override": 'Mozilla/5.0 "quoted" \\ backslash',
    "browser.startup.homepage": "about:blank",
}


class DriverRulesMissing(RuntimeError):
    pass


#: The frozen copy, with its provenance. See the file itself for why.
FROZEN = pathlib.Path(__file__).resolve().parent / "driver_prefs_rules.json"

#: Where to find a bundle after the deletion: a git worktree at the last commit
#: that carried `_driver/`. Named here so the gate can say it out loud instead
#: of leaving the reader to work it out.
BUNDLE_ENV = "INVPW_DRIVER_BUNDLE"


def _bundle():
    """A reachable bundle, or None. Never raises: absence is a state."""
    named = os.environ.get(BUNDLE_ENV)
    if named:
        candidate = pathlib.Path(named)
        return candidate if candidate.exists() else None
    return BUNDLE if BUNDLE.exists() else None


def driver_rules() -> dict:
    """The driver's literal rules: frozen, and re-checked whenever possible.

    ⛔ IT USED TO READ THE BUNDLE ON EVERY RUN, deliberately, so an upstream
    change moved this gate with it instead of leaving it comparing against a
    stale transcription. The bundle goes with the Node driver, so the rules are
    now RECORDED - in `driver_prefs_rules.json`, with the sha of the bundle and
    of the exact 1200-byte window they came from.

    ⛔ AND THE RE-CHECK IS THE WHOLE POINT OF FREEZING THEM THIS WAY. Whenever a
    bundle IS reachable - still in the tree, or named by `INVPW_DRIVER_BUNDLE`
    from a git worktree - the rules are extracted again and compared against the
    frozen copy, and a mismatch FAILS. A recorded fact nobody can re-derive is a
    transcription, and a transcription this gate cannot check is exactly what
    the original design refused to have.
    """
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))["rules"]
    bundle = _bundle()
    if bundle is None:
        return frozen
    live = _extract(bundle.read_text(encoding="utf-8", errors="replace"))
    if live != frozen:
        raise DriverRulesMissing(
            "the frozen driver rules disagree with the bundle at %s.\n"
            "  frozen: %s\n  bundle: %s\n"
            "The frozen copy is stale: re-freeze it with the new provenance, "
            "rather than loosening this comparison." % (bundle, frozen, live))
    return frozen


def _extract(text: str) -> dict:
    start = text.find("__ipwLit")
    if start < 0:
        raise DriverRulesMissing(
            "the driver's pref writer is not in the bundle: it was renamed or "
            "removed, and this gate cannot compare against something it "
            "cannot read")
    window = text[start:start + 1200]
    return {
        "sorts": "sort()" in window,
        "header": bool(re.search(r'"//[^"]*"\s*\+', window)),
        "separator": "String.fromCharCode(10)" in window or "\\n" in window,
        "integer_bare": "Number.isInteger" in window,
    }


def driver_user_js(prefs: dict) -> str:
    """What the driver writes, in Python, following the rules read above."""
    rules = driver_rules()
    names = sorted(prefs) if rules["sorts"] else list(prefs)
    lines = []
    for name in names:
        value = prefs[name]
        if isinstance(value, bool):
            literal = "true" if value else "false"
        elif isinstance(value, int):
            literal = str(value)
        elif isinstance(value, float):
            # ⛔ Not an integer, so the driver quotes it: Gecko has no float
            # pref type, and a bare fraction makes the parser fail and IGNORE
            # EVERY LINE AFTER IT.
            literal = json.dumps(str(value))
        else:
            literal = json.dumps(str(value))
        lines.append("user_pref(%s, %s);" % (json.dumps(name), literal))
    head = "// header\n" if rules["header"] else ""
    return head + "\n".join(lines) + "\n"


def ours(prefs: dict) -> str:
    from invisible_core.launch import write_user_js
    directory = tempfile.mkdtemp(prefix="prefs_parity_")
    write_user_js(directory, prefs)
    return (pathlib.Path(directory) / "user.js").read_bytes().decode("utf-8")


def compare(prefs: dict) -> list:
    theirs = driver_user_js(prefs)
    mine = ours(prefs)
    if theirs == mine:
        return []
    faults = []
    a, b = theirs.splitlines(), mine.splitlines()
    if len(a) != len(b):
        faults.append("line count: driver %d, ours %d" % (len(a), len(b)))
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            faults.append("line %d:\n    driver: %s\n    ours  : %s"
                          % (i + 1, x, y))
    if not faults:
        faults.append("same lines, different bytes: trailing newline or "
                      "encoding")
    return faults


# ── the selftest ────────────────────────────────────────────────────────────
def selftest() -> int:
    bad = 0

    def expect(name, produce, should_fire):
        nonlocal bad
        theirs = driver_user_js(SAMPLE)
        mine = produce(theirs)
        differs = theirs != mine
        if differs != should_fire:
            print("  %s: %s" % ("SURVIVED" if should_fire else "FALSE POSITIVE",
                                name))
            bad += 1
        else:
            print("  %s: %s" % ("killed" if should_fire else "silent", name))

    print("--- mutations that MUST fire ---")
    expect("a header comment is added",
           lambda t: "// written by us\n" + t, True)
    expect("the lines are sorted",
           lambda t: "\n".join(sorted(t.splitlines())) + "\n", True)
    expect("an integer is quoted",
           lambda t: t.replace("user_pref(\"zoom.stealth.screen.width\", 1920)",
                               "user_pref(\"zoom.stealth.screen.width\", \"1920\")"),
           True)
    expect("a boolean becomes a string",
           lambda t: t.replace(", false)", ", \"false\")"), True)
    expect("the trailing newline is dropped",
           lambda t: t.rstrip("\n"), True)
    expect("one pref goes missing",
           lambda t: "\n".join(t.splitlines()[1:]) + "\n", True)

    print("--- cases that must NOT fire ---")
    expect("the same text", lambda t: t, False)
    expect("the same text built twice", lambda t: driver_user_js(SAMPLE), False)
    expect("a float stays quoted",
           lambda t: t.replace('"1.0"', '"1.0"'), False)

    print()
    print("selftest: %s" % ("ALL GOOD" if not bad else "%d PROBLEMS" % bad))
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    try:
        rules = driver_rules()
    except DriverRulesMissing as failure:
        print("NOT COMPARABLE: %s" % failure)
        return 2
    # ⛔ IT SAYS WHICH SOURCE IT USED. The line used to read "driver rules read
    # from the bundle" unconditionally, and after the freeze that sentence was
    # simply false half the time - a gate that misreports where its own
    # reference came from is worse than one with no message, because the reader
    # stops looking.
    bundle = _bundle()
    print("driver rules %s: %s"
          % ("re-checked against %s" % bundle if bundle
             else "from the FROZEN copy (%s) - no bundle reachable, so nothing "
                  "re-derived them this run" % FROZEN.name,
             ", ".join("%s=%s" % kv for kv in sorted(rules.items()))))
    faults = compare(SAMPLE)
    if not faults:
        print("PREFS BYTE PARITY: identical (%d prefs)" % len(SAMPLE))
        return 0
    print("the two writers DISAGREE:")
    for f in faults:
        print("   " + f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
