"""Extracts the injected script from the driver bundle into
`_juggler/injected.js`.

WHY WE EXTRACT AND DO NOT REWRITE. That JavaScript is the selector engines
(css, xpath, text, role, testid, label), actionability, the ARIA snapshot and
`expect`: thousands of lines of subtle DOM logic that **runs in the page**
and that no Python rewrite could replace, because Python is not in the page.
It is not work to redo: it is cargo to carry over.

⛔ BUT IT IS NOT VIRGIN UPSTREAM. It already carries our own fixes -
`markTargetElements` emptied out, `__pwClock` read via a descriptor,
`Symbol.hasInstance` captured, and since 2026-08-27 listener installs
guarded by `_isUtilityWorld`. Extracting it from a bundle other than THIS
one loses all of them, silently. See `31-client-fork.md` §3.

⛔ AND IT IS NOT YET TRIMMED. The chosen perimeter
(`32-stacco-da-playwright.md` §1) leaves out the ARIA snapshot,
`locatorGenerators`, `highlight` and `consoleApi`, that is **87,468 bytes
out of 311,365**. Here we extract the WHOLE thing: the trim is a step of
its own, and it has to be done on the real module boundaries, which in the
emitted bundle **are not boundaries** (§3.3).

    python scripts/gen_injected_source.py            (extracts)
    python scripts/gen_injected_source.py --check    (regenerates and compares)
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ANCHOR = "source4 = '"


def extract(bundle: str) -> str:
    """The value of the `source4` string, de-escaped.

    ⛔ It is a SINGLE-QUOTE string on ONE physical line of three hundred
    thousand characters. It is scanned by hand respecting backslashes: a
    greedy regex would grab all the way to the last quote in the FILE, and
    a lazy one would stop at the first apostrophe inside a comment.
    """
    i = bundle.index(ANCHOR) + len(ANCHOR)
    bs = chr(92)
    j = i
    n = len(bundle)
    while j < n:
        if bundle[j] == bs:
            j += 2
            continue
        if bundle[j] == "'":
            break
        j += 1
    else:
        raise SystemExit("the source4 string never closes: corrupted bundle?")
    raw = bundle[i:j]
    # De-escape: the string is JavaScript, but the escapes it uses are the
    # ones `unicode_escape` understands. We go through latin-1 so as not to
    # break the high bytes.
    return raw.encode("latin-1", "backslashreplace").decode("unicode_escape")


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(
        here / "src" / "invisible_playwright" / "_driver" / "package" / "lib" / "coreBundle.js"))
    ap.add_argument("--out", default=str(
        here / "src" / "invisible_playwright" / "_juggler" / "injected.js"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    # ⛔ A MISSING BUNDLE IS A STATE, NOT A CRASH. Since the Node driver was
    # removed there may be no bundle in the tree at all, and the extracted
    # `injected.js` is COMMITTED - it stays valid without one. What is gone is
    # the ability to RE-DERIVE it, and letting that arrive as
    # `FileNotFoundError` from a path deep in an argparse default sends the
    # reader looking for a broken script instead of a deleted folder.
    named = os.environ.get("INVPW_DRIVER_BUNDLE")
    path = pathlib.Path(named) if named else pathlib.Path(a.bundle)
    if not path.exists():
        print("NOT COMPARABLE: no driver bundle at %s." % path)
        print("  `injected.js` is committed and still valid - what is gone is "
              "the ability to re-derive it.")
        print("  To get one back: git worktree add <dir> <the last commit that "
              "carried _driver/>, then")
        print("  INVPW_DRIVER_BUNDLE=<dir>/src/invisible_playwright/_driver/"
              "package/lib/coreBundle.js")
        return 2

    bundle = path.read_bytes().decode("utf-8", "replace")
    source = extract(bundle)

    # Proof that what we extracted is REALLY the injected script, not some
    # random string that happens to start the same way. A check on what it
    # MUST contain costs nothing and stops us from shipping the wrong blob.
    EXPECTED = ("InjectedScript", "internal:role", "internal:testid",
                "_setupHitTargetInterceptors", "createRoleEngine")
    #
    # ⛔ `_setupHitTargetInterceptors` IS STILL THE RIGHT FINGERPRINT even
    # though the shipped `injected.js` no longer has it. This tuple describes the
    # bundle we EXTRACT FROM, which is upstream's and still carries it; our
    # removal happens after. Do not delete the name because a grep for removed
    # symbols lands here.
    #
    # What catches a regeneration that quietly brings the mechanism back is
    # `tests/test_injected_page_surface.py`: constructing the InjectedScript
    # would touch the page's window again, and the gate goes red.
    missing = [x for x in EXPECTED if x not in source]
    if missing:
        raise SystemExit("the extract does not look like the injected script: "
                         "missing %s" % ", ".join(missing))
    # And that it carries OUR fixes: an upstream bundle would lose all of
    # them without anything raising an error.
    #
    # NOTE: the marker below is a LITERAL, not prose. It has to match the
    # actual bytes of the comments `injected.js` carries, so it is compared
    # exactly and never reworded. It was `MODIFICATO da invisible_playwright`
    # until 2026-08-27, when the whole repository moved to English.
    if "MODIFIED by invisible_playwright" not in source:
        raise SystemExit("the extract does NOT carry invisible_playwright's "
                         "changes: it is an upstream bundle, not ours")

    print("extracted: %d bytes, %d lines" % (len(source.encode("utf-8")),
                                              source.count(chr(10))))
    print("  our changes marked: %d"
          % source.count("MODIFIED by invisible_playwright"))

    out = pathlib.Path(a.out)
    new_bytes = source.encode("utf-8")
    if a.check:
        if not out.is_file():
            print("MISSING: %s" % out)
            return 1
        if out.read_bytes() == new_bytes:
            print("INJECTED SCRIPT ALIGNED")
            return 0
        print("DRIFT: the extract does not match the file in the tree")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(new_bytes)
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
