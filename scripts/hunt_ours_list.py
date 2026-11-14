"""HUNT SCAFFOLDING, NEVER MERGED: the list of what exists only in our engine, taken from the engine's source tree (READ ONLY: git diff / git show / git grep).

Usage: python scripts/hunt_ours_list.py SOURCE_TREE [BASE_TAG] > scripts/hunt_ours_list.json

SOURCE_TREE is a Firefox tree whose HEAD is our engine (C:\\ff\\source, branch port/159-phase4) and BASE_TAG the Mozilla tag it is a patch series on
(default FIREFOX_159_0_RELEASE). The output is JSON:

  added      files our series adds (not tests)                 -> every function in them is ours
  modified   files our series changes (not tests)
  hooks      file -> the functions that hold a STEALTH-HOOK marker (the nearest definition above the marker line)
  hunks      file -> the functions named in the hunk headers of `git diff -U0` (the code our series touched inside an upstream file)
  symbols    "Class::Method" / "function name" qualified names found in added files, hooks and hunks (what a label frame of the profiler or a symbolicated
             native frame can be matched against)
  hook_files files carrying a STEALTH-HOOK marker

The profiler's frames of JS carry a file name (matched against added / modified / hook_files by base name); the label frames of C++ carry a qualified name
(matched against symbols); an unsymbolicated native frame is only an address in a DLL, which can say neither.
"""
import json
import re
import subprocess
import sys

TREE = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "FIREFOX_159_0_RELEASE"
SKIP = re.compile(r"(^|/)(tests?|testing|gtest|reftests?|crashtests?|mochitest|xpcshell|browser/components/tests|docs?)(/|$)|\.(toml|ini|md|rst|txt|json|png|svg|ftl|html|xhtml|css)$")
CODE = re.compile(r"\.(cpp|cc|c|h|hpp|mm|js|jsm|mjs|rs|idl|sys\.mjs)$")
JS_FN = re.compile(r"^\s*(?:async\s+)?(?:function\s*\*?\s*([A-Za-z_$][\w$]*)|(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{)")
JS_CLASS = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")
CXX_FN = re.compile(r"^(?:[\w:<>\*&~ ,]+?\s+)?((?:[A-Za-z_]\w*::)+~?[A-Za-z_]\w*|[A-Za-z_]\w*)\s*\([^;]*$")
HUNK = re.compile(r"^@@ [^@]+ @@ ?(.*)$")
NOT_FN = {"if", "for", "while", "switch", "return", "catch", "else", "do", "sizeof", "function", "constructor"}


def git(*a, check=True):
    p = subprocess.run(["git", "-C", TREE] + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode not in (0, 1):
        raise SystemExit("git %s failed: %s" % (" ".join(a), p.stderr[:300]))
    return p.stdout


def names_in(text, path):
    """function-like names defined in `text`, qualified when the language has a class (JS: Class.method)."""
    out, cls = [], None
    js = re.search(r"\.(js|jsm|mjs)$", path)
    for line in text.splitlines():
        if js:
            c = JS_CLASS.match(line)
            if c:
                cls = c.group(1)
                out.append(cls)
                continue
            m = JS_FN.match(line)
            if m:
                n = m.group(1) or m.group(2)
                if n and n not in NOT_FN:
                    out.append(("%s.%s" % (cls, n)) if cls and m.group(2) else n)
        else:
            m = CXX_FN.match(line)
            if m and m.group(1) not in NOT_FN and not line.startswith(("#", "//", " ", "\t")):
                out.append(m.group(1))
    return out


def fn_of_header(text):
    m = CXX_FN.match(text.strip()) or JS_FN.match(text)
    if m:
        for g in m.groups():
            if g and g not in NOT_FN:
                return g
    mm = re.search(r"([A-Za-z_][\w:$.]*)\s*\(", text)
    return mm.group(1) if mm and mm.group(1) not in NOT_FN else None


def main():
    status = git("diff", "--name-status", BASE, "HEAD")
    added, modified = [], []
    for line in status.splitlines():
        s, _, p = line.partition("\t")
        if SKIP.search(p) or not CODE.search(p):
            continue
        (added if s.startswith("A") else modified).append(p)
    hook_files = [p for p in git("grep", "-l", "STEALTH-HOOK").splitlines() if CODE.search(p) and not SKIP.search(p)]
    symbols = set()
    hooks, hunks = {}, {}
    for p in added:
        text = git("show", "HEAD:" + p, check=False)
        symbols.update(names_in(text, p))
    for p in hook_files:
        lines = git("show", "HEAD:" + p, check=False).splitlines()
        found = []
        for i, line in enumerate(lines):
            if "STEALTH-HOOK" in line:
                for j in range(i, max(-1, i - 400), -1):
                    n = names_in(lines[j], p)
                    if n:
                        found.append(n[0])
                        break
        if found:
            hooks[p] = sorted(set(found))
            symbols.update(found)
    for p in modified:
        heads = []
        for line in git("diff", "-U0", BASE, "HEAD", "--", p).splitlines():
            m = HUNK.match(line)
            if m and m.group(1):
                n = fn_of_header(m.group(1))
                if n:
                    heads.append(n)
        if heads:
            hunks[p] = sorted(set(heads))[:80]
            symbols.update(hunks[p])
    sys.stdout.write(json.dumps({
        "base": BASE, "head": git("rev-parse", "HEAD").strip(), "added": sorted(added), "modified": sorted(modified),
        "hook_files": sorted(hook_files), "hooks": hooks, "hunks": hunks, "symbols": sorted(s for s in symbols if len(s) > 3),
    }, indent=0))


main()
