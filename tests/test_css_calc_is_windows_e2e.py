"""CSS calc() functions give Windows' text on every OS.

The style engine evaluates ``calc(1px * sin(x))``, ``asin``, ``pow``, ``log``
and the others in f32 with the C library's f32 functions (sinf, asinf, powf,
logf...), and writes the number with 6 significant digits. Windows Firefox gets
them from ucrtbase.dll; Linux Firefox got glibc's, whose last f32 bit differs
for some inputs - and for some of those the difference moves the 6th digit,
which is what a page reads with getComputedStyle.

tests/data/ucrt_css_calc.txt holds, per function, inputs where ucrtbase.dll and
glibc write a different text (and a few where they agree), with the text
ucrtbase.dll gives; scripts/ucrt_css_calc.py makes it. A function with no
differing input in the searched grid (tan, atan, atan2, exp: 0 of 65,000 to
90,000 each; hypot is a sum and a square root, exact) is in the file with
control inputs only, so it checks that the page computes at all.

A number is read with ``word-spacing`` (px) and an angle with ``rotate``
(deg): both are computed values that getComputedStyle writes as the style
engine serialized them, with no layout in between.

Every mismatch is listed in the failure, not just the first.
"""
from __future__ import annotations

from pathlib import Path

import pytest


DATA = Path(__file__).parent / "data" / "ucrt_css_calc.txt"
ANGLE_FUNCTIONS = ("asin", "acos", "atan", "atan2")

READ = """
(cases) => cases.map(([prop, value]) => {
  const el = document.createElement("div");
  el.style.setProperty(prop, value);
  document.body.appendChild(el);
  const got = getComputedStyle(el).getPropertyValue(prop);
  el.remove();
  return got;
})
"""


def _cases():
    out = []
    for line in DATA.read_text().splitlines():
        if line and not line.startswith("#"):
            parts = line.split()
            out.append((parts[0], parts[1:-1], parts[-1]))
    return out


def _css(fn, args):
    """(property, value) that makes the style engine compute fn(args)."""
    call = "%s(%s)" % (fn, ", ".join(args))
    if fn in ANGLE_FUNCTIONS:
        return "rotate", call
    return "word-spacing", "calc(1px * %s)" % call


@pytest.mark.unit
def test_the_data_has_every_function_and_the_inputs_that_tell_the_libraries_apart():
    cases = _cases()
    assert {c[0] for c in cases} == {
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "exp", "log", "pow", "hypot"}
    # The cases that make this test about the library and not only the engine.
    for fn in ("sin", "cos", "asin", "acos", "log", "pow"):
        assert sum(1 for c in cases if c[0] == fn) >= 4, fn
    # sin(-12.2958984375): ucrtbase 0.267186, glibc 0.267187.
    assert ("sin", ["-12.2958984375"], "0.267186px") in cases


@pytest.mark.e2e
def test_css_calc_functions_are_ucrtbase_bytes(firefox_binary, engine_at_least):
    engine_at_least("firefox-64")
    from invisible_playwright import InvisiblePlaywright

    cases = _cases()
    with InvisiblePlaywright(seed=6021, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        got = page.evaluate(READ, [list(_css(fn, args)) for fn, args, _ in cases])

    wrong = ["%s(%s): page %r, ucrtbase %r" % (fn, ", ".join(args), g, want)
             for (fn, args, want), g in zip(cases, got) if g != want]
    assert not wrong, ("%d of %d calc() results are not ucrtbase's:\n  %s"
                       % (len(wrong), len(cases), "\n  ".join(wrong)))
