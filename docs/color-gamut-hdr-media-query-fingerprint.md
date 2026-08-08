---
title: "Color-gamut and HDR media queries as a fingerprint"
description: "Color-gamut and HDR media features expose display capability without JavaScript. Why they must align with screen.colorDepth and what misalignment reveals."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 28
---

# Color-gamut and HDR media queries as a fingerprint

Most of the fingerprint surfaces people worry about live in JavaScript: `navigator`,
canvas, WebGL, audio. This one does not. The [`color-gamut`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/color-gamut), [`dynamic-range`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/dynamic-range) and
[`video-dynamic-range`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/video-dynamic-range) media features describe what a display can show, and a page can
read them from CSS alone, before a single line of script runs. That makes them a
quiet consistency check: they have to agree with everything your JavaScript says about
the same screen, and nothing you set in code touches them.

This page is what those features report, why they are read from a different code path
than `screen.colorDepth`, the disagreement that gives a browser away, and how
invisible_playwright keeps the two sides telling one story. It answers the question in
the title honestly: matching these features helps you look like a real display, and it
does nothing for your IP or your pacing.

## What color-gamut and dynamic-range actually report

Three CSS media features describe a display's color and brightness capability:

- `color-gamut` reports the range of color the output can render, as one of `srgb`,
  `p3` or `rec2020`. A page tests it with `@media (color-gamut: srgb) { ... }` or from
  script with `matchMedia("(color-gamut: p3)")`.
- `dynamic-range` reports whether the display and the browser can show high dynamic
  range content, as `standard` or `high`.
- `video-dynamic-range` is the same question aimed specifically at video output, and
  can differ from `dynamic-range` on hardware that treats the two paths separately.

None of these needs JavaScript to be readable. A stylesheet can branch on them and a
detector can observe which branch applied, so they are live even on a page that runs
no script at all. This puts them in the same family as the
[CSS media features documented for dark mode and pointer type](css-media-query-fingerprinting.md),
and distinct from them in what they describe: not a user preference, but a hardware
property of the panel.

The values are not free-floating. A real Windows laptop with a standard sRGB panel
reports `color-gamut: srgb` and `dynamic-range: standard`, and it reports a
`screen.colorDepth` of 24 to match. Those two facts come from the same physical
display, so on real hardware they never contradict each other. That is the whole
point of the surface for a detector.

## Why this is read from a different code path than colorDepth

Here is the trap. `screen.colorDepth` is a JavaScript property. The color-gamut media
feature is resolved by the CSS engine when it evaluates a media query. A page can read
both, and it can compare them, but they arrive through two entirely separate paths
inside the browser.

That separation is exactly what a consistency detector wants. If a stealth layer
overrides `screen.colorDepth` in JavaScript to report a plausible 24-bit display, but
the CSS side still resolves `color-gamut` against whatever the underlying environment
actually has, the two disagree. One code path was patched and the other was not. A
real browser cannot produce that disagreement, because both values are read off the
same panel. So a page that finds `screen.colorDepth: 24` next to a `color-gamut` that
resolves to something else has found a browser whose two halves were configured
independently, which is a tell that no single value would have given up.

The same shape shows up in
[the platform and oscpu strings that have to agree with the user agent](navigator-platform-oscpu-consistency.md):
individually every value is plausible, and the fingerprint is in whether they line up.
A JavaScript-only spoof can set the property a page reads first, and miss the second
surface that reports the same underlying fact through CSS.

## Why a page-level override cannot fix this

If your approach to fingerprinting is injecting JavaScript, this surface is a problem,
because there is nothing in JavaScript's gift to change here. You can redefine
`matchMedia` to lie about `color-gamut`, but redefining a built-in is itself one of
the loudest tells there is: [CreepJS](how-to-test-bot-detection.md) walks descriptors
and prototypes and records a patched built-in by name. And even if you patch
`matchMedia`, the pure-CSS path that applies a `@media (color-gamut: ...)` rule
without ever calling script is still resolving against the real value. You cannot
intercept a stylesheet branch from a `matchMedia` override.

The only place this can be made consistent is below JavaScript, where the CSS engine
and the `screen` object read the same display description. invisible_playwright is a
Firefox patched at the C++ level rather than a script injected into a stock browser,
so the display it presents is a single coherent object: the color-gamut media feature,
`dynamic-range`, `video-dynamic-range` and `screen.colorDepth` all resolve from one
plausible Windows sRGB display, the way they do on the real Firefox it is built from.
There is no second code path left unpatched, because the value is not being faked at
the property level at all.

This is the same reason [an impossible screen geometry](screen-size-headless-tells.md)
survives every page-level spoofer: the surfaces that describe the physical display are
not the ones a script can reach.

## Reading it yourself

The `browser` returned by invisible_playwright is a real Playwright `Browser`, so you
read these features the same way you would in any Playwright script. Launch, open a
page, and evaluate the media queries plus the property they have to agree with:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""() => ({
        colorGamut:
            matchMedia('(color-gamut: rec2020)').matches ? 'rec2020' :
            matchMedia('(color-gamut: p3)').matches ? 'p3' :
            matchMedia('(color-gamut: srgb)').matches ? 'srgb' : 'unknown',
        dynamicRange:
            matchMedia('(dynamic-range: high)').matches ? 'high' : 'standard',
        videoDynamicRange:
            matchMedia('(video-dynamic-range: high)').matches ? 'high' : 'standard',
        colorDepth: screen.colorDepth,
        pixelDepth: screen.pixelDepth,
    })""")

    print(report)
    # {'colorGamut': 'srgb', 'dynamicRange': 'standard',
    #  'videoDynamicRange': 'standard', 'colorDepth': 24, 'pixelDepth': 24}
```

The check that matters is not any single line of that output. It is that
`colorGamut: 'srgb'` and `colorDepth: 24` describe the same display, and would still
describe the same display if you read them again in the next session. Because the
identity is derived from the seed, passing `seed=42` gives you the same display every
run, so this is a value you can assert on in a test rather than a moving target. The
[reproducible-fingerprint workflow](quickstart.md) is what makes that assertion stable.

Run it against a stock Firefox on the same machine and diff the two reports field by
field. On a real desktop panel they match. The point of the exercise is to confirm the
patched build sits on the same side of that diff as the real browser, not on the
overcorrected side where a spoof reports a `p3` display no one asked for.

## The honest limit

This makes your browser's display self-consistent. It is worth being precise about
what that does and does not buy you.

Color-gamut and dynamic-range are properties of the browser and its claimed hardware.
Getting them right removes one way a page distinguishes a real display from a patched
one, and it removes it at the layer where it actually lives rather than papering over
it in script. That is a real part of why invisible_playwright reads as a genuine
Firefox to fingerprint, TLS and driver-layer checks.

It is not the whole session. These media features say nothing about where your traffic
exits or how fast you send it. A perfectly consistent sRGB display on a datacenter
address that a scoring service already knows is still on a known address. A browser
that fills a form in eighty milliseconds still moves like no human, whatever its
color-gamut reports. invisible_playwright does not touch IP reputation, per-account
quotas, rate limits, or the timing of your actions, and it does not claim to. Those
you supply: a clean residential proxy, human pacing, one account doing one plausible
amount of work. The fingerprint is the part that is hard to get right by hand, and it
is the part this tool owns; the rest of the session is still yours.

## Conclusion

Color-gamut, dynamic-range and video-dynamic-range are a display fingerprint that
lives in CSS, readable with no JavaScript, and they have to agree with the
`screen.colorDepth` a page reads from script through a completely separate code path.
A page-level override touches one path and leaves the other reporting the real
environment, which is a contradiction a real browser never produces. invisible_playwright
presents a single coherent Windows sRGB display across both paths because it is patched
below JavaScript, and it keeps that display stable across runs when you pin the seed.
That is worth exactly what it is worth: a browser that looks real, on top of an exit
and a rhythm you still have to get right yourself.

## Short answers to the questions that lead here

**Can a website read my display's color gamut without JavaScript?** Yes. The
`color-gamut` media feature is resolved by the CSS engine, so a stylesheet can branch
on it and observe the result before any script runs.

**What is the trap with color-gamut and screen.colorDepth?** They describe the same
physical display but are read through different code paths. If a spoof patches one and
not the other, they disagree, which a real browser never does.

**Can I fix this by overriding matchMedia?** No. Redefining a built-in is itself a
loud tell, and it still does not touch the pure-CSS path that applies a
`@media (color-gamut: ...)` rule without calling script.

**What does invisible_playwright report for these features?** A plausible, consistent
Windows sRGB display: `color-gamut: srgb`, `dynamic-range: standard`, and a matching
24-bit `screen.colorDepth`, the same as the real Firefox it is built from.

**Does matching these features make me undetectable?** No. It makes your display
self-consistent. It does nothing for your IP reputation, your rate limits or your
behaviour, which you still have to handle with a clean proxy and human pacing.

**Will the same seed give the same color-gamut every run?** Yes. The display is derived
from the seed, so `seed=42` reports the same values every session, which is what lets
you assert on them in a test.

## Sources

- The [Media Queries Level 5 specification](https://www.w3.org/TR/mediaqueries-5/),
  which defines `color-gamut`, `dynamic-range` and `video-dynamic-range` and their
  resolution from the output device.
- This project's fingerprint gates, which cross-check the CSS-resolved display
  features against `screen.colorDepth` and the canvas and WebGL persona for the same
  seed.

**See also:** [CSS media features as a fingerprint surface](css-media-query-fingerprinting.md),
[keeping navigator.platform and oscpu consistent](navigator-platform-oscpu-consistency.md),
and [screen sizes that give a headless machine away](screen-size-headless-tells.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The color-gamut and
colorDepth disagreement is one I would rather find in my own diff than have a page find
for me.*
