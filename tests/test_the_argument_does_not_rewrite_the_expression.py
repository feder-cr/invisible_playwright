"""The caller's JavaScript reaches the page as the caller wrote it.

⛔ THE ARGUMENT'S PLACEHOLDER WAS SEARCHED FOR INSIDE THE CALLER'S OWN CODE.
The wrapper spelled the argument `r(ARG)` and the caller substituted it
afterwards with `.replace("ARG", ...)` - over a string that already held the
caller's expression. So every uppercase `ARG` anybody wrote was rewritten to
the argument's JSON, inside their own script, with nothing said.

Measured on 2026-09-14 by driving a real browser through the MCP server:

    'CARGO'.length            ran as  'CnullO'.length   and answered 6, not 5
    [data-role='TARGET']      ran as  [data-role='TnullET']  and matched nothing
    /ARGH/                    ran as  /nullH/
    const ARG = 5             ran as  const null = 5    and was a SyntaxError

⛔ AND THEN THE WRAPPER ITSELF WENT ([B340]). The fix of 2026-09-14 kept the
caller's text inside `(() => { const r = (TEXT); return typeof r === 'function'
? r(ARG) : r; })()`, pinned here byte for byte because a page can look at the
code it runs. An expression is all a pair of parentheses holds, so every
statement died with "missing ) in parenthetical" where Playwright, which runs
the text with `globalThis.eval`, evaluates it. The text is now evaluated as a
script on its own, and a function it produced is called by a protocol call
with the argument as a protocol value. What a page can see of the code is the
caller's text and nothing of this package's - a stronger form of what the
byte-for-byte test protected - and that is what the first test pins now.
The change of shape was decided by the project manager on 2026-10-18.

No browser here: the server's own functions run against an injected script
that records what it is asked.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from invisible_playwright._juggler import _marshal, server
from invisible_playwright._juggler._marshal import _as_script
from invisible_playwright._juggler.server import _Predicate, _run_callers_code


class _Injected:
    """Records every command; `evaluated` are the RemoteObjects the scripts
    produce, in order, and `called` the one every call answers."""

    def __init__(self, *evaluated, called=None, context="world-1"):
        self.evaluated = list(evaluated)
        self.called = called if called is not None else {"value": "called"}
        self.context = context
        self.log = []

    def caller_context(self, frame_id, **options):
        return self.context

    def evaluate_raw(self, context, expression, **options):
        self.log.append(("evaluate", context, expression))
        return self.evaluated.pop(0)

    def call_raw(self, context, declaration, args, by_value=False, **options):
        self.log.append(("call", context, declaration, args, by_value))
        if isinstance(self.called, Exception):
            raise self.called
        return self.called

    def release(self, context, object_id):
        self.log.append(("release", context, object_id))

    def texts(self):
        """Every piece of code sent to the page."""
        return [entry[2] for entry in self.log if entry[0] in ("evaluate", "call")]


FUNCTION = {"type": "function", "objectId": "the-function"}


SCRIPTS = [
    ("document.title", "document.title"),
    ("() => x + 1", "() => x + 1"),
    ("setTimeout(() => { document.title = 'x' }, 2000);",
     "setTimeout(() => { document.title = 'x' }, 2000);"),
    ("  els => els.length\n", "els => els.length"),
    ("const a = 1; a + 1", "const a = 1; a + 1"),
    ("function f() { return 1 }", "(function f() { return 1 })"),
    ("async function () {}", "(async function () {})"),
    ("(1+2)", "(1+2)"),
    # JavaScript's trim, not Python's strip: U+FEFF goes, U+0085 stays.
    ("\ufeffdocument.title", "document.title"),
    ("\x85document.title", "\x85document.title"),
]


@pytest.mark.parametrize("text,evaluated", SCRIPTS, ids=[t for t, _ in SCRIPTS])
def test_the_script_the_page_evaluates_is_the_callers_text(text, evaluated):
    """Nothing of this package's is added to the caller's code: the text
    arrives trimmed as JavaScript trims, and a function DECLARATION in
    parentheses so that it is a value, which is Playwright's own
    normalisation. A statement with its semicolon stays a statement.

    Known-bad: put the text back inside `const r = (...)`, or any wrapper."""
    injected = _Injected({"value": 1})
    _run_callers_code(injected, "frame", {"expression": text}, by_value=True)
    assert injected.log == [("evaluate", "world-1", evaluated)]
    assert _as_script(text) == evaluated


SPELLS_IT = [
    ("(() => 'CARGO'.length)", "CARGO"),
    ("() => document.querySelector(\"[data-role='TARGET']\")", "TARGET"),
    ("() => /ARGH/.source", "ARGH"),
    ("() => { const MARGIN = 4; return MARGIN; }", "MARGIN"),
    ("() => 'ARG'", "ARG"),
]


@pytest.mark.parametrize("expression,word", SPELLS_IT,
                         ids=[w for _, w in SPELLS_IT])
def test_an_expression_that_spells_the_placeholder_is_left_alone(expression, word):
    """The caller's code is sent as it is; nothing looks inside it."""
    injected = _Injected(FUNCTION)
    _run_callers_code(injected, "frame", {"expression": expression, "arg": {"n": 7}},
                      by_value=True)
    assert injected.texts()[0] == expression and word in injected.texts()[0]


@pytest.mark.parametrize("arg,value", [
    (None, None), ({"n": 7}, 7), ({"s": "hi"}, "hi"), ({"b": True}, True),
    ({"a": [{"n": 1}, {"n": 2}]}, [1, 2]),
    ({"o": [{"k": "k", "v": {"n": 1}}]}, {"k": 1}),
], ids=["none", "number", "string", "bool", "list", "object"])
def test_the_argument_reaches_the_function_as_a_protocol_value(arg, value):
    """The argument is the call's last protocol argument and in no text: there
    is no placeholder to substitute, because there is no template."""
    injected = _Injected(FUNCTION)
    params = {"expression": "f"} if arg is None else {"expression": "f", "arg": arg}
    _run_callers_code(injected, "frame", params, by_value=True)
    call = next(entry for entry in injected.log if entry[0] == "call")
    assert call[3] == [{"objectId": "the-function"}, {"value": value}]
    assert all("7" not in text and "hi" not in text for text in injected.texts())


def test_the_receivers_come_first_and_the_argument_last():
    """`fn(element, arg)`, Playwright's contract for the element-scoped calls."""
    injected = _Injected(FUNCTION)
    _run_callers_code(injected, "frame", {"expression": "f", "arg": {"n": 2}},
                      ["el-1", "el-2"], by_value=False)
    call = next(entry for entry in injected.log if entry[0] == "call")
    assert call[3] == [{"objectId": "the-function"}, {"objectId": "el-1"},
                       {"objectId": "el-2"}, {"value": 2}]
    assert call[4] is False


def test_the_function_is_released_after_its_call_even_when_it_throws():
    injected = _Injected(FUNCTION, called=RuntimeError("the page threw"))
    with pytest.raises(RuntimeError):
        _run_callers_code(injected, "frame", {"expression": "f"}, by_value=True)
    assert injected.log[-1] == ("release", "world-1", "the-function")


def test_an_object_read_by_value_is_released_and_a_primitive_is_answered_as_it_is():
    injected = _Injected({"type": "object", "objectId": "the-object"},
                         called={"value": {"a": 1}})
    assert _run_callers_code(injected, "frame", {"expression": "({a: 1})"},
                             by_value=True) == {"value": {"a": 1}}
    assert [e[0] for e in injected.log] == ["evaluate", "call", "release"]
    assert injected.log[-1] == ("release", "world-1", "the-object")

    injected = _Injected({"value": 3})
    assert _run_callers_code(injected, "frame", {"expression": "1 + 2"},
                             by_value=True) == {"value": 3}
    assert [e[0] for e in injected.log] == ["evaluate"]


def test_by_reference_the_object_is_the_callers_to_keep():
    """`evaluate_handle`: the reference IS the handle, so nothing releases it."""
    injected = _Injected({"type": "object", "objectId": "the-object"})
    assert _run_callers_code(injected, "frame", {"expression": "document"},
                             by_value=False)["objectId"] == "the-object"
    assert [e[0] for e in injected.log] == ["evaluate"]


# --- wait_for_function --------------------------------------------------------

def test_a_function_predicate_is_evaluated_once_per_world_and_called_each_turn():
    """Playwright's poll keeps the function it evaluated (`evaledExpression`)
    and calls it again on every turn; a new world evaluates the script again."""
    injected = _Injected(FUNCTION, FUNCTION, called={"value": False})
    predicate = _Predicate(injected, "frame", {"expression": "() => window.ready"})
    assert predicate.turn() is None
    assert predicate.turn() is None
    assert [e[0] for e in injected.log] == ["evaluate", "call", "call"]
    injected.context = "world-2"
    predicate.turn()
    assert [e[0] for e in injected.log] == ["evaluate", "call", "call", "evaluate", "call"]


def test_an_expression_predicate_is_evaluated_again_each_turn():
    injected = _Injected({"value": False}, {"value": 0}, {"value": "yes"})
    predicate = _Predicate(injected, "frame", {"expression": "window.ready"})
    assert predicate.turn() is None
    assert predicate.turn() is None
    assert predicate.turn() == {"value": "yes"}
    assert [e[0] for e in injected.log] == ["evaluate"] * 3


def test_the_predicate_releases_its_function_and_every_falsy_object():
    injected = _Injected(FUNCTION, called={"type": "object", "subtype": "null",
                                           "objectId": "falsy"})
    predicate = _Predicate(injected, "frame", {"expression": "() => document.all"})
    server.js_truthy, real = (lambda remote: False), server.js_truthy
    try:
        assert predicate.turn() is None
    finally:
        server.js_truthy = real
    assert ("release", "world-1", "falsy") in injected.log
    predicate.release()
    assert injected.log[-1] == ("release", "world-1", "the-function")
    predicate.release()
    assert injected.log.count(("release", "world-1", "the-function")) == 1


# --- the class: one place, no template, no placeholder -----------------------

def _package_trees():
    root = pathlib.Path(_marshal.__file__).resolve().parents[1]
    for path in sorted(root.rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(
            path.read_text(encoding="utf-8"), filename=str(path))


def test_no_placeholder_is_left_anywhere_in_the_package():
    """The class, not the instance: that word was only ever the placeholder.

    ⛔ READ AS SYNTAX, NOT AS TEXT: a docstring that explains the defect spells
    the word, and only the tree tells a literal that IS it from prose that
    merely contains it. Known-bad: pass that word as a literal to anything."""
    guilty = ["%s:%d" % (rel, node.lineno) for rel, tree in _package_trees()
              for node in ast.walk(tree)
              if isinstance(node, ast.Constant) and node.value == "A" + "RG"]
    assert not guilty, guilty


def test_the_callers_text_is_evaluated_in_one_place_and_wrapped_in_none():
    """The other defect a builder had: copies. `handle.evaluate` was fixed to
    pass the argument while `eval_on_selector`, built by hand two methods
    away, kept dropping it. Now no literal wraps the caller's text, and the
    text is evaluated only by `_run_callers_code` and the predicate's turn.

    Known-bad: evaluate `_as_script(...)` from a dispatcher method, or write a
    `typeof r === 'function'` wrapper anywhere."""
    wrappers, evaluators = [], set()
    for rel, tree in _package_trees():
        # A docstring that explains the old wrapper quotes it; it is prose,
        # not a literal anything sends.
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                        ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and "typeof r === 'function'" in node.value):
                wrappers.append("%s:%d" % (rel, node.lineno))
        for owner in ast.walk(tree):
            if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(owner):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "_as_script"):
                    evaluators.add("%s::%s" % (rel, owner.name))
    assert not wrappers, wrappers
    assert evaluators == {"_juggler/server.py::_run_callers_code",
                          "_juggler/server.py::turn"}, evaluators
