---
title: "Canvas fingerprint noise: why per-call randomising fails"
description: "Per-call canvas noise beats a detector that reads once and fails a detector that reads twice. The second check almost nobody knows about, and what a consistent alternative has to look like."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 1
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Guides",
      "item": "https://feder-cr.github.io/invisible_playwright/guides.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Canvas, WebGL, Fonts and Audio",
      "item": "https://feder-cr.github.io/invisible_playwright/guides-canvas-webgl-fonts-audio.html"
    },
    {
      "@type": "ListItem",
      "position": 4,
      "name": "Canvas fingerprint noise: why per-call randomising fails"
    }
  ]
}
</script>

# Canvas fingerprint noise: why per-call randomising fails

The standard advice for canvas fingerprinting is to add a little noise to the pixels so
your hash is not stable. It is the most widely deployed anti-fingerprinting technique
there is, and against a detector that asks once it works.

Against a detector that asks twice it is the thing that gets you caught. This page is
the check that catches it, the second check almost nobody knows about, and what a
consistent alternative has to look like.

## The check that catches per-call noise

Render the same canvas twice in the same page and compare.

```js
function hash() {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.textBaseline = 'top';
  ctx.font = '14px Arial';
  ctx.fillText('abcdefghijklmnop', 2, 2);
  return c.toDataURL();
}
hash() === hash();   // real hardware: true.  Per-call noise: false.
```

On real hardware the answer is always identical, because drawing the same shapes with
the same rasteriser produces the same pixels. There is no source of variation.

A tool that adds `Math.random()` to the pixel values on every read produces two
different answers, and now the page knows something far more specific than your canvas
hash: it knows your canvas is being tampered with. An unusual hash makes you rare. A
hash that changes between two reads one millisecond apart makes you impossible.

This is four lines of JavaScript. Assume it is running.

## The second check, which is subtler

The first one is well known enough that better tools seed their noise, so two reads
agree. There is another shape, and it is worth understanding because it caught us.

A detector draws a **small canvas filled with a handful of solid reference colours**,
then reads it back and checks those exact colour values came back unchanged. Not a
hash, not a comparison between two reads: an assertion that a flat red rectangle is
still exactly that red.

Real rendering does not change a solid fill. Any per-pixel noise does, even seeded
noise, even tiny noise. And a tool that fails this is not scored as "unusual", it is
scored as **masking detected**, which is a different and worse category.

The fix is a guard: before altering anything, count the distinct colours in the buffer.
If there are only a few, the render is a reference probe rather than a fingerprint, and
you leave it alone. Small buffers get the same treatment, for the same reason.

We shipped without that guard and learned it from a detector telling us so. The general
form is the same one that appears in audio and everywhere else:

> A detector does not have to recognise your noise. It only has to ask a question whose
> correct answer is already known, and check whether it still gets that answer.

## Additive noise versus substitution, and the cost nobody states

There are two ways to make a canvas read return something other than the truth.

**Additive.** Perturb each pixel slightly. The image still looks like the image, the
hash changes. This is what extensions do.

**Substitution.** Replace the pixel values wholesale with values derived from a seed.
The hash is then a pure function of the seed and nothing else, which means it is
identical on every machine running that identity, including a Linux server pretending
to be Windows.

Substitution is stronger for consistency, because with additive noise the underlying
rendering still carries the signature of
[the operating system's text stack](bundled-fonts-cross-platform.md) under the
perturbation. Two machines with different font backends produce different base images,
and a small perturbation on top does not hide that -
[this is the reasoning that gets the same seed to the same byte-identical hash on
Windows and Linux](canvas-webgl-cross-platform-consistency.md), not just a similar one.

Now the cost, which is real and which most write-ups omit. A substitution applied at
readback does not know why the page is reading the canvas. It cannot distinguish a
fingerprinting probe from an application that is cropping a photo, decoding a QR code,
capturing a video frame or applying a filter. Those all read pixels through the same
API, and they all get the substituted values.

So if your automation does real canvas work, you have to know this is happening. It is
a trade, not a free win, and anyone presenting per-pixel protection as costless has not
built one.

## What a defensible canvas looks like

Working backwards from the checks above:

- **Stable within a session.** Two reads agree. Non-negotiable and trivially checked.
- **Stable across sessions of the same identity.** A hash that changes every launch
  describes a machine that changed its GPU overnight.
- **Different between identities.** Otherwise every user of the tool shares one hash,
  which is its own cohort.
- **Solid fills come back solid.** Reference probes are untouched.
- **Consistent with the rest of the claim.** A canvas that says one platform under a
  user agent that says another is caught by comparison, not by rarity.

That list is short and every item is a thing a page can verify in a few lines. That is
the level the checks are actually at.

The same canvas element carries a second surface that doesn't involve reading a single
pixel: [`measureText()` and its ten-plus numeric fields](measuretext-textmetrics-fingerprinting.md),
which fail the same way for the same reason if the noise added to them accumulates
instead of staying bounded.

## Checking your own

```js
const a = hash(), b = hash();
console.log('stable within session:', a === b);

// solid-fill probe
const c = document.createElement('canvas'); c.width = 70; c.height = 5;
const ctx = c.getContext('2d');
ctx.fillStyle = '#ff0000'; ctx.fillRect(0, 0, 10, 5);
const px = ctx.getImageData(0, 0, 1, 1).data;
console.log('solid red survived:', px[0] === 255 && px[1] === 0 && px[2] === 0);
```

Then relaunch with the same identity and confirm the first hash is the same value. Two
launches, one identity, one hash.

## Short answers to the questions that lead here

**Does adding noise to canvas prevent fingerprinting?** It prevents a stable identifier
and it announces that something is modifying the canvas. Which of those matters depends
on whether anything is checking.

**Is Canvas Blocker style protection detectable?** The per-call randomisation kind is,
in four lines. That is a statement about the technique, not about any particular tool.

**Why does my canvas hash change on every page load?** Because something is randomising
per call or per session. No real machine does that.

**Should the hash be unique to me?** It should be consistent for one identity and
different between identities. Unique-per-request is not privacy, it is a signature.

**Does `privacy.resistFingerprinting` solve this?** It alters canvas readings, and it
also makes you identifiable as someone running that mode. There is
[a longer page on that trade](resist-fingerprinting.md).

**Will noise break my application?** If you read pixels for anything real, yes,
potentially. Protection applied at readback cannot tell your QR decoder from a
fingerprinting probe.

**See also:** [AudioContext fingerprinting](audiocontext-fingerprinting.md), where the
same per-call mistake has the same consequence and where our own noise turned out to be
the thing being detected, and [what sannysoft checks](sannysoft-explained.md), whose
canvas-in-iframe rows are a consistency test rather than a fingerprint test.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The solid-fill guard exists because we shipped
without it.*
