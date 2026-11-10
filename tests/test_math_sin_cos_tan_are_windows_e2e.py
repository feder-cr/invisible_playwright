"""Math.sin, Math.cos and Math.tan give Windows' bytes on every OS.

A page reads the last bit of Math.sin(Math.PI / 4): Windows Firefox computes it
with ucrtbase.dll and gets 0.7071067811865476 (0x3fe6a09e667f3bcd); Linux
Firefox got 0.7071067811865475 (...bcc), from fdlibm, which the engine used
outside Windows (javascript.options.use_fdlibm_for_sin_cos_tan), or from glibc
with that pref off. Measured over 30,372 distinct hard inputs (near multiples of
pi/4, the reduction thresholds, huge arguments; NaN results left out): fdlibm
differs from ucrtbase on 441, glibc 2.43 on 651, one or both on 832 (Windows 11,
WSL Ubuntu, 2026-11-08).

The engine now keeps Windows' value of the pref (false) everywhere, and on Linux
libxul defines sin, cos and tan itself with ucrtbase's results (gfx/idwr/
libm_ucrt.c in the engine series), so every call inside libxul gets them.

The test reads the result bits of the 4,516 inputs of
tests/data/ucrt_sin_cos_tan.txt (those 832, and every 8th of the others), in
the same three functions, round after round for at least MIN_SECONDS each, and
every round must equal ucrtbase. A loop that hot for that long is where the JITs
take over, but no page-visible API tells which tier ran a call, so the test does
not claim one: it shows that the result does not change over a long hot run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data" / "ucrt_sin_cos_tan.txt"

#: Each function runs over its ~1,500 inputs for at least this long (and at
#: least MIN_ROUNDS rounds): millions of calls from the same loop.
MIN_SECONDS = 3
MIN_ROUNDS = 12

RUN = """
([cases, minRounds, minSeconds]) => {
  const buf = new DataView(new ArrayBuffer(8));
  const fromHex = (h) => { buf.setBigUint64(0, BigInt("0x" + h)); return buf.getFloat64(0); };
  const toHex = (d) => { buf.setFloat64(0, d); return buf.getBigUint64(0).toString(16).padStart(16, "0"); };
  // One monomorphic function per Math function, as a page would write them.
  const fns = {
    sin(xs, out) { for (let i = 0; i < xs.length; i++) out[i] = Math.sin(xs[i]); },
    cos(xs, out) { for (let i = 0; i < xs.length; i++) out[i] = Math.cos(xs[i]); },
    tan(xs, out) { for (let i = 0; i < xs.length; i++) out[i] = Math.tan(xs[i]); },
  };
  const wrong = [];
  const rounds = {};
  for (const name of Object.keys(fns)) {
    const mine = cases.filter((c) => c[0] === name);
    const xs = new Float64Array(mine.map((c) => fromHex(c[1])));
    const want = mine.map((c) => c[2]);
    const out = new Float64Array(xs.length);
    const t0 = performance.now();
    let r = 0;
    for (; r < minRounds || performance.now() - t0 < minSeconds * 1000; r++) {
      fns[name](xs, out);
      for (let i = 0; i < xs.length; i++) {
        const got = toHex(out[i]);
        if (got !== want[i] && wrong.length < 1000) wrong.push([r, name, mine[i][1], got, want[i]]);
      }
    }
    rounds[name] = r;
  }
  return {wrong, rounds, pi4: toHex(Math.sin(Math.PI / 4)), pi4Text: String(Math.sin(Math.PI / 4))};
}
"""


def _cases():
    out = []
    for line in DATA.read_text().splitlines():
        if line and not line.startswith("#"):
            fn, x, want = line.split()
            out.append([fn, x, want])
    return out


@pytest.mark.unit
def test_the_data_holds_the_inputs_that_tell_the_libraries_apart():
    cases = _cases()
    assert {c[0] for c in cases} == {"sin", "cos", "tan"}
    assert len(cases) == 4516
    # Math.PI / 4 is in the list, with ucrtbase's sin.
    assert ["sin", "3fe921fb54442d18", "3fe6a09e667f3bcd"] in cases


@pytest.mark.e2e
def test_math_sin_cos_tan_bytes_are_ucrtbase_over_a_long_hot_loop(firefox_binary, engine_at_least):
    engine_at_least("firefox-61")
    from invisible_playwright import InvisiblePlaywright

    cases = _cases()
    with InvisiblePlaywright(seed=5807, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        result = page.evaluate(RUN, [cases, MIN_ROUNDS, MIN_SECONDS])

    assert result["pi4"] == "3fe6a09e667f3bcd", result["pi4"]
    assert result["pi4Text"] == "0.7071067811865476"
    assert all(r >= MIN_ROUNDS for r in result["rounds"].values()), result["rounds"]
    wrong = result["wrong"]
    by_round = {}
    for r, name, *_ in wrong:
        by_round.setdefault(r, {}).setdefault(name, 0)
        by_round[r][name] += 1
    assert not wrong, "%d+ results differ from ucrtbase (round: {fn: count}) %s; first: %s; rounds %s" % (
        len(wrong), by_round, wrong[:5], result["rounds"])
