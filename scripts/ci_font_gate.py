#!/usr/bin/env python3
"""CI font gate - assert the patched binary exposes exactly the Windows font
persona on EVERY host OS (Windows / Linux / macOS), with zero host-font leak.

The patched binary is bundle-only: at font-list construction it drops every host
system font and exposes only the bundled Windows-11 family set (the exposed set
IS the bundle). This gate launches the binary on its NATIVE runner - so
macOS/CoreText, Linux/fontconfig and Windows/DWrite are each tested for real -
enumerates the visible families with the same width-probe web detectors use, and
asserts three things:

  1. the detected family set == the canonical Windows set (EXPECTED): the SAME
     set on all three platforms. A leaked host font or a missing Windows one
     fails here. This is the "identical on every OS" contract.
  2. no known host family is visible (macOS: Helvetica Neue / Geneva / Menlo ...;
     Linux: DejaVu / Ubuntu ...) - a POSITIVE proof that block-at-birth ran for
     this platform's backend, not just "no obvious tell".
  3. the CSS generics resolve to Windows fonts (serif=Times New Roman,
     sans-serif=Arial, monospace=Consolas) and system-ui=Segoe UI.

This is the macOS validator the local Win/Linux gate cannot be - there is no
local Mac, so CoreText is only ever exercised here. Headless, no proxy, no
secrets, loopback-free (about:blank + arrow-function evaluate, which is not
eval and carries no CSP problem) -> safe in public CI.

Usage:  python ci_font_gate.py <firefox-binary>
Exit 0 + "FONT GATE OK ..." on success; non-zero + the diff on failure.
"""
from __future__ import annotations

import sys

# The canonical Windows-11 family set the bundle exposes. Verified byte-for-byte
# identical on Windows/DWrite and Linux/fontconfig; macOS/CoreText must match it
# too. Keep this sorted and in sync with the bundle (browser/fonts/moz.build).
EXPECTED = [
    "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Cambria Math",
    "Candara", "Comic Sans MS", "Consolas", "Constantia", "Corbel",
    "Courier New", "Ebrima", "Franklin Gothic", "Franklin Gothic Medium",
    "Gabriola", "Gadugi", "Georgia", "Impact", "Ink Free", "Javanese Text",
    "Leelawadee", "Leelawadee UI", "Lucida Console", "Lucida Sans Unicode",
    "MS Gothic", "MS PGothic", "MS UI Gothic", "MV Boli", "Malgun Gothic",
    "Marlett", "Microsoft Himalaya", "Microsoft JhengHei",
    "Microsoft JhengHei UI", "Microsoft New Tai Lue", "Microsoft PhagsPa",
    "Microsoft Sans Serif", "Microsoft Tai Le", "Microsoft Uighur",
    "Microsoft YaHei", "Microsoft YaHei UI", "Microsoft Yi Baiti",
    "MingLiU-ExtB", "Mongolian Baiti", "Myanmar Text", "NSimSun", "Nirmala UI",
    "PMingLiU-ExtB", "Palatino Linotype", "Segoe Fluent Icons",
    "Segoe MDL2 Assets", "Segoe Print", "Segoe Script", "Segoe UI",
    "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Symbol", "SimSun",
    "SimSun-ExtB", "Sitka Small", "Sylfaen", "Symbol", "Tahoma",
    "Times New Roman", "Trebuchet MS", "Verdana", "Webdings", "Wingdings",
    "Wingdings 2", "Wingdings 3", "Yu Gothic", "Yu Gothic UI",
]

# Host families that must NEVER be visible - one per backend. Their presence is a
# hard fail (block-at-birth did not run for this OS). These are decoys added to
# the probe list; they must all come back absent.
HOST_MUST_BE_ABSENT = [
    # macOS / CoreText
    "Helvetica Neue", "Geneva", "Menlo", "Monaco", "Avenir", "Lucida Grande",
    "Apple SD Gothic Neo", "PingFang SC",
    # Linux / fontconfig
    "DejaVu Sans", "Liberation Sans", "Ubuntu", "Nimbus Sans", "Noto Sans",
    # Office / non-standard families intentionally dropped from the bundle
    "Century Gothic", "Agency FB", "Monotype Corsiva", "Pristina",
]

# CSS generic -> the Windows family it must resolve to under bundle-only.
GENERICS = {
    "serif": "Times New Roman",
    "sans-serif": "Arial",
    "monospace": "Consolas",
    "system-ui": "Segoe UI",
}

# Families that live inside a .ttc TrueType Collection (several faces packed in
# one file). Being *listed* is not enough: without a per-face index the table
# lookup reads the first font of the collection, so every other face silently
# falls back to a default one. That is invisible to the presence probe below
# (the family is registered either way) but wrecks the persona for CJK text.
# The FF150->151 rebase dropped exactly that fix and only 7 of these loaded.
#
# Calibrated on firefox-17, the known-good release: these are the families that
# demonstrably render the CJK sample with their own face there. Deliberately
# NOT listed: "SimSun-ExtB" (covers Unicode Ext-B, not the BMP characters in
# the sample, so it legitimately falls back) and the "... UI" variants of YaHei
# and JhengHei (they already fall back on firefox-17, so requiring them would
# fail the known-good build). Keep this list as a regression detector, not an
# aspiration: everything here loads on firefox-17 and must keep loading.
TTC_FAMILIES = [
    "Microsoft YaHei", "Microsoft JhengHei",
    "MS Gothic", "MS PGothic", "MS UI Gothic",
    "SimSun", "NSimSun",
    "Yu Gothic", "Yu Gothic UI", "Malgun Gothic",
    "MingLiU-ExtB", "PMingLiU-ExtB",
]

# Width+height probe (the offsetWidth method real detectors use): a family is
# "present" if styling text in it renders at a different size than the three CSS
# base generics. For the generics, return the measured size of each generic and
# of its target Windows family so the caller can assert they coincide.
DETECT_JS = r"""(arg) => {
  const bases = ['monospace', 'sans-serif', 'serif'];
  const sample = 'mmmmmmmmmmlli WwQ 0123456789 gjpqy';
  const sp = document.createElement('span');
  sp.style.cssText =
    'position:absolute;left:-9999px;font-size:72px;white-space:nowrap;';
  sp.textContent = sample;
  document.body.appendChild(sp);
  const size = (ff) => { sp.style.fontFamily = ff; return sp.offsetWidth + 'x' + sp.offsetHeight; };
  const bw = {};
  for (const b of bases) bw[b] = size(b);
  const present = {};
  for (const f of arg.cands) {
    present[f] = bases.some((b) => size("'" + f + "'," + b) !== bw[b]);
  }
  const gen = {};
  for (const g of arg.generics) gen[g] = size(g);
  const genref = {};
  for (const w of arg.targets) genref[w] = size("'" + w + "'");
  // .ttc face-loading probe: render CJK glyphs, which only the family's own
  // face can draw. If the size matches a nonexistent-font fallback, the face
  // never loaded and the text is being drawn by a default face instead.
  sp.textContent = arg.cjk;
  const fb = size("'__NoSuchFontXYZ__'");
  const ttcload = {};
  for (const f of arg.ttc) {
    ttcload[f] = size("'" + f + "','__NoSuchFontXYZ__'") !== fb;
  }
  document.body.removeChild(sp);
  return { present, gen, genref, ttcload };
}"""

# Suppress the new-tab machinery so the launch is quiet (mirrors ci_drive_gate).
_PREFS = {
    "browser.startup.page": 0,
    "browser.newtabpage.enabled": False,
    "browser.newtab.preload": False,
    "browser.newtabpage.activity-stream.enabled": False,
}


def main(exe: str) -> int:
    from playwright.sync_api import sync_playwright

    cands = EXPECTED + HOST_MUST_BE_ABSENT
    arg = {
        "cands": cands,
        "generics": list(GENERICS.keys()),
        "targets": list(GENERICS.values()),
        "ttc": TTC_FAMILIES,
        "cjk": "中文字体測試あア漢字",
    }
    with sync_playwright() as p:
        browser = p.firefox.launch(executable_path=exe, headless=True,
                                   firefox_user_prefs=_PREFS)
        try:
            page = browser.new_page()
            page.goto("about:blank")
            r = page.evaluate(DETECT_JS, arg)
        finally:
            browser.close()

    detected = {f for f, v in r["present"].items() if v}
    expected = set(EXPECTED)
    missing = sorted(expected - detected)
    # Anything detected that isn't in EXPECTED (host leaks land here too).
    extra = sorted(detected - expected)
    leaked_host = [h for h in HOST_MUST_BE_ABSENT if r["present"].get(h)]
    gen_bad = []
    for g, want in GENERICS.items():
        got, ref = r["gen"].get(g), r["genref"].get(want)
        if got != ref:
            gen_bad.append(f"{g} -> {got} (expected {want} = {ref})")

    n = len(detected)
    print(f"[font-gate] {exe}")
    print(f"[font-gate] detected {n} families (expected {len(EXPECTED)})")
    if missing:
        print(f"[font-gate] MISSING (in bundle, not exposed): {missing}")
    if extra:
        print(f"[font-gate] UNEXPECTED (exposed, not in canonical set): {extra}")
    if leaked_host:
        print(f"[font-gate] HOST LEAK (block-at-birth did not run!): {leaked_host}")
    if gen_bad:
        print(f"[font-gate] GENERIC MISMATCH: {gen_bad}")
    ttc_bad = sorted(f for f in TTC_FAMILIES if not r.get("ttcload", {}).get(f))
    if ttc_bad:
        print(f"[font-gate] TTC FACE NOT LOADED (listed but falls back to a "
              f"default face for CJK): {ttc_bad}")

    ok = (not missing and not extra and not leaked_host and not gen_bad
          and not ttc_bad)
    if ok:
        print(f"FONT GATE OK - exactly the {n} Windows families, host-leak 0, "
              f"generics map to Windows (serif/sans/mono/system-ui), "
              f"{len(TTC_FAMILIES)}/{len(TTC_FAMILIES)} .ttc faces load.")
        return 0
    print("FONT GATE FAILED - the exposed set does not match the Windows "
          "persona on this OS (see the diff above).")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python ci_font_gate.py <firefox-binary>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
