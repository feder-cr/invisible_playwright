"""Every pin dict written anywhere in this repository is accepted by the validator.

WHY THIS EXISTS. On 2026-09-15 three keys left the pin surface in
`invisible_core`, and two test files here kept pinning two of them. Both files
are marked `e2e`, so the default selection - `-m 'not slow and not e2e'` -
deselects them: every local run was green, and the failure appeared on a CI leg
that launches a browser, as **81 errors** in a job that takes four minutes to
say so. The migration was not incomplete because it was hard. It was incomplete
because the only thing that would have noticed needed a browser.

Nothing here needs a browser. A pin dict is a dict of strings, and
`_validate_pin_key` is a pure function, so the question "does this key exist"
can be answered by reading the source and calling it. That is the whole gate.

It finds pin dicts two ways, because the repository writes them two ways:

  * a dict passed as `pin=` to any call, anywhere;
  * a module-level dict named `PIN`, `*_PIN` or `PIN_*`, which is the shape the
    e2e files use so one profile can be shared by a module of tests.

And it skips two things on purpose, both found by the first draft going red on a
perfectly healthy repository - the state in which a gate teaches people to
ignore it:

  * anything inside `with pytest.raises(...)`. Those dicts hold keys that are
    SUPPOSED to be refused; flagging them would be flagging the test for
    testing. Four files do this deliberately.
  * names that merely contain the letters PIN. `_NOT_PINNABLE` is a table of
    reasons, not a pin dict, and matching it read its explanations as keys.

Only STRING keys are checked. A key built at runtime is not something a reader
can check either, and there are none today.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from invisible_core._fpforge.profile import _validate_pin_key

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TREES = ("src", "tests", "scripts", "examples")


def _python_files():
    for tree in _TREES:
        d = _ROOT / tree
        if not d.is_dir():
            continue
        for f in d.rglob("*.py"):
            if "__pycache__" in str(f) or "/_pw/" in str(f).replace("\\", "/"):
                continue
            yield f


def _is_pin_name(name: str) -> bool:
    u = name.upper()
    return u == "PIN" or u.endswith("_PIN") or u.startswith("PIN_")


def _raising_lines(tree) -> set:
    """Every line inside a `with pytest.raises(...)` block."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "raises"):
                out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def _pin_dicts(path: pathlib.Path):
    """(line, [keys]) for every pin dict literal in one file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:                      # not ours to judge
        return
    refused = _raising_lines(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (kw.arg == "pin" and isinstance(kw.value, ast.Dict)
                        and kw.value.lineno not in refused):
                    yield kw.value.lineno, _string_keys(kw.value)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(_is_pin_name(n) for n in names):
                yield node.value.lineno, _string_keys(node.value)


def _string_keys(d: ast.Dict):
    return [k.value for k in d.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)]


def test_every_pin_dict_in_the_repository_uses_keys_that_exist():
    bad = []
    for path in _python_files():
        for line, keys in _pin_dicts(path):
            for key in keys:
                try:
                    _validate_pin_key(key)
                except ValueError as exc:
                    bad.append("%s:%d  %r - %s"
                               % (path.relative_to(_ROOT), line, key,
                                  str(exc).split(".")[0]))
    assert not bad, (
        "these pin dicts name keys the validator refuses, so every call that "
        "uses them raises:\n  " + "\n  ".join(bad) +
        "\nThe pin surface lives in invisible_core; a key that left it has to "
        "leave here too.")


def test_the_scan_actually_finds_the_dicts_it_claims_to():
    """A gate that scanned nothing would pass this file forever.

    Both shapes have to be found, because both exist: the e2e modules build a
    module-level PIN and share it across a module of tests, while the unit tests
    pass `pin=` inline. If either count goes to zero, the scan stopped matching
    the way the repository is written rather than the repository becoming clean.
    """
    inline = module_level = 0
    for path in _python_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                inline += sum(1 for kw in node.keywords
                              if kw.arg == "pin" and isinstance(kw.value, ast.Dict))
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                if any(_is_pin_name(t.id) for t in node.targets
                       if isinstance(t, ast.Name)):
                    module_level += 1
    assert inline >= 5, f"only {inline} inline `pin=` dicts found; the scan is blind"

    assert module_level >= 2, (
        f"only {module_level} module-level PIN dicts found; the e2e modules use "
        f"that shape and they are what this gate was written for")
