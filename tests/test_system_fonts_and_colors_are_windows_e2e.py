"""The CSS system fonts and system colours are Windows retail's, on every OS.

``font: menu`` (and caption, icon, message-box, small-caption, status-bar) and
every CSS system colour keyword, in both colour schemes, are read with
getComputedStyle. Our engine must answer what Firefox 159 retail answers on
Windows: fonts come from the ui.font.* prefs the core declares (never Pango or
GTK), colours from the declared palette or from Firefox's own stand-ins.

tests/data/retail_159_system_theme.json is the measurement: Firefox 159 retail
for Windows, headless, an empty profile, read through Marionette by
scripts/measure_retail_system_theme.py. ``system-ui`` is a family keyword
resolved by the font list (retail: the menu font), so its computed family,
size, weight, style and stretch are compared like the others.

Every mismatch is listed in the failure, not just the first.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

RETAIL = json.loads((Path(__file__).parent / "data" / "retail_159_system_theme.json").read_text())

READ = """
({fonts, colors}) => {
  const out = {fonts: {}, light: {}, dark: {}};
  for (const f of fonts) {
    const d = document.createElement('div');
    d.style.font = f === 'system-ui' ? '16px system-ui' : f;
    document.body.appendChild(d);
    const c = getComputedStyle(d);
    out.fonts[f] = {family: c.fontFamily, size: c.fontSize, weight: c.fontWeight,
                    style: c.fontStyle, stretch: c.fontStretch};
    d.remove();
  }
  for (const scheme of ['light', 'dark']) {
    for (const k of colors) {
      const d = document.createElement('div');
      d.style.colorScheme = scheme;
      d.style.color = k;
      document.body.appendChild(d);
      out[scheme][k] = getComputedStyle(d).color;
      d.remove();
    }
  }
  return out;
}
"""


@pytest.mark.unit
def test_the_reference_has_every_font_and_both_schemes():
    assert set(RETAIL["fonts"]) == {
        "menu", "caption", "icon", "message-box", "small-caption", "status-bar", "system-ui"}
    assert set(RETAIL["light"]) == set(RETAIL["dark"])
    assert {"Highlight", "HighlightText", "Mark", "MarkText", "SelectedItem",
            "AccentColor", "LinkText", "VisitedText", "ActiveText"} <= set(RETAIL["light"])
    # Segoe UI at 9pt: 12px, normal weight, upright.
    assert RETAIL["fonts"]["menu"] == {
        "family": "Segoe UI", "size": "12px", "weight": "400", "style": "normal",
        "stretch": "100%"}


@pytest.mark.e2e
def test_system_fonts_and_colours_match_windows_retail(firefox_binary, engine_at_least):
    engine_at_least("firefox-61")
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=6022, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        got = page.evaluate(READ, {"fonts": list(RETAIL["fonts"]),
                                   "colors": list(RETAIL["light"])})

    wrong = []
    for name, want in RETAIL["fonts"].items():
        have = got["fonts"][name]
        if have != want:
            wrong.append("font %s: page %r, retail %r" % (name, have, want))
    for scheme in ("light", "dark"):
        for name, want in RETAIL[scheme].items():
            if got[scheme][name] != want:
                wrong.append("%s %s: page %s, retail %s" % (scheme, name, got[scheme][name], want))
    assert not wrong, ("%d system values differ from Windows retail:\n  %s"
                       % (len(wrong), "\n  ".join(wrong)))
