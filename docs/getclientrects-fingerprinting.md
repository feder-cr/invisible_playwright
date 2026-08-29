---
title: "getClientRects fingerprinting: subpixel geometry as ID"
description: "getClientRects leak subpixel geometry that hashes into cross-platform fingerprints. Why they betray faked OS, and how real Windows renders stay self-consistent."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 17
---


# getClientRects fingerprinting: subpixel geometry as ID

Most fingerprinting pages measure something you think of as visual: a canvas drawing,
a WebGL string, an audio buffer. This one measures the position of text on the page,
down to fractions of a pixel, and it does it without drawing anything you can see.

[`element.getClientRects()`](https://developer.mozilla.org/en-US/docs/Web/API/Element/getClientRects) and [`element.getBoundingClientRect()`](https://developer.mozilla.org/en-US/docs/Web/API/Element/getBoundingClientRect) return the geometry
of a laid-out element: its top, left, width and height as [floating-point numbers](https://www.w3.org/TR/cssom-view-1/). Those
numbers are not round. They carry a long fractional tail, and that tail is decided by
the same stack a real browser cannot hide: the font rasterizer, the device pixel ratio,
and the operating system's text layout. A script reads a handful of rects, hashes the
floats, and now has a value that is stable for your machine and different for a machine
pretending to be yours.

This page is what that measurement actually is, why it is a cross-platform tell in
particular, and why a browser genuinely rendering on Windows produces Windows-shaped
rects for free. It also answers the question honestly: matching geometry proves your
platform is consistent, and nothing more.

## What the rects actually contain

When the layout engine positions a run of text, it does not snap glyphs to whole
pixels. Fractional advances accumulate across characters, subpixel positioning nudges
each glyph, and the resulting box has a width like `152.34375` rather than `152`. The
exact fraction depends on:

- **The font rasterizer and its hinting.** How the platform turns an outline into
  positioned glyphs differs between operating systems, and the difference lands in the
  fractional part of every text box.
- **The device pixel ratio.** A DPR that no real display uses, or one that disagrees
  with the reported screen, shifts the rounding and the fractional tail with it.
- **The OS text layout and font stack.** Which fonts are actually installed, and which
  one the platform substitutes when the requested face is missing, changes the metrics
  the box is built from.

Read one box and it is just a number. Read a small panel of elements with different
fonts, sizes and styles, and the collection of fractional tails is a signature. This is
the same family of measurement as [text metrics fingerprinting](measuretext-textmetrics-fingerprinting.md):
both read layout numbers a real render produces and a fake render cannot fake into
agreement.

## Why float geometry is a cross-platform tell

Here is the part that turns a fingerprint into a detector.

The width of a text box is not a free parameter. It is a consequence of the platform.
A browser claiming to be Windows in its user agent, but rendering with a Linux font
stack and a Linux rasterizer, produces text boxes with the wrong fractional tails for
Windows. Nothing on the page looks wrong to a human. The numbers are wrong to a script
that knows what Windows rects look like.

So the detector does not ask "is this box unusual". It asks "does this geometry match
the platform this browser claims". That is the more dangerous question, and it is the
same shape as every other consistency check in this set: a value that is individually
plausible and disagrees with a value it should match. A pinned user agent, a spoofed
`navigator.platform`, a header generator that rewrites the OS string - none of them
touch the layout engine, so none of them move the rects. The disguise says Windows and
the geometry says something else.

You cannot patch your way out of this from JavaScript in a way that survives. Rounding
the rects to whole numbers is itself a signal: real browsers return fractional values,
so an integer-only response is a browser announcing it has been tampered with, the same
trap that [canvas noise injection](canvas-fingerprint-noise.md) falls into. And
generating plausible-looking fractions is generating a second thing that now has to
agree with the fonts, the DPR and the screen you also reported. The only response that
is consistent with the claimed platform is the one an actual render on that platform
produces.

## Why a real Windows render stays self-consistent

This is where the product does something a spoof cannot.

invisible_playwright is a Firefox patched at the C++ level, and when it runs on real
Windows the layout engine is genuinely rasterizing text on the Windows font stack at
the reported device pixel ratio. The rects it returns are Windows rects because they
were produced by Windows, not written to look like Windows. There is no separate
geometry spoof to keep in agreement with the fonts, because the fonts, the rasterizer
and the DPR are the ones actually doing the work. The `getBoundingClientRect` tail
agrees with the font list agrees with the screen agrees with the user agent, because
one real stack produced all of them.

That is the general principle behind the whole fingerprint here: consistency comes from
a real render rather than from a correction layer.
[Canvas and WebGL stay cross-platform-consistent for the same reason](canvas-webgl-cross-platform-consistency.md) -
they are drawn by the machine they claim to be, so no field has to be talked into
matching another.

The two-line launch, and the rect read this page is about:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # read the subpixel geometry a detector would hash
    rect = page.evaluate("""() => {
        const el = document.body;
        const r = el.getBoundingClientRect();
        return { top: r.top, left: r.left, width: r.width, height: r.height };
    }""")
    print(rect)   # fractional values, Windows-shaped, because Windows produced them
```

The `browser` object is a real Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser), so `page.evaluate`,
`getClientRects` and every other method behave exactly as documented upstream. Nothing
about reading geometry is special-cased.

Because the whole identity derives from the seed, the geometry is reproducible: the same
`seed=42` gives the same screen, the same DPR and the same font stack, so a rect you
read today you can read again next week while you bisect a failure. Change the seed and
you get a different consistent machine, not a different set of contradictions.

```python
# same seed -> same screen, same DPR, same font stack -> same rects, run after run
with InvisiblePlaywright(seed=42) as browser:
    ...
```

## What matching geometry does not prove

The honest boundary, because this is the part that gets overstated.

Windows-consistent rects prove one thing: the browser's layout output matches the
platform it claims, so this particular cross-platform tell reads clean. That is why
invisible_playwright passes most fingerprint-level checks - the geometry, the fonts, the
canvas, the TLS handshake and the driver layer all read as a genuine Firefox on the OS
it says it is, because that is what they are. It is a real render, not a table of spoofed
answers.

It does not prove the session is a trusted human, and it does not touch the things that
live outside the browser:

- **IP reputation.** A perfectly consistent browser on a datacenter address is still on
  a datacenter address. Geometry does not move an IP. Supply a clean residential exit.
- **Per-account quotas and rate limits.** Velocity is counted against your account and
  your address regardless of how real the rects are.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, a perfectly uniform request cadence - none of that is a fingerprint, and
  none of it is fixed by geometry. You supply human pacing.

Matching geometry removes one reason to be flagged. It does not remove the others, and a
page that promised otherwise would be lying to you. Treat it as one clean surface among
several you are responsible for, in the order [the detection checklist](playwright-detected-as-bot.md)
works through them.

## Conclusion

Subpixel DOM geometry is a fingerprint because the fractional tails of `getClientRects`
and `getBoundingClientRect` are decided by the font rasterizer, the device pixel ratio
and the OS text layout, and a script can hash them into a stable value and check it
against the platform you claim. It is a cross-platform tell in particular: a faked OS
produces the wrong tails, and rounding or forging them only trades one signal for
another. A Firefox genuinely rendering on real Windows returns Windows rects because
Windows produced them, so the geometry agrees with the fonts, the screen and the user
agent without a correction layer to keep in sync. That earns you a clean surface. It does
not earn you a clean IP, a fresh quota, or human timing, and those are still yours to
bring.

## Short answers to the questions that lead here

**What is getClientRects fingerprinting?** A script reads the floating-point geometry of
laid-out elements and hashes the subpixel values into an identifier, because those
fractions are decided by the font rasterizer, the DPR and the OS text layout.

**Why are the width and height not whole numbers?** Because the layout engine positions
glyphs at fractional advances and does not snap boxes to whole pixels. The fractional
tail is the part that carries the fingerprint.

**Can I just round the rects to hide it?** No. Real browsers return fractional values, so
an integer-only response is itself a tampering signal, and forged fractions still have to
agree with your fonts, DPR and screen.

**Why does a spoofed user agent fail this check?** Because changing the OS string does
not change the layout engine, so the geometry keeps reporting the real platform while the
user agent claims another. The two disagree, and the detector reads the disagreement.

**Does invisible_playwright pass geometry checks?** On real Windows the rects are Windows
rects because the engine genuinely rendered on Windows, so this surface reads consistent
with the rest of the fingerprint.

**If the geometry matches, am I safe?** You have cleared one cross-platform tell. IP
reputation, account quotas, rate limits and behaviour are separate, and a consistent
browser on a bad IP or with robotic timing still gets flagged.

## Sources

- The W3C [CSSOM View](https://www.w3.org/TR/cssom-view-1/) specification, which defines
  `getClientRects` and `getBoundingClientRect` on the Element interface and specifies the
  returned rectangles as floating-point values, retrieved 2026-08-28.
- [MDN: `Element.getClientRects()`](https://developer.mozilla.org/en-US/docs/Web/API/Element/getClientRects)
  and [MDN: `Element.getBoundingClientRect()`](https://developer.mozilla.org/en-US/docs/Web/API/Element/getBoundingClientRect),
  retrieved 2026-08-28, the reference documentation for both methods; the getClientRects
  page states outright that fractional pixel offsets are possible.
- This project's fingerprint gates, which compare a full field panel against a stock
  browser on the same machine rather than reading a verdict, and which treat a value that
  disagrees with the claimed platform as a failure.

**See also:** [text metrics fingerprinting](measuretext-textmetrics-fingerprinting.md) for
the closely related layout-number tell, [cross-platform canvas and WebGL consistency](canvas-webgl-cross-platform-consistency.md)
for why a real render needs no correction layer, and [how to test whether your browser is
detected](how-to-test-bot-detection.md) for the compare-against-stock method that catches
a geometry mismatch.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The rects are Windows rects
because Windows drew them, not because we wrote them down.*
