---
title: "Canvas and WebGL fingerprints, identical across OSes"
description: "Canvas and WebGL fingerprints identical across Windows and Linux: intercept readback, not the render, so one seed produces a byte-identical hash on every OS."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 9
---


# Canvas and WebGL fingerprints, identical across OSes

A GPU-backed render surface can't be read from or written to directly and safely, so
the fix intercepts the one place JavaScript can actually see the result instead - the
same seed then produces a byte-identical canvas and WebGL hash on Windows and Linux.

[Why headless browsers render different fonts](headless-fonts-differ.md) and
[how to make Linux and macOS report real Windows fonts](bundled-fonts-cross-platform.md)
cover the same shape of problem and fix, for text instead of pixels.

## The surface you can't safely touch is not the surface JavaScript reads

A GPU-backed render target is not something you can map and rewrite freely - reading
one directly with the ordinary in-process API can crash rather than return data,
depending on the backend. That rules out the obvious approach of patching the render
itself.

It turns out you don't need to. Content never touches the render surface directly. It
only ever gets pixels back through a small, enumerable set of export calls:

- [`toDataURL`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL) and
  [`toBlob`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toBlob), both on the canvas element itself
- [`getImageData`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/getImageData), on the 2D context
- [`readPixels`](https://developer.mozilla.org/en-US/docs/Web/API/WebGLRenderingContext/readPixels), on the WebGL context
- a captured [`OffscreenCanvas`](https://developer.mozilla.org/en-US/docs/Web/API/OffscreenCanvas)
- the chain that runs through [`createImageBitmap`](https://developer.mozilla.org/en-US/docs/Web/API/Window/createImageBitmap)

Every one of those is a **readback** call, and readback is a single choke point you
can intercept safely, even when the surface behind it cannot be touched directly.

So the fix is the same shape as the font one: block the host's real value at the one
place content can actually observe it, rather than trying to prevent the value from
existing in the first place.

## What gets overwritten, and what doesn't

At each readback call, the real pixel buffer is rewritten with output derived from
the session's seed and the pixel's own position, before JavaScript ever sees it - for
both the canvas-2D path and the WebGL path, since they are separate APIs with
separate export calls that both needed the same treatment.

The one thing this has to get right is not breaking the pages that read canvas
content for a legitimate reason. A reference probe - a handful of flat colours used
to check whether canvas output has been tampered with at all - looks nothing like a
real rendered scene: very few distinct colours across the whole buffer. Below a
small distinct-colour threshold, the output is left untouched; above it, the
substitution applies in full.

A solid red rectangle comes back solid red. A real
rendered scene, which is high-entropy by construction, gets the full treatment. This
is what keeps a masking-detection probe from immediately noticing that something
intercepts every readback.

## Why substitution instead of noise, here specifically

[Adding noise per call is the common approach, and it fails a very cheap check](canvas-fingerprint-noise.md):
read the canvas twice in the same page and compare. What makes substitution the
right choice for cross-platform consistency specifically is a different property:
substituted output is a pure function of the seed and the pixel position, with zero
input from the actual rendering pipeline underneath.

Additive noise on top of a real
render still carries the render's own signature - two machines with different font or
GPU backends produce different base images, and a small perturbation on top doesn't
hide that. Substitution removes the underlying image from the equation entirely, so
there's nothing left for the host to leak into the result.

There's a second, sharper way per-pixel noise fails, found on this exact surface: a
detector doesn't need two reads to catch it, because the noise itself has a
statistical shape that a single read can flag. A small random offset applied to a
fraction of pixels reads as unnaturally high-frequency variation - a masking-detection
check built to look for exactly that pattern flagged it directly, on the WebGL
readback specifically, with the rest of the fingerprint surface clean.

Replacing it
with a per-channel remap - which looks like an ordinary colour-profile difference
between GPUs rather than injected noise - took a measured spike count from roughly
320 down to near zero on the same probe. Substitution inherits that property too,
for the same reason: nothing about it resembles noise added on top of a real signal.

| Approach | How it hides the host | Where it fails |
|---|---|---|
| Additive per-call noise | Perturbs a fraction of pixels on top of the real render | Two reads in one page differ; the real render's font/GPU signature still shows through; the noise has a high-frequency shape a single read can flag |
| Full substitution | Replaces pixels with a pure function of the seed and the pixel position, with zero input from the render underneath | Must leave low-entropy buffers untouched so it does not break legitimate canvas reads |

## The proof: byte-identical hashes, not just "looks similar"

Rendered the same seed's canvas (text plus a gradient) and WebGL scene (a
high-entropy draw) on Windows and on Linux, hashed both in-page. The hashes matched
byte for byte on both platforms. Run against a commercial fingerprint scanner across
multiple seeds, zero failures on either OS.

That is a stronger claim than "produces a plausible fingerprint." It's the same
identity, provably, regardless of which OS is actually running underneath - which is
what makes a seed a real, portable identity instead of a value that happens to look
right on the machine it was tested on.

## What this generalises to, and where the edges still are

The underlying idea applies to any surface where content can only see a value
through a small number of export or readback calls: intercept there, and it doesn't
matter what happens upstream.

It is also, necessarily, an enumeration problem - the
fix is only as complete as the list of export paths it covers, and browsers keep
adding new ones (newer capture and streaming APIs, newer GPU-facing interfaces).
Treat any such list as something that needs revisiting as the platform grows, not as
a fact that becomes true once and stays true.

## Conclusion

The render was never the reachable surface. Content only ever sees a canvas or a
WebGL scene through a handful of readback calls, and putting the substitution there
instead of in the renderer is what makes a seed portable: the same identity produces
the same bytes on Windows and on Linux, because nothing about either host's actual
rendering pipeline reaches the output.

The tradeoff is honest rather than free - the fix covers only the readback paths it
enumerates, and a masking-safe design still has to leave genuinely low-entropy canvas
reads alone so it doesn't break the pages that use canvas for something other than
fingerprinting.

## Short answers to the questions that lead here

**Can canvas and WebGL fingerprints be made identical across operating systems?** Yes,
if the interception happens at every point content can read the result rather
than at the render itself, which is the part that can't be touched directly on a
GPU-backed surface.

**Why substitution instead of adding noise to the real render?** Noise on top of a
real image still carries the host's own rendering signature underneath it.
Substitution replaces the value entirely, so nothing about the actual host reaches
the output.

**Won't this break real uses of canvas, like image editing or QR decoding?** A
masking-safe design leaves near-uniform, low-entropy output untouched and only
substitutes high-entropy renders, which is what a fingerprinting probe looks like
and an image editor's output usually doesn't - though the same caveat as
[canvas fingerprint noise](canvas-fingerprint-noise.md) applies if your own use case
happens to fall on the wrong side of that line.

**Is this the same fix as the font work?** The same shape - intercept at the point
content can observe, not at the source - applied to a different surface.

## Sources

- [MDN: `HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL),
  [MDN: `HTMLCanvasElement.toBlob()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toBlob),
  [MDN: `CanvasRenderingContext2D.getImageData()`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/getImageData),
  [MDN: `WebGLRenderingContext.readPixels()`](https://developer.mozilla.org/en-US/docs/Web/API/WebGLRenderingContext/readPixels),
  [MDN: `OffscreenCanvas`](https://developer.mozilla.org/en-US/docs/Web/API/OffscreenCanvas), and
  [MDN: `createImageBitmap()`](https://developer.mozilla.org/en-US/docs/Web/API/Window/createImageBitmap),
  retrieved 2026-08-28, the enumerable set of readback calls this page's interception
  point covers.
- This project's own render-readback interception work and its cross-platform
  validation (same-seed byte-identical hashes on Windows and Linux, multi-seed
  results against a commercial fingerprint scanner).

**See also:** [canvas fingerprint noise](canvas-fingerprint-noise.md), for why
per-call randomisation specifically fails; [how to make Linux and macOS report real
Windows fonts](bundled-fonts-cross-platform.md), for the same architecture applied to
fonts; [WebGL renderer strings](webgl-renderer-strings.md), for the adjacent
surface of what the GPU claims to be rather than what it draws; and
[how the BrowserLeaks canvas and WebGL hash is computed](browserleaks-canvas-webgl-hash.md),
for what that hash actually measures.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The same-seed check here once passed while the
render was actually broken: a Linux canvas with text came back holding 11994 distinct
colours where Windows gave 16, and identical bytes for two different strings.*
