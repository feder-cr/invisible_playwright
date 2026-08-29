---
title: "measureText and TextMetrics as a fingerprinting surface"
description: "measureText and TextMetrics leak font and platform data with no permission prompt. What the fields expose, why per-glyph noise backfires, and how we fixed it."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 7
---


# measureText and TextMetrics as a fingerprinting surface

[`CanvasRenderingContext2D.measureText()`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/measureText) is a fingerprinting surface: it returns a
[`TextMetrics`](https://developer.mozilla.org/en-US/docs/Web/API/TextMetrics) object whose ten-plus numbers depend on the exact font file, shaping engine
and rendering backend, so the same probe string measures differently across platforms and
font sets. It gets a fraction of the attention canvas image fingerprinting does, which is
backwards from how often it is actually used. It needs no permission prompt, no pixel
readback, and no `getImageData()` call a privacy extension might flag. It is one line, it
is fast, and it is a genuine font and platform signal.

## What measureText actually returns

Calling `measureText()` on a string returns more than a width: a `TextMetrics`
object carrying ten-plus numeric fields covering width, bounding box, and baseline
offsets.

```js
const m = ctx.measureText("some probe string")
m.width
m.actualBoundingBoxAscent
m.actualBoundingBoxDescent
m.actualBoundingBoxLeft
m.actualBoundingBoxRight
m.fontBoundingBoxAscent
m.fontBoundingBoxDescent
m.emHeightAscent
m.emHeightDescent
m.hangingBaseline
m.alphabeticBaseline
m.ideographicBaseline
```

That is ten-plus numbers per call, each one a function of the exact font file, the exact
shaping engine, and the exact rendering backend behind it. Run the same string against a
list of probe fonts and you have a measurement surface with no image involved at all,
which is why `TextMetrics` shows up inside general-purpose fingerprinting libraries
alongside canvas image hashing rather than instead of it.

## Why the natural fix is per-glyph noise, and why the naive version of it fails

The obvious defence, the one [canvas image fingerprinting](canvas-fingerprint-noise.md)
also reaches for first, is to add a small varying offset so repeated measurements do not
collapse to one value across every install of the same browser version. We shipped a
version of that and had to replace it.

The first attempt added a small random-looking offset to every glyph's advance,
cumulatively, as the string was shaped. It worked for a two- or three-character probe.
It stopped working for a realistic one: the offset scaled with string length, so a
longer probe string could drift by several pixels from what a real browser would ever
report for that font at that size. A per-session number that grows with input length in
a way no real rendering pipeline does is its own tell, and it measurably raised a
commercial fingerprint scanner's tampering signal on exactly the profiles that used
smaller fonts, where the accumulated drift was largest relative to the string.

The fix was to stop making it cumulative. A single, small, bounded offset, applied once,
to the advance of the last glyph in the run only. Same per-seed determinism, same
purpose, but the total possible drift on any string is now one small bounded value
instead of one that grows with how much text you measure. The commercial scanner's
tampering signal returned to its baseline once this shipped.

## The width is not the whole surface: vertical metrics need a different fix

Fixing the horizontal advance does not touch the vertical fields -
`fontBoundingBoxAscent`, `emHeightAscent`, the baselines, and friends. Those come from a
different part of the rendering pipeline, and on a machine built to look like one
platform while actually running on another, they told the truth about the platform
underneath: measured 0.9 to 3 pixels of difference between the two rendering backends
for the same declared font, on the same string.

The reason a per-glyph shaping offset cannot fix this is that these values are not glyph
advances, they are the font's own vertical design metrics, and different platforms read
them through different code paths that round slightly differently. Patching the shaper
does nothing to a value the shaper never touches.

The fix that closed the gap was to stop asking the rendering backend for these values at
all, and read them directly from the font file's own metrics table instead - the same
bytes on every platform, since it is the same font file. A rendering backend's read of
those bytes can round differently by platform; the bytes themselves cannot. Measured
after the change: a difference of about three thousandths of a pixel between the two
platforms, down from a difference of one to three whole pixels, on the same set of test
families.

## The general lesson, not specific to fonts

Two mistakes, two different shapes, and the same underlying principle both times:

**Noise that accumulates with input length is not noise, it is a growing tell.**
Anything you add per-unit-of-input needs a ceiling independent of how much input there
is, or the total eventually leaves the range real output ever falls in.

**A value can come from a code path your fix never reaches.** Two properties that look
like they should move together, an advance and a baseline, both descriptions of the same
rendered glyph, can be produced by genuinely separate mechanisms under the hood. Fixing
one and assuming the other followed is how a fix ships half-done.

## Checking your own

Measure a handful of strings at a handful of sizes, more than once, in the same session
and across a fresh one. Within a session, the numbers should be identical for identical
input; that is expected and not itself a signal. Across a fresh session with the same
identity, or the same identity reused on a different host, is where a growing-with-length
drift or a host-dependent vertical value would show up first.

## Short answers to the questions that lead here

**Is measureText a real fingerprinting signal?** Yes, and it needs no permission prompt
and no pixel readback, unlike canvas image hashing.

**Why does adding noise to text metrics sometimes make things worse?** If the noise
accumulates per glyph, the total can grow past what any real rendering pipeline would
produce for a long string, which is itself detectable.

**Why do vertical text metrics differ between operating systems?** They come from the
font's own design metrics read through the platform's rendering backend, and backends
round those values slightly differently, independent of anything a shaping-level fix
touches.

**Does fixing measureText width also fix the baseline fields?** No. They are produced
by different code, and a fix for one does not reach the other.

**See also:** [why headless browsers render different fonts](headless-fonts-differ.md),
for the enumeration side of the same font surface; [how to make Linux and macOS report
real Windows fonts](bundled-fonts-cross-platform.md), for the architecture this metrics
fix sits inside; and [canvas fingerprint noise](canvas-fingerprint-noise.md), for the
same accumulation mistake made on pixels instead of text.

## Sources

- [MDN: `CanvasRenderingContext2D.measureText()`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/measureText)
  and [MDN: `TextMetrics`](https://developer.mozilla.org/en-US/docs/Web/API/TextMetrics),
  retrieved 2026-08-28, for the full field list the returned object carries.
- This project's own canvas and font-shaping patch catalogue, including the measured
  before/after on both the per-glyph accumulation fix and the vertical-metrics
  host-independence fix.
- A commercial fingerprint scanner's tampering signal, used as the before/after
  measurement for the accumulation fix.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, including the text-shaping layer that fed us this one
the hard way.*
