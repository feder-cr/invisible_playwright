---
title: "Emoji fingerprinting: why emoji look the same on any OS"
description: "Emoji usually leak the host OS, since each platform ships its own emoji font. Firefox draws colour emoji from a bundled font, so they look identical everywhere."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 10
---


# Emoji fingerprinting: why emoji look the same on any OS

Emoji look the same on any OS in Firefox because Firefox draws colour emoji from
its own bundled font rather than the platform's, so the render is host-independent.
That is worth explaining, because emoji are otherwise one of the oldest
operating-system tells there is. The same code point is drawn from a different font
on each platform, and those fonts do not agree on a single pixel: Windows renders
from Segoe UI Emoji, macOS from Apple Color Emoji, Android from Noto Color Emoji.
Paint one emoji into a canvas, hash the pixels, and you have a signal that separates
the three without asking a single JavaScript question about the platform.

That makes emoji an attractive check for a detector, and a trap for automation
that claims one OS while running on another. This page is about why that trap
mostly does not apply to Firefox, what still can go wrong, and how to read the
surface yourself instead of trusting a claim.

## Why emoji are a classic OS tell

Emoji are a classic OS tell because each platform ships its own colour-emoji font,
and those fonts render the same code point as different pixels: different shapes,
palettes, metrics, and coverage of newer code points. The mechanism is the same as
any font fingerprint, only sharper. A plain glyph like the letter "a" is drawn from
a text font, and text fonts differ enough between systems to matter, but the visual
gap between a Windows emoji and an Apple one is far larger than the gap between two
Latin text fonts, so the pixel hash separates cleanly.

It is also cheap to read. A detector does not need to enumerate anything or ask
for a permission. It draws a handful of emoji to an offscreen canvas, reads the
pixels back, and compares against a table of known per-OS renders. A browser
claiming Windows that produces the Apple emoji set has contradicted itself, and
[a contradiction is exactly what the tampering-focused suites look for](how-to-test-bot-detection.md).
The same logic sits behind [why a headless container often renders different fonts](headless-fonts-differ.md)
than the desktop it claims to be.

## Firefox draws its own emoji, and that changes the game

Here is the part most anti-detect writeups miss. Firefox does not use the
operating system's emoji font at all. It ships its own colour-emoji font, based on
Twemoji, inside the browser package, and it rasterises colour emoji from that
bundled file on every platform it runs on. A stock Firefox on Windows, a stock
Firefox on macOS and a stock Firefox on Linux all draw the same emoji from the
same font, because the font travels with the browser rather than coming from the
host.

That is a structural difference from Chrome, which defers to the platform emoji
font, and it is the reason emoji are a much weaker OS tell in Firefox than people
assume. The engine has already made the render host-independent.

| Browser | Where the emoji font comes from | Emoji as an OS tell |
|---|---|---|
| Firefox | Bundled with the browser (Twemoji-based) | Weak: the same render on every OS |
| Browsers using the OS font (e.g. Chrome) | The host operating system's emoji font | Strong: the render changes per OS |

This project builds on that property deliberately. To make text match Windows on
any host, the build bundles a set of real Windows font files and exposes only
those families, so a Linux server draws Latin, CJK and the rest from genuine
Windows glyphs rather than from whatever the host happens to have installed. The
same idea is covered in detail under [bundled fonts that render the same on every OS](bundled-fonts-cross-platform.md).

The emoji font is treated differently, and on purpose. The colour-emoji file is
authored by Mozilla, not supplied by Microsoft, so it is not a Windows system
family and it is not exposed as one of the spoofed Windows families that a page
can enumerate. It is kept as the emoji fallback instead. The result is the one you
want: emoji do not appear in the Windows family list, so they cannot contradict
it, and they still render, from the same engine-provided font a real Firefox uses.
An emoji drawn on the Linux server is byte-for-byte the emoji drawn on a Windows
desktop, because both come from the browser, not the box.

## Reading the emoji surface yourself

Do not take any of this on faith. Measure it. Every session is derived from a
seed, so a fixed seed gives you a fixed identity to read twice and compare, using
the same [`toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
canvas readback every emoji fingerprinting probe relies on.

```python
from invisible_playwright import InvisiblePlaywright

# Written as unicode escapes so the source stays ASCII; each is a real emoji.
EMOJI = [
    "\U0001F600",              # grinning face
    "\U0001F44D",              # thumbs up
    "\U00002764\U0000FE0F",    # red heart
    "\U0001F1EE\U0001F1F9",    # regional-indicator flag
]

def emoji_hash(page, ch):
    return page.evaluate(
        """(ch) => {
            const c = document.createElement('canvas');
            c.width = 64; c.height = 64;
            const ctx = c.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '48px sans-serif';
            ctx.fillText(ch, 4, 4);
            return c.toDataURL();
        }""",
        ch,
    )

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    for ch in EMOJI:
        print(repr(ch), emoji_hash(page, ch)[:48])
```

Run that on your laptop and on the server you deploy to. The emoji data URLs
should match across the two machines, because the emoji font is the same on both.
If they differ, something is drawing emoji from a host font, and that is the bug
to chase.

You can also confirm it visually, which is the check a pixel hash cannot lie
about. Reading the screenshot rather than the log catches the case where the text
extractor was looking at the wrong thing:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.set_content(
        "<div style='font-size:64px'>"
        "\U0001F600 \U0001F44D \U00002764\U0000FE0F \U0001F1EE\U0001F1F9"
        "</div>"
    )
    page.screenshot(path="emoji.png")
    # Open emoji.png. The colour emoji must be present and identical on every host.
```

Two runs with the same seed must produce identical emoji hashes, the same way any
canvas read must be stable within a session. A value that changes per call is
[the cheapest tampering signal a detector can ask for](canvas-fingerprint-noise.md),
and it applies to emoji exactly as it applies to plain text.

## Where an emoji difference can still creep in

A non-tell is not a solved problem you can stop thinking about. A few ways emoji
can still differ from a real Firefox:

- **A second spoofing layer.** If you stack a JavaScript patch or a font-faking
  plugin on top of the engine, it can intercept canvas reads and reshape the
  emoji output, producing a value that no real Firefox emits. One layer is the
  rule; two layers contradict each other.
- **Text presentation of an emoji.** Some code points can render as a flat
  monochrome glyph or as a colour emoji depending on a variation selector. If your
  content omits the selector on a code point that needs it, you may get a text
  glyph where a human would see colour. That is a content bug, not an engine one,
  but it still shows up in a pixel comparison.
- **Very new code points.** Any bundled emoji font has a coverage date. A code
  point newer than the font falls back to a tofu box or a component render, and if
  the reference browser you compare against ships a newer font, the two disagree.
  Compare against the same Firefox build you deploy, not against a browser on your
  desktop.
- **Assuming the emoji surface stands in for the whole font surface.** Emoji being
  clean says nothing about Latin, CJK or the family enumeration. Those are separate
  checks and want their own comparison.

The general habit is the one that governs every surface: compare the automated
browser against a stock one on the same machine, field by field, and treat a
suppressed or blank emoji as a failure rather than a pass.

## What we measured across operating systems

The claim is only worth as much as the measurement behind it. The cross-OS font
gate runs the browser on a Linux host that has no Windows fonts installed at all,
and reads what it renders. The Windows text families all resolve from the bundled
files, none of the host's own Linux fonts leak into the enumeration, and Latin,
Japanese, simplified and traditional Chinese, Korean and the colour emoji all
render genuinely rather than as fallback boxes.

The emoji specifically came back identical to the Windows-host render, which is
exactly what the engine-provided emoji font predicts: the host has no say in it.
That is the whole point of the design. On a signal that separates Windows, macOS
and Android at a glance for most automation, a Firefox built this way produces the
same emoji everywhere, so the emoji tell has nothing to catch. It is one field
among the many a session derives from its seed, and it is one that costs a
container-based scraper nothing to get wrong and everything to get right.

## Conclusion

Emoji are a strong OS tell for browsers that draw them from the operating system,
and a weak one for Firefox, which draws them from a font it carries itself. This
project leans on that: text is made to match Windows from a bundle of real Windows
fonts, while the colour-emoji font stays the engine's own Mozilla-authored file, so
emoji never enter the spoofed Windows family list yet always render from the same
source a real Firefox uses. Measured across operating systems, the emoji come out
identical. The right way to trust that is not to trust it: fix a seed, draw the
emoji twice, and read the pixels.

## Short answers to the questions that lead here

**Do emoji reveal my operating system?** In most browsers yes, because each
platform draws them from a different emoji font. In Firefox much less so, because
Firefox rasterises colour emoji from its own bundled font on every platform.

**Why do my emoji look the same on Windows and Linux with this tool?** Because the
emoji font travels with the browser rather than coming from the host, so the host
OS does not change the render.

**Can a detector tell the OS from a canvas emoji?** It can try, by hashing the
pixels of a drawn emoji against a per-OS table. With an engine-provided emoji font
the render is host-independent, so that table has nothing distinguishing to match.

**Are the emoji spoofed or faked?** No. They are drawn from Firefox's own bundled
emoji font, unmodified. Faking them would create a value no real Firefox emits,
which is the opposite of what you want.

**Why is the emoji font not in the Windows font list I can enumerate?** Because it
is a Mozilla-authored file, not a Windows system font. Exposing it as a Windows
family would be a contradiction; keeping it as the emoji fallback is not.

**Should I still test the emoji surface?** Yes. Fix a seed, draw the same emoji
twice, and confirm the hashes match within a session and across the machines you
deploy on. A blank or changing emoji is a failure, not a pass.

## Sources

- This project's font architecture and its cross-OS font gate, which runs the
  browser on a Linux host with no Windows fonts and reads what it renders,
  including colour emoji (decision log, 2026-06-20).
- Firefox's use of a bundled colour-emoji font in place of the platform emoji
  font, verified against a stock build on more than one operating system.
- [`HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
  (MDN), the canvas readback method the emoji hash in this page's code sample
  relies on.
- The public detection suites named across this documentation set, read from
  their own source rather than from a rendered verdict.

**See also:** [bundled fonts that render the same on every OS](bundled-fonts-cross-platform.md),
[why headless renders different fonts](headless-fonts-differ.md), and
[the method for testing whether a browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The emoji came back
identical on a Linux server with no Windows fonts, which is the whole reason this
page exists.*
