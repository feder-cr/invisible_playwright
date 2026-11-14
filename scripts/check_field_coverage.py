"""Which parameters does the client SEND that the server never reads?

⛔ THE INVENTORY THAT COUNTS OPERATIONS CANNOT SEE THIS. `inventario_voce6.py`
derives its coverage from the `METHODS` tables, so it answers "does this name
exist and respond". Both defects that survived the first transport judgement
were names that existed, responded, and threw the argument away:

* `newContext` stored the caller's options on the dispatcher, handed them back
  to the client in the initializer, and never sent one of them to the browser.
  Everything looked wired. A session declaring `timezone_id="America/New_York"`
  reported the host's zone - the exact signal this project exists not to emit.
* `updateSubscription` was a no-op, which is right for every event the server
  emits unconditionally and wrong for `fileChooser`, the one the engine will
  not report until it is told to intercept.

Neither is visible from the operation list, and neither failed loudly. This
compares the two sides that actually matter: **what crosses the wire** against
**what the code reads**.

⛔ THE "SENT" SIDE IS MEASURED, NOT DECLARED. It comes from a real captured
session (`capture_protocol.py`), because a protocol declaration says what MAY be
sent while a trace says what IS. A field the client never sends is not a hole to
fill; a field it sends every session and nobody reads is.

⛔ THE UNIT IS THE OPERATION, and the search walks outwards from it to a
fixed point: the helpers it calls on `self`, the helpers those call, and the
class-level tables any of them name. A per-MODULE search was the first version
and it was too weak - `enabled` is read by some unrelated operation, so
`updateSubscription` would have passed clean. One hop was the second version
and it was too strong - it reported `locale` and `timezoneId` as ignored on an
implementation that reads both through a helper.

    python scripts/capture_protocol.py <binary> -o trace.json
    python scripts/check_field_coverage.py trace.json
    python scripts/check_field_coverage.py --selftest
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "src" / "invisible_playwright" / "_juggler" / "server.py"
sys.stdout.reconfigure(encoding="utf-8")

#: Fields the client sends that this server deliberately does not read, each
#: with the reason. ⛔ A NAME GOES HERE ONLY WITH AN ARGUMENT: the list is the
#: place where "we decided not to" and "we forgot" become indistinguishable if
#: nobody writes which one it was.
DELIBERATE = {
    "sdkLanguage": "the client tells the driver which binding it is, for its "
                   "own error text. Nothing here behaves differently.",
    "guid": "addressing, not payload: the dispatcher is resolved from it "
            "before the operation is called.",
    "id": "the message id, consumed by the transport.",
    "internal": "a tracing hint for the inspector, which this server does "
                "not implement (perimeter, section 5.4).",
    "title": "the same: it labels a call in the trace viewer.",
    "info": "`__waitInfo__`'s payload, which exists for the trace viewer.",
    "noDefaultViewport": "Playwright's way of saying 'do not impose one'. "
                         "This server imposes nothing it was not given, so "
                         "the absence of `viewport` already says it.",
    "acceptDownloads": "downloads are outside the perimeter and the refusal "
                       "layer says so by name.",
    "recordVideo": "artifacts are outside the perimeter (section 5.4).",
    "recordHar": "HAR is outside the perimeter (section 5.4).",
    "selectorEngine": "custom selector engines are not implemented; the "
                      "refusal layer names the feature.",
    "selectorEngines": "the same feature, plural: the client registers its "
                       "custom engines at context creation. None of the "
                       "built-in selectors travel in this field - they are "
                       "resolved by the injected script - so ignoring it "
                       "loses `selectors.register()` and nothing else.",
    "strictSelectors": "the client enforces strictness on its own side "
                       "before the call ever reaches here.",
    "serviceWorkers": "always 'allow' here: the engine has no switch for it "
                      "and pretending would be a promise we cannot keep.",
    "baseURL": "the client resolves relative urls before sending them.",
}


def _constants(node: ast.AST) -> set:
    """Every string constant under a node. Blunt on purpose - see `reachable`."""
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _classes(tree: ast.AST) -> dict:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


#: ⛔ BOTH KINDS, and `AsyncFunctionDef` is not hypothetical here. Until
#: 2026-08-29 this gate collected `ast.FunctionDef` only, which was harmless
#: while the dispatcher had no async methods at all - and that stopped being
#: true the same day, when the Dialog fusion added `PageDispatcher.send_async`
#: as "the primitive a fused type needs", with more fused types stated as the
#: next step. The failure it would cause is silent and backwards: an `async
#: def` operation is absent from `_methods`, so `reachable` returns an EMPTY
#: known-set for it, and every field that operation legitimately reads gets
#: reported as ignored. Both function kinds are listed for the same reason as in any
#: AST walk over operations.
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _methods(cls: ast.ClassDef) -> dict:
    return {n.name: n for n in cls.body if isinstance(n, _FUNCTIONS)}


def _attributes(cls: ast.ClassDef) -> dict:
    """Class-level assignments, by name. `METHODS`, `ENGINE_OPTIONS`, ..."""
    out = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return out


def reachable(cls: ast.ClassDef, op: str) -> set:
    """The names an operation can plausibly read, from its own body outwards.

    ⛔ PER OPERATION, NOT PER MODULE, and the first version of this file was
    per module - which would have passed `updateSubscription` clean, because
    `enabled` is read by an unrelated operation twenty lines away. The defect
    it exists to catch is precisely an operation that reads NOTHING, so the
    unit of the question has to be the operation.

    ⛔ IT WALKS TO A FIXED POINT, and each thing it follows is there because
    leaving it out produced a false alarm on real code:

      * `self.other(...)` in the same class, transitively - `op_new_context`
        reads nothing itself and delegates the whole option set to
        `_apply_context_options`, which delegates further;
      * `self.CONSTANT` - that helper reads `params.get(name)` with a VARIABLE,
        and the names live in a class-level table. Without this every field
        driven by a table reads as ignored, which is a gate that cries wolf on
        the very pattern it should be encouraging.

    Recursion between helpers terminates: a method is expanded once.

    A tighter analysis would need real data flow. This one is deliberately
    generous: it can miss a field that is read nowhere but happens to appear as
    a string, and it does not invent faults - and a check that is wrong in the
    strict direction gets switched off after the third false alarm.
    """
    methods = _methods(cls)
    attributes = _attributes(cls)
    if op not in methods:
        return set()
    # ⛔ TO A FIXED POINT, not one hop. The first version followed `self.x`
    # only from the operation's own body, and reported `locale` and
    # `timezoneId` as ignored on an implementation that reads both: the
    # operation delegates to a helper, and the helper is the one that names
    # the class-level table. One hop short is a gate that reports the correct
    # code and stays quiet about the next real defect, because by then nobody
    # reads its output.
    seen = {op}
    frontier = [methods[op]]
    names = set()
    while frontier:
        body = frontier.pop()
        names |= _constants(body)
        for node in ast.walk(body):
            if not (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"):
                continue
            if node.attr in methods and node.attr not in seen:
                seen.add(node.attr)
                frontier.append(methods[node.attr])
            elif node.attr in attributes:
                names |= _constants(attributes[node.attr])
    return names


def sent_fields(trace: list) -> dict:
    """method -> the set of parameter names the client actually sent."""
    out: dict = {}
    for entry in trace:
        if entry.get("dir") != "send":
            continue
        message = entry.get("msg") or {}
        method = message.get("method")
        if not method:
            continue
        out.setdefault(method, set()).update((message.get("params") or {}))
    return out


def operations(tree: ast.AST) -> dict:
    """protocol method -> (class, op function name), from the METHODS tables.

    ⛔ Read from the CODE, never listed here. The tables are the server's own
    routing, so a method added tomorrow is audited tomorrow without anybody
    remembering to update this file - which is the difference between a gate
    and a comment.
    """
    out = {}
    for cls in _classes(tree).values():
        table = _attributes(cls).get("METHODS")
        if not isinstance(table, ast.Dict):
            continue
        for key, value in zip(table.keys, table.values):
            if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                out.setdefault(key.value, []).append((cls, value.value))
    return out


def audit(trace: list, source: str) -> list:
    tree = ast.parse(source)
    routes = operations(tree)
    faults = []
    for method, fields in sorted(sent_fields(trace).items()):
        targets = routes.get(method)
        if not targets:
            # ⛔ Not a fault HERE. A method with no route is a missing
            # operation, which is what `inventario_voce6.py` counts; this file
            # is about operations that exist and ignore their input, and
            # reporting both in one number would blur the two.
            continue
        known = set()
        for cls, op in targets:
            known |= reachable(cls, op)
        missing = sorted(f for f in fields
                         if f not in known and f not in DELIBERATE)
        if missing:
            faults.append((method, missing))
    return faults


def _cls(op_body: str, methods='{"newContext": "op"}', extra="", asynchronous=False):
    """A one-class module for the selftest, assembled line by line.

    ⛔ Built from a list rather than a literal with escapes in it. This project
    has corrupted four files by writing backslashes through a shell, twice in
    this same session, and a test fixture whose newlines are wrong fails as a
    SyntaxError in the thing under test - which reads as the checker being
    broken rather than the fixture.

    `asynchronous=True` emits `async def` instead of `def`, so the case below
    can pin the blind spot fixed on 2026-08-29: before that, an async
    operation was invisible to `_methods` and every field it read was
    misreported as ignored.
    """
    return chr(10).join([
        "class D:",
        "    METHODS = %s" % methods,
        extra,
        "    %sdef op(self, params):" % ("async " if asynchronous else ""),
        op_body,
        "",
    ])


SELFTEST = [
    # (name, source, trace, how many methods must be reported)
    ("a field the operation reads is not reported",
     _cls('        return params.get("locale")'),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"locale": "en-US"}}}], 0),
    ("a field the operation does NOT read is reported",
     _cls('        return params.get("locale")'),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"locale": "en", "timezoneId": "X"}}}],
     1),
    # ⛔ The two cases below are the same pair as above with `async def`
    # instead of `def`. Only the FIRST one guards the 2026-08-29 fix: with the
    # blind spot restored it reports BROKEN (1, expected 0), because an async
    # operation was absent from `_methods`, its known-set came back empty, and
    # the field it plainly reads was called ignored.
    # The second SURVIVES that mutation, and it is kept knowing this: it
    # answers 1 either way, for two different reasons - with the fix because
    # the field really is unread, without it because the method is invisible
    # entirely. It pins the other half of the behaviour, not the fix.
    ("a field an ASYNC operation reads is not reported",
     _cls('        return params.get("locale")', asynchronous=True),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"locale": "en-US"}}}], 0),
    ("a field an ASYNC operation does NOT read is still reported",
     _cls('        return params.get("locale")', asynchronous=True),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"locale": "en", "timezoneId": "X"}}}],
     1),
    ("an operation that reads nothing at all is reported",
     _cls("        return None"),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 1),
    ("a deliberate omission is not a fault",
     _cls("        return None"),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"sdkLanguage": "python"}}}], 0),
    ("a received message is not an input",
     _cls("        return None"),
     [{"dir": "recv", "msg": {"method": "__create__",
                              "params": {"whatever": 1}}}], 0),
    ("a method with no route is not reported here",
     _cls("        return None", methods='{"other": "op"}'),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 0),
    ("a field read through a HELPER on self counts",
     _cls("        return self.helper(params)",
          extra=chr(10).join(['    def helper(self, params):',
                              '        return params.get("timezoneId")'])),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 0),
    ("a field read TWO hops away counts",
     _cls("        return self.first(params)",
          extra=chr(10).join([
              "    def first(self, params):",
              "        return self.second(params)",
              "    def second(self, params):",
              '        return params.get("timezoneId")'])),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 0),
    ("a table named two hops away counts",
     _cls("        return self.first(params)",
          extra=chr(10).join([
              '    OPTS = (("timezoneId", "x"),)',
              "    def first(self, params):",
              "        return [params.get(n) for n, _ in self.OPTS]"])),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 0),
    ("recursion between helpers must not hang the walk",
     _cls("        return self.first(params)",
          extra=chr(10).join([
              "    def first(self, params):",
              "        return self.second(params)",
              "    def second(self, params):",
              "        return self.first(params)"])),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 1),
    ("a field driven by a class-level TABLE counts",
     _cls("        return [params.get(n) for n, _ in self.OPTS]",
          extra='    OPTS = (("timezoneId", "x"),)'),
     [{"dir": "send", "msg": {"method": "newContext",
                              "params": {"timezoneId": "X"}}}], 0),
    ("a method with no params is not a fault",
     _cls("        return None", methods='{"close": "op"}'),
     [{"dir": "send", "msg": {"method": "close", "params": {}}}], 0),
]


def selftest() -> int:
    bad = 0
    for name, source, trace, expected in SELFTEST:
        got = len(audit(trace, source))
        ok = got == expected
        bad += 0 if ok else 1
        print("  %-58s %s (%d, expected %d)"
              % (name, "ok" if ok else "BROKEN", got, expected))
    print("selftest: %d cases, %d broken" % (len(SELFTEST), bad))
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("trace", nargs="?", help="a trace from capture_protocol.py")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if not a.trace:
        p.error("a trace is required (or --selftest)")

    trace = json.loads(pathlib.Path(a.trace).read_text(encoding="utf-8"))
    faults = audit(trace, SERVER.read_text(encoding="utf-8"))
    for method, missing in faults:
        print("  %s: the client sends %s and nothing in the server reads "
              "%s" % (method, missing,
                      "them" if len(missing) > 1 else "it"))
    if faults:
        print("FIELD COVERAGE: %d operation(s) ignore what they are given. "
              "Each one is either a decision - then name it in DELIBERATE "
              "with the reason - or a feature that silently does nothing."
              % len(faults))
        return 1
    print("FIELD COVERAGE: every parameter this session sent is read "
          "somewhere in the server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
