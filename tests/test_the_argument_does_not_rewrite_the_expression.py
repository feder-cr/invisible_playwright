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

Three of those four are SILENT, which is the bad kind: no error, no warning,
just a wrong answer that a caller builds its next decision on. The three
letters only have to appear inside a longer word, so MARGIN, LARGE and CHARGE
are all affected.

⛔ AND THE FIRST TEST HERE IS NOT ABOUT THE DEFECT. `op_evaluate` runs in the
MAIN world, as the page, which is the one place in this file that deliberately
leaves the utility world. That makes the emitted script something a site can
look at, so a change to its SHAPE is a stealth change even when it is a
correctness fix. The script is pinned byte for byte against what shipped
before: for an expression that never spelled the placeholder, this fix must
produce exactly the same characters, and that is what the first test proves.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from invisible_playwright._juggler import _marshal
from invisible_playwright._juggler._marshal import _as_callable, _with_argument

#: What the wrapper emitted before this fix, captured from the shipped code.
#: Any change to these strings changes what a page can see, so it has to be a
#: decision rather than a side effect of tidying.
SHIPPED = [
    ({"expression": "document.title"},
     "(() => { const r = (document.title);"
     "  return typeof r === 'function' ? r(null) : r; })()"),
    ({"expression": "() => x + 1", "arg": {"n": 7}},
     "(() => { const r = (() => x + 1);"
     "  return typeof r === 'function' ? r(7) : r; })()"),
    ({"expression": "els => els.length", "arg": {"s": "hello"}},
     "(() => { const r = (els => els.length);"
     '  return typeof r === \'function\' ? r("hello") : r; })()'),
]


@pytest.mark.parametrize("params,shipped", SHIPPED,
                         ids=["no argument", "number", "string"])
def test_the_script_the_page_runs_is_byte_for_byte_what_it_was(params, shipped):
    """The page must not be able to tell this fix happened.

    Known-bad: change one character of the template - a space, the name `r`,
    the arrow - and this goes red, which is the point. The wrapper is page
    visible, so its shape is not free to drift.
    """
    assert _with_argument(params) == shipped


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
    """The caller's code is an argument to the format string, never a place
    the format string looks.

    Known-bad: go back to `_as_callable(expression).replace("ARG", ...)` and
    every one of these loses its word.
    """
    built = _with_argument({"expression": expression})
    assert word in built, (
        "the caller wrote %r and the script that runs does not contain it: %r"
        % (word, built))
    assert "null" not in built.replace("r(null)", ""), (
        "something in the caller's expression was replaced: %r" % built)


def test_the_argument_still_reaches_the_function():
    """The placeholder moved, so the thing it existed for has to be re-proved.

    Known-bad: drop the second `%s` and the argument never arrives.
    """
    assert "r(null)" in _with_argument({"expression": "f"})
    assert "r(7)" in _with_argument({"expression": "f", "arg": {"n": 7}})
    assert 'r("hi")' in _with_argument({"expression": "f", "arg": {"s": "hi"}})
    assert "r(true)" in _with_argument({"expression": "f", "arg": {"b": True}})
    assert "r([1, 2])" in _with_argument(
        {"expression": "f", "arg": {"a": [{"n": 1}, {"n": 2}]}})
    assert 'r({"k": 1})' in _with_argument(
        {"expression": "f", "arg": {"o": [{"k": "k", "v": {"n": 1}}]}})


def test_the_wrapper_cannot_be_built_without_saying_what_the_argument_is():
    """`_as_callable` takes the argument now, so there is no half-built script
    with a placeholder in it for somebody to substitute later.

    Known-bad: give the parameter a default and the old two-step shape becomes
    writable again.
    """
    with pytest.raises(TypeError):
        _as_callable("document.title")


def test_no_placeholder_is_left_anywhere_in_the_package():
    """The class, not the instance. That word was only ever this placeholder,
    so the package holding none of it is the property worth keeping: a second
    helper that reintroduces the trick fails here rather than in a caller's
    wrong answer six months later.

    ⛔ READ AS SYNTAX, NOT AS TEXT, and the first draft proved why. A line scan
    was red on the DOCSTRING three functions up, which explains the defect and
    therefore spells it - the gate accused the paragraph that documents it.
    A placeholder is a string literal that IS the word; prose that mentions it
    is a longer string that merely contains it, and the tree tells them apart
    where a grep cannot.

    Known-bad: pass that word as a literal to anything in the package.
    """
    root = pathlib.Path(_marshal.__file__).resolve().parents[1]
    guilty = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "A" + "RG":
                guilty.append("%s:%d" % (path.relative_to(root), node.lineno))
    assert not guilty, (
        "the argument placeholder is back, and whatever reads it reads the "
        "caller's code too: %s" % guilty)
