"""The expected values of tests/test_css_calc_is_windows_e2e.py.

Run it with `python -I`. Two steps, because a number is only worth testing if
the two C libraries give a different page-visible text for it:

  python -I scripts/ucrt_css_calc.py dump  OUT   # on Windows: ucrtbase.dll's f32 functions
  python -I scripts/ucrt_css_calc.py dump  OUT   # on Linux:   the host's libm
  python -I scripts/ucrt_css_calc.py pick  WIN LINUX tests/data/ucrt_css_calc.txt

`dump` computes, for a fixed grid of inputs, what the style engine (Servo's
calc.rs) makes of ``calc(1px * sin(x))`` and the others, and the text it then
serializes. The style engine does every step in f32 (CSSFloat): the library
function is the f32 one (sinf, cosf, tanf, asinf, acosf, atanf, atan2f, expf,
logf, powf), an angle is converted to degrees with one f32 multiplication, and
a number is written by cssparser's dtoa_short: the shortest decimal that
reads back as that f32, rounded half up to 6 significant digits. So two
libraries whose results differ in the last f32 bit only show on the page when
that bit moves the 6th digit - which `pick` looks for.

`pick` keeps every input whose text differs between the two dumps, and a few
that do not (the control: the same text from both libraries), in the format
the test reads:

    <fn> <args...> <text on Windows>

The reference is ucrtbase's FMA3 code path, the one every Windows 11 CPU runs
(the same one idwr_math reproduces); the SSE2 fallback of older CPUs can differ in
the last bit and is not what a page on Windows 11 reads.

Inputs are exact in f32 (multiples of a power of two), so the page and this
script read the same number whatever the CSS parser does with decimals.
"""
from __future__ import annotations

import ctypes
import math
import struct
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

#: Rust's f32::to_degrees multiplier, as an f32.
RAD_TO_DEG = struct.unpack("<f", struct.pack("<f", 57.2957795130823208767981548141051703))[0]

#: How many differing inputs of each function `pick` keeps, and how many of the
#: ones that agree.
KEEP_DIFFERENT = 6
KEEP_SAME = 3


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def load_libm():
    if sys.platform == "win32":
        lib = ctypes.CDLL("ucrtbase.dll")
    else:
        lib = ctypes.CDLL("libm.so.6")
    funcs = {}
    for name, nargs in (("sinf", 1), ("cosf", 1), ("tanf", 1), ("asinf", 1), ("acosf", 1),
                        ("atanf", 1), ("atan2f", 2), ("expf", 1), ("logf", 1), ("powf", 2)):
        fn = getattr(lib, name)
        fn.restype = ctypes.c_float
        fn.argtypes = [ctypes.c_float] * nargs
        funcs[name] = fn
    return funcs


def shortest_f32(x: float) -> str:
    """The shortest decimal text that reads back as this f32."""
    for digits in range(1, 10):
        text = "%.*e" % (digits - 1, x)
        if f32(float(text)) == x:
            return text
    return "%.8e" % x


def serialize(x: float) -> str:
    """cssparser's number text for an f32: 6 significant digits, half up,
    no trailing zeros, no exponent in the range the grid stays in."""
    if x == 0:
        return "0"
    d = Decimal(shortest_f32(x))
    exp = d.adjusted()
    q = d.quantize(Decimal(1).scaleb(exp - 5), rounding=ROUND_HALF_UP)
    text = format(q.normalize(), "f")
    return "0" if text in ("-0", "") else text


def decimal(k: int, shift: int) -> str:
    """k / 2**shift as an exact decimal text."""
    d = Decimal(k) / Decimal(2 ** shift)
    return format(d.normalize(), "f")


def grid():
    """(fn, args as texts) for every case of the dump."""
    out = []
    for fn, lim, shift in (("sin", 16, 10), ("cos", 16, 10), ("tan", 16, 10)):
        for k in range(-lim << shift, (lim << shift) + 1):
            out.append((fn, [decimal(k, shift)]))
    # Finer, over the stretch where the functions bend the most.
    for k in range(-(4 << 13), (4 << 13) + 1):
        out.append(("tan", [decimal(k, 13)]))
    for k in range(-(8 << 12), (8 << 12) + 1):
        out.append(("atan", [decimal(k, 12)]))
    for k in range(-(4 << 12), (4 << 12) + 1):
        out.append(("exp", [decimal(k, 12)]))
    for k in range(1, 32 * 1024 + 1):
        out.append(("log", [decimal(k, 10)]))
    for a in range(-128, 129):
        for b in range(-128, 129):
            if a or b:
                out.append(("atan2", [decimal(a, 6), decimal(b, 6)]))
    for k in range(-4096, 4097):
        out.append(("asin", [decimal(k, 12)]))
        out.append(("acos", [decimal(k, 12)]))
    for k in range(-8 * 512, 8 * 512 + 1):
        out.append(("atan", [decimal(k, 9)]))
    for a in range(-64, 65):
        for b in range(-64, 65):
            if a or b:
                out.append(("atan2", [decimal(a, 4), decimal(b, 4)]))
    for k in range(-10 * 256, 10 * 256 + 1):
        out.append(("exp", [decimal(k, 8)]))
    for k in range(1, 64 * 64 + 1):
        out.append(("log", [decimal(k, 6)]))
    for a in range(2, 513):
        for b in (2, 3, 5, 7, 10):
            out.append(("log", [decimal(a, 3), str(b)]))
    for a in range(-32, 33):
        for b in range(-32, 33):
            out.append(("hypot", [decimal(a * 3, 4), decimal(b * 5, 4)]))
    for a in range(4, 257):
        for b in range(-64, 65):
            out.append(("pow", [decimal(a, 4), decimal(b, 4)]))
    return out


def evaluate(lib, fn, args):
    """(text, unit) the page shows, from f32 steps."""
    v = [f32(float(a)) for a in args]
    if fn in ("sin", "cos", "tan", "exp"):
        return serialize(f32(lib[fn + "f"](v[0]))), "px"
    if fn == "log":
        if len(v) == 1:
            return serialize(f32(lib["logf"](v[0]))), "px"
        return serialize(f32(f32(lib["logf"](v[0])) / f32(lib["logf"](v[1])))), "px"
    if fn == "hypot":
        # powi(2) is a product, the sum is f32, sqrt is exact in IEEE: no library
        # function here, so no difference between the two - the control.
        return serialize(f32(math.sqrt(f32(f32(v[0] * v[0]) + f32(v[1] * v[1]))))), "px"
    if fn == "pow":
        return serialize(f32(lib["powf"](v[0], v[1]))), "px"
    if fn in ("asin", "acos", "atan"):
        return serialize(f32(f32(lib[fn + "f"](v[0])) * RAD_TO_DEG)), "deg"
    if fn == "atan2":
        return serialize(f32(f32(lib["atan2f"](v[0], v[1])) * RAD_TO_DEG)), "deg"
    raise ValueError(fn)


def dump(out: Path) -> None:
    lib = load_libm()
    lines = []
    for fn, args in grid():
        text, unit = evaluate(lib, fn, args)
        if text in ("nan", "inf", "-inf") or "e" in text.lower():
            continue
        lines.append("%s %s %s%s" % (fn, " ".join(args), text, unit))
    out.write_text("\n".join(lines) + "\n")
    print("wrote %s: %d cases (%s)" % (out, len(lines), sys.platform))


def read(path: Path) -> dict:
    cases = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        cases[tuple(parts[:-1])] = parts[-1]
    return cases


def pick(win: Path, linux: Path, out: Path) -> None:
    a, b = read(win), read(linux)
    chosen = ["# Expected text of getComputedStyle for calc(1px * f(args)) (word-spacing)",
              "# and of rotate: f(args) for the angle functions, from ucrtbase.dll's f32",
              "# functions. Generated by scripts/ucrt_css_calc.py (python -I) - do not edit.",
              "# <fn> <args...> <text on Windows>"]
    stats = []
    for fn in ("sin", "cos", "tan", "asin", "acos", "atan", "atan2", "exp", "log", "pow", "hypot"):
        keys = sorted(k for k in a if k[0] == fn and k in b)
        different = [k for k in keys if a[k] != b[k]]
        same = [k for k in keys if a[k] == b[k]]
        # Spread over the list, not the first ones.
        step_d = max(1, len(different) // KEEP_DIFFERENT)
        quarter = max(1, len(same) // 4)
        take = different[::step_d][:KEEP_DIFFERENT] + same[quarter::quarter][:KEEP_SAME]
        stats.append("%s: %d of %d differ" % (fn, len(different), len(keys)))
        for k in take:
            chosen.append("%s %s" % (" ".join(k), a[k]))
    out.write_text("\n".join(chosen) + "\n")
    print("\n".join(stats))
    print("wrote %s" % out)


def main(argv) -> int:
    if len(argv) == 3 and argv[1] == "dump":
        dump(Path(argv[2]))
    elif len(argv) == 5 and argv[1] == "pick":
        pick(Path(argv[2]), Path(argv[3]), Path(argv[4]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
