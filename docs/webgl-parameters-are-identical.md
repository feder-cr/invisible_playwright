---
title: "WebGL parameters: the numbers are the same on every GPU"
description: "The common advice is to raise WebGL's numeric limits to match a claimed high-end GPU. On Windows that is backwards: ANGLE clamps every card to the same feature-level ceiling, so raising the numbers gets you caught."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 3
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
      "name": "WebGL parameters: the numbers are the same on every GPU"
    }
  ]
}
</script>

# WebGL parameters: the numbers are the same on every GPU

The most repeated advice about WebGL fingerprinting goes like this: if you claim a
high-end card, you must also raise `MAX_TEXTURE_SIZE` and the forty-odd other numeric
limits to match, or you have reported an expensive GPU with a cheap card's ceiling.

That advice is backwards, at least on Windows, which is the platform most people are
claiming. Raising those numbers to "match" the card is how you leave the set of values
real browsers produce.

## What ANGLE does to the numbers

On Windows, Firefox and Chrome do not talk to the graphics driver directly. Both render
WebGL through **ANGLE**, which translates to Direct3D 11.
[Which is also why the renderer string starts with `ANGLE (`](webgl-renderer-strings.md).

ANGLE reports limits based on the **D3D feature level**, not on what the card can
actually do. It clamps. So the numbers are a property of the translation layer and the
feature level, and every Windows GPU from roughly 2012 onward sits at the same feature
level and returns the same block:

```
MAX_TEXTURE_SIZE                  = 16384
MAX_CUBE_MAP_TEXTURE_SIZE         = 16384
MAX_RENDERBUFFER_SIZE             = 16384
MAX_VIEWPORT_DIMS                 = [16384, 16384]
MAX_VERTEX_ATTRIBS                = 16
MAX_VARYING_VECTORS               = 30
MAX_FRAGMENT_UNIFORM_VECTORS      = 1024
MAX_VERTEX_UNIFORM_VECTORS        = 4096
MAX_TEXTURE_IMAGE_UNITS           = 16
MAX_VERTEX_TEXTURE_IMAGE_UNITS    = 16
MAX_COMBINED_TEXTURE_IMAGE_UNITS  = 32
ALIASED_POINT_SIZE_RANGE          = [1.0, 1024.0]
```

A recent flagship and a 2012 integrated chip return that identical list, because both
are feature level 11_0 as far as ANGLE is concerned. On a very large share of Windows
machines this is exactly what a page reads.

**The variation between GPUs lives in the renderer string, not in the numbers.** That
is the sentence the popular advice gets inverted.

Sources you can check rather than take our word for: ANGLE's own D3D11 renderer
utilities, where the caps are computed from the feature level, and the public surveys of
WebGL parameter distributions in the wild.

## What happens when you randomise them anyway

We know because we did it. An early version of this project had a dozen preferences
that randomised the numeric limits per session, on the reasonable-sounding theory that
varying values are harder to track.

The result was the opposite of privacy. [CreepJS](https://abrahamjuliot.github.io/creepjs/)
compares the parameter set against a collection of hashes seen on real browsers. Ours
matched none of them, so it was classified as low entropy and the **entire WebGL section
was dropped from the report**.

Think about what that means. We had not blended in. We had produced a combination that
does not exist, and been marked as such.

Setting them per GPU class would have been the same mistake in a more sophisticated
costume, for the reason above: the classes do not differ.

**So the rule is simple and it is the opposite of the intuition.** Do not randomise
these. Do not scale them to the card you claim. Emit the canonical block, because that
is what almost every real Windows browser emits.

## The extension list is a different question

The numbers are a driver-stack property. The **extension list** is a platform property,
and it is the one that catches a Linux machine claiming Windows.

`gl.getSupportedExtensions()` on Windows returns a list shaped by ANGLE, including
`ANGLE_`-prefixed entries. The same browser built on Linux runs on Mesa and native
OpenGL instead, so it returns a Linux-shaped list with different entries and none of the
ANGLE ones.

That is a straight comparison against your user agent, and it needs no hardware
knowledge at all. A browser announcing Windows and listing Mesa extensions has answered
the same question two ways.

An honest note about how far a list can be fixed: you can declare which extension names
are reported, and you cannot conjure an extension that the build does not physically
have. A page that inspects the list sees the corrected one; a page that calls
`getExtension()` on an entry that does not exist underneath gets `null`. In practice
almost everything inspects and very little calls, but the gap is real and worth knowing
if you depend on it.

## What a consistent WebGL story looks like

- **Renderer and vendor strings** describe hardware someone could buy, in the shape the
  claimed platform produces.
- **Numeric parameters** are the canonical block for that platform, identical across
  machines, not scaled to the claimed card.
- **The extension list** belongs to the claimed platform, not the host.
- **Shader precision formats** come from the same place as the numbers, for the same
  reason.
- **Everything is stable** for one identity across sessions. A parameter set that moves
  between page loads is the loudest possible outcome.
- **The pixels agree with the claim**, which is the one thing none of the above fixes.
  [The renderer string can say NVIDIA while a software rasterizer draws](renderer-string-vs-render.md).

## Checking your own

```js
const gl = document.createElement('canvas').getContext('webgl');
const P = ['MAX_TEXTURE_SIZE','MAX_RENDERBUFFER_SIZE','MAX_VERTEX_ATTRIBS',
           'MAX_VARYING_VECTORS','MAX_FRAGMENT_UNIFORM_VECTORS',
           'MAX_VERTEX_UNIFORM_VECTORS','MAX_TEXTURE_IMAGE_UNITS'];
console.table(Object.fromEntries(P.map(k => [k, gl.getParameter(gl[k])])));
console.log(gl.getParameter(gl.MAX_VIEWPORT_DIMS));
console.log(gl.getSupportedExtensions().join('\n'));
```

Compare against a stock browser on the platform you claim, not against a table of GPU
specifications. Then reload and confirm every number is unchanged.

## Short answers to the questions that lead here

**Should `MAX_TEXTURE_SIZE` match my claimed GPU?** On Windows it should be the
canonical value, which is 16384 for essentially every machine, regardless of the card.
The limits come from the translation layer, not the hardware.

**Do an RTX-class card and an old integrated chip really report the same numbers?** On
Windows through ANGLE, yes, because both are the same D3D feature level and ANGLE clamps
to it.

**Is randomising WebGL parameters good for privacy?** No. It produces combinations that
do not occur, and at least one well-known detector explicitly compares against the set
of real ones.

**Why does my WebGL section disappear from a CreepJS report?** Often because the
parameter set was not recognised as a real one, which gets it treated as low entropy
rather than as an identity.

**What actually differs between GPUs then?** The renderer and vendor strings, and the
pixels that come out of the rasteriser.

**Does the extension list matter?** Yes, and it is the value most likely to give away
the host platform rather than the card.

**See also:** [Firefox WebGL renderer strings](webgl-renderer-strings.md) for the string
half, and [your renderer string says NVIDIA, your pixels say software](renderer-string-vs-render.md)
for the part that no parameter can fix.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The randomised-parameters version described above was
ours, and removing it was the fix.*
