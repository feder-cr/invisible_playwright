"""Every operation that refuses is named in `perimeter.py`, and nowhere else.

⛔ WRITTEN AFTER FINDING NINE THAT WERE NOT. `perimeter.py` exists so that a
refusal can name the feature it belongs to, and its own docstring says the point
is to tell a caller "that the refusal is a decision rather than a gap". Measured
2026-09-15: `OUTSIDE` declared 76 operations, and **ten more raised
`ProtocolException` inline in their dispatcher** without appearing in any
inventory. Reading `server.py` end to end was the only way to know they existed.

That matters more here than it would elsewhere, because the README promises
**"100% Playwright-compatible - all methods"**. A refusal nobody has written
down is the exact gap between that sentence and the code.

**What this test asserts, and why it is two directions.**

1. Every inline refusal is declared. A new one that nobody adds to
   `perimeter.py` turns this red, so the inventory cannot silently fall behind
   the code again.
2. Every declared name is still refused. A set that keeps a name whose
   implementation has since landed is just as wrong: it would describe a gap
   that no longer exists, and `OUTSIDE` drives the sentence users read.

⛔ **STATIC, and deliberately so.** It parses `server.py` with `ast` and imports
nothing: no browser, no engine, no session. It costs milliseconds and can run in
the default suite, which is the only place a gate of this kind is any use.

⛔ **It looks at the FIRST statement of the body, not at "the method mentions
ProtocolException".** A method that validates its arguments and raises on a bad
one is not a refusal: it is an ordinary error path, and counting it would make
this test demand that correct code be declared as missing. Only a method whose
body BEGINS by raising is refusing unconditionally.
"""
from __future__ import annotations

import ast
from pathlib import Path

from invisible_playwright._juggler import perimeter

SERVER = Path(perimeter.__file__).with_name("server.py")


def _method_tables(tree: ast.Module) -> dict:
    """python name -> the protocol operations it answers.

    ⛔ A LIST, NOT A SINGLE NAME, and the first version of this got it wrong.
    Two operations can share one handler: `resolve` and `reject` both map to
    `op_binding_reply`, which refuses. Keyed by the python name with a single
    value, the second one overwrote the first, so the scan saw `resolve` and
    was blind to `reject` - a refusal that no inventory would have caught,
    inside the test written to catch exactly that.
    """
    found: dict = {}
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for stmt in cls.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(getattr(t, "id", "") == "METHODS" for t in stmt.targets):
                continue
            if isinstance(stmt.value, ast.Dict):
                for key, value in zip(stmt.value.keys, stmt.value.values):
                    if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                        found.setdefault(value.value, []).append(key.value)
    return found


def _unconditional_refusals(tree: ast.Module, table: dict) -> set:
    refusing = set()
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for stmt in cls.body:
            if not isinstance(stmt, ast.FunctionDef) or stmt.name not in table:
                continue
            body = [s for s in stmt.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            if not body or not isinstance(body[0], ast.Raise):
                continue
            exc = body[0].exc
            name = getattr(getattr(exc, "func", None), "id", None)
            if name == "ProtocolException":
                # every operation routed to this handler refuses, not just one
                refusing.update(table[stmt.name])
    return refusing


def _read():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    table = _method_tables(tree)
    return table, _unconditional_refusals(tree, table)


def test_the_method_tables_are_readable():
    """The safety net under the two assertions below.

    Both of them compare against a set derived from `server.py`. If that
    derivation broke - a renamed table, a changed shape - it would return
    nothing, both assertions would pass over an empty set, and this file would
    report green while checking exactly nothing.
    """
    table, _ = _read()
    operations = sum(len(v) for v in table.values())
    assert operations > 100, (
        "only %d operations found in the METHODS tables: the derivation is "
        "broken, not the server" % operations)


def test_every_inline_refusal_is_declared_in_the_perimeter():
    _, refusing = _read()
    undeclared = sorted(refusing - perimeter.REFUSED)
    assert not undeclared, (
        "these operations refuse and no inventory names them:\n  "
        + "\n  ".join(undeclared)
        + "\n\nAdd each to the set in perimeter.py that says WHY it refuses:\n"
          "  NO_ENGINE_COMMAND  the engine offers no command at all\n"
          "  FIXED_AT_BUILD     decided when the injected script is built\n"
          "  NOT_WIRED_YET      our debt, simply not written\n"
          "  OUTSIDE            a whole feature left out by decision\n"
          "  WITHDRAWN          written, and taken back: the engine answers,\n"
          "                     and what it leaves behind is worse than not\n"
          "                     having the method at all\n"
          "A refusal nobody wrote down is the gap between the README's "
          "'100% compatible' and the code.")


def test_every_declared_refusal_is_still_refused():
    """The other direction: a name that now works must leave the inventory."""
    _, refusing = _read()
    # ⛔ `WITHDRAWN` belongs in this direction more than any of the others. The
    # three above describe things nobody has written; a withdrawn operation IS
    # written and is being held back by a defect, so the day that defect is
    # fixed the natural move is to stop refusing and forget the inventory. This
    # is what asks.
    declared_inline = (perimeter.NO_ENGINE_COMMAND | perimeter.FIXED_AT_BUILD
                       | perimeter.NOT_WIRED_YET | perimeter.WITHDRAWN)
    stale = sorted(declared_inline - refusing)
    assert not stale, (
        "these are declared as refusing and no longer do:\n  "
        + "\n  ".join(stale)
        + "\n\nRemove them: an inventory that describes a gap which has been "
          "closed is as wrong as one that misses a gap.")
