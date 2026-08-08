---
title: "BrowserLeaks canvas and WebGL hash, explained"
description: "The BrowserLeaks canvas and WebGL signature is a hash of a pixel readback, not your GPU. What it measures, and why one seed gives a byte-identical hash."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 6
---


# BrowserLeaks canvas and WebGL hash, explained

The BrowserLeaks canvas and WebGL signature is a hash of the pixels a readback call
returns, not a fingerprint of your GPU. It sits at the very end of the render pipeline,
which is why editing a reported renderer string never moves it, and why the only way to
control it is to control the bytes handed back at the readback boundary. Get that boundary
right and one seed produces a byte-identical hash on Windows and Linux alike.

BrowserLeaks shows you a canvas "signature" and a WebGL "signature" and treats each as a
stable identifier for your machine (see [what each BrowserLeaks panel tests](browserleaks-explained.md)
for the wider tour). People read those short hex strings as a fingerprint
of the GPU, and then try to change the GPU to change them. That is the wrong layer. The
signature is a hash of one specific thing, and once you know which thing, the whole
surface reads differently.

This page is what the signature measures, where in the pipeline it is actually taken, what
has to be true for two machines to produce the same one, and how to read both values
yourself against a stock browser.

## What BrowserLeaks calls a signature

The canvas test draws text and shapes into an off-screen canvas, then asks the page to
read the resulting pixels back out and hashes them. The WebGL test does the same with a
rendered 3D scene. In both cases the "signature" you see is the hash of the pixel bytes,
not of any property you can enumerate.

That distinction matters because the pixel bytes are the product of a long chain: the
font rasterizer, the anti-aliasing, the GPU, the driver, the compositor. Two machines that
report the same WebGL renderer string can still hash differently, because the string and
the pixels are decided by different code. The signature is downstream of everything, which
is exactly why detectors like it and exactly why it is hard to fake by editing properties.
[The renderer string and the pixels it implies are two separate surfaces](renderer-string-vs-render.md),
and a mismatch between them is its own tell.

## The readback is the only thing JavaScript can see

JavaScript never sees the render surface directly; the only bytes a page can obtain are the
ones a readback call hands back. The GPU draws into memory the page has no handle on. The
only way script gets any pixels at all is by calling a readback method -
[`getImageData`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/getImageData),
`toDataURL`, `toBlob` on the 2D side,
[`readPixels`](https://developer.mozilla.org/en-US/docs/Web/API/WebGLRenderingContext/readPixels)
on the WebGL side - and those methods are
the single point where the rendered pixels cross from the engine into script.

So the signature is not a measurement of your GPU. It is a measurement of whatever those
readback calls return. If the bytes handed back at that boundary are a pure function of a
seed, the signature is a pure function of the seed, and the GPU underneath stops mattering
to the hash entirely.

That is the whole strategy in one sentence: the render can depend on the host all it likes,
because the host contribution is overwritten before any script can observe it.

## What our substitution does at that point

At each readback boundary, the pixels the page receives are replaced with values derived
from the seed and the position of the pixel, roughly `hash(seed, pixel_index)` per byte.
The scene is still drawn, so timing and shape and side effects all behave like a real
render, but the bytes that come back out are seed-determined rather than GPU-determined.

Two consequences follow directly:

- Read the canvas twice in one session and the hash is identical, because the same seed
  and the same pixel positions produce the same bytes. A hash that changes between two
  reads in one session is the cheapest tampering signal there is, and
  [per-call noise is exactly what a consistency probe is built to catch](canvas-fingerprint-noise.md).
- Two different operating systems running the same seed return the same bytes, because
  nothing host-specific survives to the readback. That is the property the next section is
  about.

Because the substitution happens where the pixels are read rather than where they are
drawn, it covers the indirect readback paths too - drawing the canvas into another canvas
and reading that, capturing it as an image bitmap and reading that - since every one of
them still ends at a readback call, and the readback is what gets replaced.

## Why a byte-identical hash across OSes is the real test

A stealth build that merely perturbs the canvas looks fine on one machine and falls apart
across a fleet, because the perturbation rides on top of a host render that still differs
between Windows and Linux. The honest test is not "did the hash change", it is "do two
different hosts, same seed, produce the same hash to the byte".

Measured on this build, same seed on Windows and on Linux, read in the page the way
BrowserLeaks reads it: the canvas signature is `df2a9319` on both, and the WebGL signature
is `0df1d6f8` on both. Not close, identical.

| Signature | Windows | Linux | Match |
|---|---|---|---|
| Canvas | `df2a9319` | `df2a9319` | byte-identical |
| WebGL | `0df1d6f8` | `0df1d6f8` | byte-identical |

Across a batch of sixteen distinct seeds run
through a full fingerprinting suite, zero of sixteen failed the cross-host check on either
operating system. That is the number that says the host contribution is actually gone
rather than merely reduced, and it is the same guarantee the
[canvas and WebGL cross-platform consistency page](canvas-webgl-cross-platform-consistency.md)
describes from the engine side.

The reason this is worth insisting on: a server render and a laptop render disagreeing is
one of the most common ways an otherwise-good identity gets linked across a fleet. If the
hash is byte-identical by construction, that entire class of leak is closed rather than
papered over.

## The low-entropy render left alone, and why

There is a trap in overwriting every readback unconditionally. Some pages do not draw a
fingerprint scene at all - they draw a tiny reference block, a flat rectangle of one or two
colours, precisely to check whether a browser is tampering with canvas output. A masking
scanner expects that reference to come back byte-exact. If your substitution rewrites it,
you have announced that you rewrite canvas reads, which is worse than any GPU string.

So the substitution has a threshold. A render with more than sixteen distinct colours is a
real fingerprint scene and gets the full overwrite. A render at or below sixteen distinct
colours is treated as a low-entropy reference and is left byte-exact, so its hash stays the
global constant a masking scanner expects to see. The high-entropy scene is spoofed; the
probe render is untouched. A detector looking for tampering on its reference block finds
none, and a detector reading the fingerprint scene gets a clean, seed-stable, host-free
hash.

This is the difference between hiding a signal and blocking it. Blocking canvas entirely,
or scrambling every read, is itself a loud signal - the kind
[a suppressed surface leaves behind](how-to-test-bot-detection.md). Leaving the reference
render exact while making the fingerprint render seed-deterministic is what keeps the
surface quiet.

## Reading the two hashes yourself

You do not need the detector's page to see this. Draw a canvas, read it back the way the
signature is computed, and print the hash. Do it twice in one session to confirm stability,
then relaunch with the same seed to confirm reproducibility.

```python
from invisible_playwright import InvisiblePlaywright

READBACK = """
() => {
  const c = document.createElement('canvas');
  c.width = 300; c.height = 60;
  const ctx = c.getContext('2d');
  ctx.textBaseline = 'alphabetic';
  ctx.font = '16px sans-serif';
  ctx.fillStyle = '#069';
  ctx.fillText('signature check', 4, 40);
  ctx.strokeStyle = 'rgba(120,0,90,0.7)';
  ctx.beginPath(); ctx.arc(240, 30, 20, 0, Math.PI * 2); ctx.stroke();
  // the readback: the one point JavaScript sees the render
  const bytes = ctx.getImageData(0, 0, c.width, c.height).data;
  let h = 0x811c9dc5;
  for (let i = 0; i < bytes.length; i++) {
    h ^= bytes[i];
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    first = page.evaluate(READBACK)
    second = page.evaluate(READBACK)
    print("read 1:", first)
    print("read 2:", second)
    assert first == second, "canvas hash changed within a session - per-call noise"
```

Run the same script a second time with the same `seed=42` and the printed hash is the same
again. Change the seed and it changes. Run it on a Windows host and on a Linux host with
one fixed seed and the two hosts print the same string. To make the comparison against a
stock browser concrete, run the identical readback under plain Playwright on the same
machine and note that its hash tracks the host GPU instead:

```bash
python read_hash.py           # invisible_playwright: seed-stable, host-free
python read_hash_stock.py     # stock Playwright: hash follows this machine's GPU
```

The stock browser is the reference. The point is not that the two disagree; it is that the
seeded one gives the same answer on a second machine and the stock one does not.

## Conclusion

The BrowserLeaks canvas and WebGL signatures are hashes of a readback, and the readback is
the only place a page can observe a render at all. That single fact is what makes the
surface tractable: substitute the bytes at the readback boundary with a function of the
seed, and the signature becomes seed-determined instead of GPU-determined, byte-identical
across operating systems, and stable across reads within a session. Leave the low-entropy
reference render exact and a tampering probe finds nothing to flag. The measurable outcome
is a canvas signature of `df2a9319` and a WebGL signature of `0df1d6f8` reproduced to the
byte on two different hosts, with zero cross-host failures across sixteen seeds.

## Short answers to the questions that lead here

**What does the BrowserLeaks canvas signature actually measure?** The hash of the pixels a
readback call returns, not any property of your GPU. It is downstream of the entire render
pipeline, which is why editing a renderer string does not move it.

**Why is my canvas hash different on my server than on my laptop?** Because a plain render
depends on the host GPU, driver and rasterizer, and those differ. When the readback is
replaced with a seed-derived function, the host drops out and the hash is the same on both.

**Should the canvas hash change on every page load?** No. A hash that changes between two
reads in one session is per-call noise, which is itself detectable. It should be stable
within a session and reproducible across sessions for a fixed seed.

**Is blocking canvas a good defence?** No. An empty or scrambled canvas is a loud signal.
The quiet approach returns a plausible, stable hash and leaves a detector's low-entropy
reference render byte-exact.

**Can I get the same signature on two machines on purpose?** Yes, that is the design. Same
seed, same signature, to the byte, on Windows and Linux alike.

**Does this touch the WebGL renderer string too?** That is a separate surface with its own
handling; see the note on [WebGL renderer strings](webgl-renderer-strings.md). The
signature is the pixels, the renderer string is a reported value, and both have to agree.

## Sources

- BrowserLeaks canvas and WebGL pages, read for how the signature is computed: a hash over
  the bytes returned by a readback call
  ([browserleaks.com/canvas](https://browserleaks.com/canvas),
  [browserleaks.com/webgl](https://browserleaks.com/webgl)).
- This project's host-independence audit, which maps every path a host-dependent render
  value can reach JavaScript and confirms the seeded hashes (`df2a9319`, `0df1d6f8`)
  byte-identical on Windows and Linux, zero of sixteen seeds failing the cross-host check.

**See also:** [canvas and WebGL fingerprints identical across OSes](canvas-webgl-cross-platform-consistency.md),
[the cheapest tampering check there is](canvas-fingerprint-noise.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The two hashes above are
real measurements, reproduced to the byte on two operating systems from one seed.*
