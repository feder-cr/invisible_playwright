---
title: "What BrowserLeaks actually tests, surface by surface"
description: "What BrowserLeaks tests, surface by surface: canvas hash, WebGL, WebRTC, fonts, ClientRects. Why a unique panel is not a fail, and the miss it never flags."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 7
---


# What BrowserLeaks actually tests, surface by surface

BrowserLeaks is the tool people reach for when they want to see a number rather than
read a verdict. It prints the raw value of each browser surface - the canvas hash, the
WebGL parameter block, the WebRTC candidate addresses, the installed font list, the
ClientRects geometry - each on its own page.

That design is its strength and the source of most of the panic it causes. It shows you
everything and scores none of it. So the panel that lights up red and says "unique" is
the one everybody worries about, while the thing that actually gets a session flagged -
two panels that quietly contradict each other - is displayed in plain text and never
called out, because BrowserLeaks does not compare its own pages to one another.

This page is what each surface measures, why "unique" is not the alarm it looks like,
and how to read the tool for the failure it will not point at.

## A raw-value inspector, not a scorer

BrowserLeaks reads each browser surface and prints its raw value; it does not judge whether the values are coherent. It shows the canvas hash, the WebGL block, the font list and how rare each is in its sample, but it never cross-checks one surface against another and never hands you a trust score. That is the distinction that makes it useful, and the one people miss.

[CreepJS asks whether you are lying](creepjs-explained.md): it takes a clean copy of the
built-ins, walks descriptors and prototypes, and cross-checks every surface against
every other one, then hands you a single trust score. BrowserLeaks does none of that. It
reads a value, hashes it if it is a bitmap, tells you how rare that exact value is in its
own visitor sample, and moves on to the next page.

That means two things. First, a "unique" result on any single BrowserLeaks page is a
statement about rarity in their sample, not about whether your browser is coherent. A
perfectly real, perfectly consistent machine can be unique. Second, and this is the part
that matters, BrowserLeaks will show you a Windows font list on one page and a WebGL
renderer that no Windows machine ships on another, side by side, both green, and say
nothing - because it has no page that reads both. The contradiction is on screen. The
tool just is not looking for it.

## Five pages, surface by surface

Each page answers one narrow question. Using them as if they were interchangeable is
most of why people get confused.

- **Canvas.** Draws text and shapes to a `<canvas>`, reads the pixels back, and hashes
  them. The hash is a fingerprint of how your specific GPU, driver and font stack
  rasterize that exact drawing. It is not a property you can set; it is
  [a readback of what the machine actually painted](browserleaks-canvas-webgl-hash.md).
- **WebGL.** Two things on one page: the
  [`UNMASKED_VENDOR_WEBGL` / `UNMASKED_RENDERER_WEBGL`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  strings, and a long block of numeric limits (max texture size, shader precision
  ranges, supported extensions). The strings are the famous part; the numeric block is
  the part that has to agree with them, and a disagreement is a contradiction a scoring
  detector catches even though BrowserLeaks prints both without comment.
- **WebRTC.** Asks the browser to gather ICE candidates and prints the addresses it
  finds. This is where a proxied browser can leak the real local address next to the
  proxy exit, or [return nothing at all, which is its own tell](webrtc-leak-proxy.md).
- **Fonts.** Measures the width of a probe string in a long list of font names and
  reports which ones are installed. Under a Windows user agent this list must look like
  Windows, not [the font set a bare Linux container ships](detect-installed-fonts-javascript.md).
- **ClientRects.** Reads the sub-pixel bounding boxes of positioned elements with
  [`getClientRects()`](https://developer.mozilla.org/en-US/docs/Web/API/Element/getClientRects).
  Like canvas, the exact fractional geometry varies with the
  rendering stack, so it is a fingerprint rather than a setting.

There are more pages - TLS, HTTP headers, geolocation, DNS - but these five are the ones
people arrive worried about, and they are all raw-value readouts with no cross-check
between them.

## The panel people fear: "unique" is not "wrong"

The canvas page is the one that sends people to search engines, and usually for the
wrong reason.

It prints "unique" in red, a rarity percentage, and a signature hash, and the instinct
is to make the number stop being unique. That instinct is backwards. A real browser on a
real machine is very often unique on canvas, because the rasterization depends on a
specific GPU and driver combination that few other visitors share. Uniqueness is the
baseline for real hardware, not the anomaly.

The failure modes that actually matter on this panel are the opposite of "unique":

- **A hash that changes on every read.** If you refresh and the canvas signature is
  different each time, something is adding per-call noise, and per-call randomness is
  itself the tell - a real GPU is deterministic, so
  [a hash that will not repeat is the cheapest tampering signal there is](canvas-fingerprint-noise.md).
- **A hash that matches thousands of other visitors exactly.** That is the signature of
  a software rasterizer, the same fallback renderer every headless container falls back
  to, which is a "this is a datacenter" flag wearing a "not unique" badge.

So the correct thing to want from the canvas panel is not a common value. It is a stable,
plausible, hardware-shaped value that reads identically twice and is not the shared
software-renderer hash. This product derives that value from the seed, so it is unique in
the way real hardware is unique and byte-identical on a re-read, rather than either
randomized per call or collapsed onto the software default.

## The risk it shows but never flags: two panels that disagree

Here is the thing BrowserLeaks displays and will never point at, and it is the thing a
real scoring detector fails you on.

Every surface on the site carries an implicit claim about the same machine. The user
agent says a platform. The WebGL renderer string implies a GPU vendor. The font list
implies an operating system. The canvas and ClientRects hashes imply a specific
rasterization stack. On a real browser these all describe one coherent computer. On a
patched-together disguise they describe two or three different ones, and each individual
page still looks fine on its own.

BrowserLeaks will happily show you:

- A user agent claiming Windows, and a font page missing the fonts every Windows install
  ships.
- A WebGL renderer string naming a discrete GPU, and a canvas hash equal to the shared
  software-renderer value - the string says NVIDIA while the pixels were drawn by a
  software rasterizer.
- A WebRTC page reporting a timezone-consistent exit, and a geolocation page a continent
  away.

None of those triggers a warning on BrowserLeaks, because no page reads another page.
CreepJS would collapse your trust score on any one of them. This is exactly the gap the
comparison method in [how to test whether your browser is detected](how-to-test-bot-detection.md)
exists to close: the tool gives you the raw values, and it is on you to check that they
agree, because it will not.

The practical consequence: do not read BrowserLeaks page by page and tick each one green.
Read the values across pages as a set and ask whether one machine could produce all of
them. That is the check the site skips.

## Reading BrowserLeaks with the product, twice and against a stock browser

The way to use BrowserLeaks well is to automate the cross-check it does not do: read the
same surface twice for stability, and read the surfaces together for coherence.

Switching from plain Playwright is a two-line change, and the returned object is a real
Playwright `Browser` with every standard method:

```python
from invisible_playwright import InvisiblePlaywright

# seed fixed, so every surface is reproducible and a failing run can be replayed
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    # 1. Stability: read the canvas signature twice. A real GPU is deterministic,
    #    so these two hashes must be identical. If they differ, something is
    #    adding per-call noise, which is a tell in its own right.
    page.goto("https://browserleaks.com/canvas")
    first = page.locator("#canvas-hash").inner_text()
    page.reload()
    second = page.locator("#canvas-hash").inner_text()
    assert first == second, "canvas hash changed between reads - per-call noise"

    # 2. Coherence: pull the WebGL renderer string and the font list, then check
    #    they tell the same story about one machine.
    page.goto("https://browserleaks.com/webgl")
    renderer = page.locator("#UNMASKED_RENDERER_WEBGL").inner_text()
    print("WebGL renderer:", renderer)

    page.goto("https://browserleaks.com/fonts")
    print("fonts detected:", page.locator("#fonts-metrics-report").inner_text())
```

Two rules turn that from a screenshot into a test.

First, assert presence, not absence. A BrowserLeaks page that comes back empty, blocked
or still spinning is a failure, not a clean pass - the WebRTC page returning no candidates
at all reads as clean to a naive check and as suspicious to a real detector.

Second, and this is the one caveat worth being honest about: the raw canvas and WebGL
hashes are stable and reproducible per seed, but they are still *unique*, because real
hardware is unique. If your threat model needs many sessions that are individually
plausible, change the seed per session rather than reusing one - a reproducible
fingerprint is a debugging tool, not a crowd to hide in. Run the same read against a stock
browser on the same machine and diff the two reports field by field; anything that differs
other than the exit address is your candidate, whatever colour the panel is.

## Conclusion

BrowserLeaks is the right tool for reading a specific surface and the wrong tool for
deciding whether you are safe. It prints raw values page by page and scores none of them,
so the "unique" canvas panel that scares people is usually just real hardware behaving
normally, while the failure that actually matters - two panels describing two different
machines - is on screen in plain text with no warning attached.

Read it as a set, not a stack of independent verdicts. Check that each value is stable on
a re-read, check that the values across pages could all come from one computer, and diff
the whole thing against a stock browser. Do that and BrowserLeaks becomes a precise
instrument. Read it panel by panel and it will scare you about the wrong number and stay
silent about the right one.

## Short answers to the questions that lead here

**BrowserLeaks says my canvas is unique - am I detected?** Almost certainly not.
Uniqueness is the baseline for real hardware, which rasterizes in a way few other
visitors share. The tell is the opposite: a hash that changes every read, or one that
matches thousands of others exactly.

**Does BrowserLeaks give a bot score?** No. It is a raw-value inspector. It prints each
surface and how rare it is, but it never scores cross-surface consistency the way CreepJS
does, so it cannot tell you whether your surfaces agree with each other.

**Why does BrowserLeaks look clean but I still get blocked?** Because it showed you values
that individually look fine and never checked that they describe one machine. A Windows
user agent with a Linux font list passes every BrowserLeaks page and fails a real
detector.

**Which BrowserLeaks page matters most?** None alone. The value is reading them together:
the canvas, WebGL, font and WebRTC pages have to tell the same story about one computer.

**How do I know if my canvas hash is randomized?** Load the canvas page twice. A real GPU
gives the identical hash both times. If it differs, something is injecting per-call noise,
which is itself a signal.

**Should I try to make my canvas hash common?** No. Common usually means the shared
software-renderer value that every headless container falls back to, which reads as a
datacenter. You want a stable, hardware-shaped, unique value, not a popular one.

## Sources

- BrowserLeaks' own test pages named above, retrieved 2026-08-28:
  [browserleaks.com/canvas](https://browserleaks.com/canvas),
  [browserleaks.com/webgl](https://browserleaks.com/webgl),
  [browserleaks.com/webrtc](https://browserleaks.com/webrtc),
  [browserleaks.com/fonts](https://browserleaks.com/fonts),
  [browserleaks.com/rects](https://browserleaks.com/rects) (ClientRects),
  [browserleaks.com/tls](https://browserleaks.com/tls),
  [browserleaks.com/ip](https://browserleaks.com/ip) (HTTP headers),
  [browserleaks.com/geo](https://browserleaks.com/geo), and
  [browserleaks.com/dns](https://browserleaks.com/dns), each read from its own page
  rather than from a summary score.
- This project's release gates, including the WebRTC gate whose absence-only assertions
  passed a feature that returned nothing, and the seed-derived canvas readback checked for
  stability across reads.
- [CreepJS's own source](https://github.com/abrahamjuliot/creepjs), for the cross-surface
  trust score this page contrasts against BrowserLeaks' page-by-page raw values; the rest
  of the companion detector pages in this set, read from each tool's own behaviour.

**See also:** [how CreepJS scores the cross-surface consistency BrowserLeaks skips](creepjs-explained.md),
[what the canvas and WebGL hash actually measures](browserleaks-canvas-webgl-hash.md), and
[the comparison method that catches contradictions no single panel flags](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed makes every surface
reproducible, so the two-read stability check above is a test rather than a guess.*
