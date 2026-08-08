---
title: "What is a browser fingerprint?"
description: "Browser fingerprint joins low-entropy attributes - canvas, WebGL, fonts, audio, screen, timezone - that identify a browser. How and what it doesn't cover."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 10
---


# What is a browser fingerprint?

A browser fingerprint is not one value. It is the combination of dozens of small,
individually unremarkable attributes that a page can read from JavaScript, joined
together into something that often identifies a browser without any cookie at all.

No single attribute in that set is unique to you. Your timezone is shared by a
timezone's worth of people. Your screen size is one of a handful of common ones. Your
user agent matches millions of other machines. The identifying power is in the join:
the number of browsers that share your timezone AND your exact font list AND your
canvas rendering AND your audio signature AND your GPU string shrinks with every
attribute added, until the intersection is small enough to recognise you on your next
visit.

This page is what those attributes are, why the join is what matters, and one honest
limit on what fixing the fingerprint layer actually buys you.

## The attributes, and why each one is low entropy alone

The set a typical fingerprinting script reads is longer than most people expect. The
common members:

- **[Canvas rendering](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API).**
  The page draws text and shapes to an offscreen canvas and hashes the pixels. The
  result depends on the GPU, the driver, the font rasterizer and anti-aliasing, so two
  different machines usually produce different hashes.
- **[WebGL](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API).** The renderer
  and vendor strings, plus a hash of a rendered 3D scene. This leans on the graphics
  stack even harder than canvas does.
- **Installed fonts.** Enumerated directly or inferred by measuring text width. The set
  is characteristic of the operating system and what has been installed on it.
- **[AudioContext](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API).**
  An oscillator processed through the audio pipeline and hashed. The output varies with
  the audio stack, and a machine with no real audio device answers with tell-tale
  defaults.
- **Screen.** Width, height, available height, colour depth and device pixel ratio.
- **Timezone and language.** The IANA zone the browser reports and its language list.
- **User agent, platform and hardware.** The UA string, `navigator.platform`,
  `hardwareConcurrency`, `deviceMemory`.

Taken one at a time, every one of these is shared by a large population. That is the
whole point of the word "low-entropy": each attribute narrows the field only a little.
The BrowserLeaks-style per-surface pages exist precisely because reading one surface at
a time is how you inspect the pieces before worrying about the join.

## Why the join identifies you when no single value does

Entropy adds up. If an attribute splits the population in half, it contributes one bit.
Real fingerprint attributes contribute far more than one bit each, and there are dozens
of them, so the combined selectivity gets large quickly.

Two consequences follow, and the second is the one that catches automation:

1. **Recognition without cookies.** A site that stores nothing on your machine can still
   recognise a returning visitor by recomputing the join and looking it up. This is what
   a [FingerprintJS visitor ID](fingerprintjs-visitor-id.md) is: a hash of many
   components, stable enough to link two visits.
2. **Contradiction detection.** Because the attributes are supposed to come from one real
   machine, they are supposed to agree. A user agent that says Windows, a font list that
   says Linux, and a WebGL renderer that says "software rasterizer" describe three
   different machines wearing one coat. A detector does not need any single value to be
   rare - it needs two values that should match to disagree. That is why
   [navigator.platform and oscpu have to tell the same story](navigator-platform-oscpu-consistency.md),
   and why [a randomised canvas hash that changes on every read](canvas-fingerprint-noise.md)
   is itself a signal rather than a disguise.

Plain automation loses on both counts at once. It emits blank or default values for the
attributes it never populated (no fonts, a software renderer, a screen nobody has), and
the values it does set by hand tend to contradict the ones it forgot to.

## How invisible_playwright makes the join consistent

The reason automation fingerprints badly is usually not that any one value is wrong. It
is that the values were assembled from different sources - some real, some faked, some
left blank - so they do not describe a single coherent machine.

invisible_playwright takes the opposite approach: it derives every attribute in the set
from one seed. Canvas, WebGL, fonts, audio, screen, hardware counts and the rest all
come out of the same generator, so they are internally consistent by construction and
they match a real Firefox-on-Windows profile rather than a datacenter default. Because
the seed is the only input, the same seed reproduces the same machine every run, which
is what makes a failing session replayable instead of a guess.

Switching from stock Playwright is two lines, and after that every method is the same:

```python
from invisible_playwright import InvisiblePlaywright

# One seed drives canvas, WebGL, fonts, audio, screen, timezone and hardware
# together, so the join is internally consistent and reproducible.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

The `browser` object is a real Playwright `Browser`, so anything you already do with
Playwright works unchanged. To confirm the join is consistent rather than trusting a
verdict, read the fingerprint surfaces yourself and compare against a stock browser on
the same machine:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate("""() => ({
        ua: navigator.userAgent,
        platform: navigator.platform,
        cores: navigator.hardwareConcurrency,
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
        screen: [screen.width, screen.height, window.devicePixelRatio],
        langs: navigator.languages,
    })"""))
```

Run it with the same seed twice and every field comes back identical. Change the seed
and you get a different but equally coherent machine. That reproducibility is what
[pinning specific fields](pinning.md) builds on when you need to hold one value fixed and
let the rest stay seed-derived.

## The honest caveat: this is the fingerprint layer only

Here is the part a demonstration has to be straight about. A consistent, real-looking
fingerprint gets you past the checks that read the browser - and that is a large share
of them - but a fingerprint is not the whole session.

What the fingerprint layer does not touch:

- **Your IP address and its reputation.** A perfect browser on a datacenter range, or
  on a proxy IP that is already on a public block list, still reads as what it is. The
  fingerprint join says nothing about where the request came from.
- **Request behaviour and timing.** Pointer motion, typing rhythm, how fast a form is
  filled, whether the page is ever scrolled. Some sites barely fingerprint at all and
  simply watch what you do.
- **Per-account quotas and rate limits.** Volume from one identity is a signal no
  fingerprint fixes.
- **The TLS handshake**, which is decided before any JavaScript runs and which no
  in-page test can see.

You supply those: a clean exit, human pacing, sane volume. invisible_playwright makes
the browser read as a genuine Firefox driven by a real person, which is why it clears
the fingerprint, driver and engine checks. It does not, and cannot, claim to be
undetectable, because the fingerprint is one layer of several and the others are yours
to get right. The realistic framing is "it fixes the fingerprint layer well, and leaves
the network and behaviour layers to you", not "it evades everything".

## Conclusion

A browser fingerprint is a join, not a value. No single attribute identifies you; the
intersection of dozens of low-entropy ones does, and the same join lets a detector catch
the contradictions that hand-assembled automation produces. Deriving every attribute
from one seed is what makes a profile internally consistent and reproducible instead of
a pile of mismatched defaults.

Keep the honest boundary in view. Fixing the fingerprint is necessary and it is a lot of
the battle, but it is the browser layer only. The address you come from and the way you
behave are separate signals, and a coherent fingerprint on a dirty IP still loses.

## Short answers to the questions that lead here

**What is a browser fingerprint?** The combination of dozens of attributes a page reads
from JavaScript - canvas, WebGL, fonts, audio, screen, timezone, user agent, hardware
counts - joined into something that can identify a browser without any cookie.

**Can one attribute identify me?** No. Each one is shared by a large population. The
identifying power is in the join across all of them, where the shared population shrinks
fast.

**Is a fingerprint the same as a cookie?** No. A cookie is stored on your machine and you
can delete it. A fingerprint is computed from what your browser reports, so there is
nothing local to clear.

**Why does automation fingerprint badly?** It emits blank or default values for
attributes it never set (no fonts, a software renderer) and hand-sets others that then
contradict the ones it forgot, so the join describes no real machine.

**Does a good fingerprint make me undetectable?** No. It clears the checks that read the
browser, which is many of them, but not your IP reputation, your request behaviour, your
rate of requests, or the TLS handshake. Those are separate and yours to handle.

**How do I know my fingerprint is consistent?** Read the surfaces yourself and compare
against a stock browser on the same machine, and read the same value twice to confirm it
is stable rather than randomised per call.

## Sources

- The public per-surface and per-suite detectors documented elsewhere in this set
  (CreepJS, BotD, FingerprintJS, sannysoft, BrowserLeaks), each read from its own source.
- This project's seed-derived generator, which produces canvas, WebGL, font, audio,
  screen and hardware attributes from a single seed so the join is consistent by
  construction.
- MDN Web Docs, [Canvas API](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API),
  [WebGL API](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API) and
  [Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API), the
  documented interfaces behind the canvas, WebGL and audio attributes above.

**See also:** [what a FingerprintJS visitor ID actually hashes](fingerprintjs-visitor-id.md),
[why a randomised canvas hash is itself a tell](canvas-fingerprint-noise.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md) before you
trust any verdict.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The join, not any single
value, is the whole idea - and the fingerprint layer is where it stops.*
