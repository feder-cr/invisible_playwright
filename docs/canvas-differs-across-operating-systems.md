---
title: "Why the same canvas draws differently on Windows and Linux"
description: "Canvas fingerprints differ across Windows and Linux because the rasteriser, font files, hinting and GPU path differ, not the canvas drawing code."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 15
---


# Why the same canvas draws differently on Windows and Linux

Two machines running the exact same `fillText()` and `toDataURL()` calls can return
different bytes. A canvas hash never measures the drawing code alone: it measures the
text rasteriser, the actual font file behind that family name, the subpixel and
hinting policy, and whether a GPU or a software path drew it. Change any one and the
pixels change, with nothing wrong in the script itself.

## What actually differs, mechanically, between two operating systems

A `fillText()` call looks like one instruction, but several layers sit underneath it
before a pixel comes back, and any one of them can move the output between two
machines running the identical script.

### The text rasteriser and its hinting policy

Windows draws glyphs through DirectWrite, or the older GDI path; Linux desktops
typically use FreeType, fed by fontconfig. Both decide, pixel by pixel, how much of
each pixel a glyph edge covers, and that depends on the hinting level in force. A
glyph spanning eleven and a half pixels gets rounded differently by rasterisers with
different hinting rules, and the rounding shows up directly in the readback.

### Which font file actually backs the family name

`ctx.font = "16px Arial"` does not point at one universal file. On Windows it
resolves to Microsoft's own Arial. On a Linux desktop without Microsoft's fonts
installed, the same family name commonly falls back to a metric-compatible substitute
such as Liberation Sans, matching Arial's advance widths but not its glyph outlines.
Same request, same font name, a different file drawing every letter.

### Subpixel geometry and antialiasing

A desktop session with subpixel rendering on draws each glyph edge using a display's
red, green and blue sub-elements, producing colour fringing invisible at normal
viewing distance but visible in a raw pixel readback. A headless or virtualised
environment usually falls back to plain grayscale antialiasing instead, since there
is no subpixel layout to target. The glyph looks the same to a human; the bytes do
not match.

### GPU-accelerated versus software rendering paths

Canvas content can be composited by the GPU or rasterised in software, and which path
a machine takes depends on driver support. A software rasteriser and a
GPU-accelerated one do not have to agree on sub-pixel rounding, so identical draw
commands can differ by a handful of values before fonts even enter the picture.

## Why text is the least stable element on a canvas

Draw a plain filled rectangle and the pixel values are close to universal: a solid
colour passes through a rasteriser mostly unchanged, so a flat-colour hash tends to
agree across machines that disagree on everything else. Draw text instead, and every
layer named above gets involved at once: rasteriser, font file, hinting, subpixel
policy, GPU path. That is why a text-free canvas is far more stable across machines,
and why fingerprinting scripts almost always put a mixed-case string on the probe
canvas rather than a plain shape.

## A canvas hash is a platform tell as much as a machine tell

Because font rendering is tied so tightly to the operating system, a canvas hash
carries platform information whether anyone intended it to.
[Windows implies Segoe UI and a specific CJK set; a bare Linux desktop implies
DejaVu and Liberation and little else](headless-fonts-differ.md), and text rendered
through those two stacks does not converge. That gives a page a checkable claim
rather than a rare value: a browser whose user agent says Windows, drawing text whose
antialiasing looks like Linux's FreeType instead of Windows' DirectWrite, has
contradicted itself, and the check needs only one sample of each platform, not a
notion of "normal" in the abstract.

## What this project does about it, and what it does not

For the values a page can read back from a canvas, this project ties the output to
the session's own seed rather than to whatever the host has, which is what lets [the
same identity return the same canvas and WebGL hash on Windows and on
Linux](canvas-webgl-cross-platform-consistency.md); [`measureText()`'s numeric fields
get the same treatment through a different mechanism](measuretext-textmetrics-fingerprinting.md).
That is a statement about what a page can observe, not a claim that the rendering
pipeline was rewritten to erase itself: rasterisation here is real drawing, done by
[a real, bundled font stack](bundled-fonts-cross-platform.md) rather than a
substituted picture, which is why a screenshot still shows real text instead of
noise. The honest limit: the fix covers only the readback calls a page can make
today, and any new export path a future engine adds needs the same treatment first.

## Conclusion

A canvas hash is not a single number a browser decides to report. It is the visible
output of a rasteriser, a font file, a hinting policy and a rendering path, four
layers a stock browser never controls independently of its operating system. Text
draws through all four at once, which is why a text-bearing canvas is the first
readback worth checking when a hash looks wrong for the platform a session claims to
be.

## Short answers to the questions that lead here

**Why does my canvas fingerprint differ between Windows and Linux?** The pixels come
from the OS's rasteriser, its font files, its hinting and subpixel policy, and
whether the GPU or software drew the glyph, not from the drawing code.

**Does the same drawing code produce the same canvas hash on every OS?** No, not on
a stock browser. Identical calls run through a different rasteriser and font file on
each platform, and the readback reflects that.

**Is a canvas fingerprint a browser tell or a platform tell?** Mostly a platform
tell. Two browsers on the same OS, drawing the same text, agree far more closely
than one browser does across two operating systems.

**Does removing text make the hash more stable?** Yes. Solid fills pass through a
rasteriser largely unchanged, while text pulls in the font stack, hinting and
subpixel geometry at once.

**Can a browser match its canvas hash to a claimed operating system exactly?** Only
by controlling what a page reads back, since the render itself comes from whichever
rasteriser and font files are actually installed.

**See also:** [canvas fingerprint noise and why per-call randomising
fails](canvas-fingerprint-noise.md), [why a canvas fingerprint changes every
run](canvas-fingerprint-changes-every-run.md), and [measureText and TextMetrics as a
fingerprinting surface](measuretext-textmetrics-fingerprinting.md).

## Sources

- [MDN: `CanvasRenderingContext2D.fillText()`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/fillText),
  [MDN: `HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
  and [MDN: `CanvasRenderingContext2D.getImageData()`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/getImageData),
  for the calls above.
- MDN, `CanvasRenderingContext2D.measureText()`,
  https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/measureText -
  it "returns a TextMetrics object that contains information about the measured text
  (such as its width, for example)", which is the surface this page's text argument rests
  on. Read 5 September 2026.
- This project's own measurements on font rendering, font bundling and canvas
  readback, referenced via the linked pages on this wiki.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A canvas hash was never
really about the canvas; it was always about everything underneath it.*
