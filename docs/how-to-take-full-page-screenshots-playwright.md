---
title: "How to take full-page screenshots with Playwright"
description: "Take full-page screenshots with Playwright, and learn why your captured image is true rendered pixels while the same page's canvas fingerprint is substituted."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 14
---


# How to take full-page screenshots with Playwright

To take a full-page screenshot in Playwright, call `page.screenshot(path="page.png",
full_page=True)`: it captures the entire scrollable document as a single PNG, not just the
visible viewport. Here the captured image is the true rendered pixels even though this
browser spoofs fingerprints, because your screenshot and the page's own canvas fingerprint
of those same pixels come off two different readback paths, on purpose.

Most screenshot how-tos stop at `full_page=True` and a path. That gets you an image.
What it does not tell you is whether the image is the real page or a doctored one, and
on a browser that spoofs fingerprints that is a fair question to ask. It has a precise
answer here, and the answer is the interesting part: your screenshot is the true
rendered pixels, while the page's own canvas fingerprint of those same pixels is
substituted.

This page is the working API, the full-page mechanics people actually trip on, the two
paths and why they diverge, and how to confirm your capture is real.

## The two-line switch, then standard Playwright

Screenshots need no special API. The browser object you get back is a real Playwright
`Browser`, so every screenshot method works exactly as documented upstream. The only
change from plain Playwright is how the browser is launched.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")
    page.screenshot(path="page.png", full_page=True)
```

`seed=42` fixes the identity so the same run reproduces the same machine, which matters
for screenshots specifically: a capture you cannot reproduce is a capture you cannot
diff against a known-good one later. Drop the seed and each session gets a distinct
fingerprint instead; the screenshot behaviour is identical either way.

The async form is the same call with `await`:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com", wait_until="networkidle")
    await page.screenshot(path="page.png", full_page=True)
```

## What full_page actually captures, and what breaks it

`full_page=True` captures the entire scrollable document, not just the viewport. Under
the hood Playwright resizes the capture to the full content height and paints the whole
thing, so a tall page comes back as one tall PNG rather than the slice you can see.

Two things routinely make that image wrong, and neither is a bug in the screenshot call:

- **Lazy-loaded content never entered.** Images and sections that load on scroll do not
  load if nothing scrolls, so the tall capture shows placeholders or blank bands. You
  have to move the page first (the same problem, from the capture side, as
  [scraping lazy-loaded images](how-to-scrape-lazy-loaded-images-playwright.md)).
- **The layout was still settling.** `networkidle` waits for the network, not for
  fonts, images and reflow to finish painting. A short explicit wait, or
  [waiting on a concrete element](how-to-wait-for-page-load-playwright.md), buys the paint.

A scroll-to-bottom nudge that handles the lazy case:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")

    # walk the page down so scroll-triggered content loads, then stop when
    # the document stops growing
    previous = 0
    for _ in range(30):
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(400)
        height = page.evaluate("document.body.scrollHeight")
        if height == previous:
            break
        previous = height

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_load_state("networkidle")
    page.screenshot(path="full.png", full_page=True)
```

Using `page.mouse.wheel` rather than a raw `scrollTo` loop is deliberate: the wheel goes
through the same input path as the Bezier-curve pointer motion the wrapper already
uses, so the scroll looks like a scroll instead of a scripted jump. On sites that watch
behaviour, that difference is the whole point (see the
[headless vs headful](headless-vs-headful.md) notes for why the rendering path being
real matters here too).

## A single element, or a fixed clip

Two narrower captures worth knowing, both stock Playwright.

Screenshot one element by locating it and calling `screenshot` on the locator. Playwright
scrolls it into view and captures just its box:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.locator("#report").screenshot(path="element.png")
```

Or capture a fixed rectangle with `clip`, which is useful when you want the same region
across many pages regardless of what element lives there:

```python
    page.screenshot(
        path="corner.png",
        clip={"x": 0, "y": 0, "width": 640, "height": 400},
    )
```

`clip` and `full_page` are mutually exclusive; pick one per call.

## Two readback paths: why your image is real and the fingerprint is not

Your screenshot is the real page and the site's canvas fingerprint of that same page is
not, because a rendered surface can be read back in two very different ways here and each
is treated differently on purpose. This is the part specific to a fingerprint-spoofing
browser, and the reason this page exists rather than pointing you at the upstream docs.

- **The browser's own privileged capture** is what `page.screenshot` uses. It reads the
  composited result through an internal, trusted path that page JavaScript can never
  reach. This project keeps that path clean, so the bytes you get are the true rendered
  pixels: the actual page, on the real rendering pipeline, exactly as a person would see
  it.
- **Web-facing canvas readback** is what a page's own fingerprinting code uses:
  `toDataURL`, `getImageData`, `readPixels` and the handful of related export calls.
  That path is intercepted, and above a small entropy threshold the pixels are replaced
  with output that is a pure function of the seed and each pixel's position, before the
  script ever sees them. The details of why substitution beats added noise are in
  [canvas fingerprint noise](canvas-fingerprint-noise.md).

So the same page yields two different results depending on who is reading. Your
screenshot is real. The site's canvas fingerprint of that identical page is
seed-substituted and, because it is a pure function of the seed, comes back
byte-identical on Windows and on Linux for the same identity. The consistency proof for
that is in [canvas and WebGL fingerprints identical across OSes](canvas-webgl-cross-platform-consistency.md):
same seed, byte-for-byte matching hash on both platforms.

That these two paths stay separate is not free, and it is guarded. A privileged-readback
patch was dropped once during a rebase, which quietly routed screenshot capture through
the spoofed path instead of the clean one and corrupted every screenshot (the full
write-up is [Playwright screenshot returns noise](playwright-screenshot-returns-noise.md)).
It is now a release-gate check: the byte size of a privileged capture is verified, and the same
render is confirmed still spoofed when read from the web side. The separation is a
tested invariant, not a happy accident.

## Verifying the capture is real pixels

You do not have to take that claim on faith. Read both paths in one session
and compare.

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")

    # path 1: privileged capture -> true rendered pixels
    png = page.screenshot(full_page=True)
    print("screenshot bytes:", len(png))

    # path 2: web-facing canvas readback -> seed-substituted, and stable
    fp = page.evaluate("""() => {
        const c = document.createElement('canvas');
        const ctx = c.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillText('abcdefghijklmnop', 2, 2);
        return c.toDataURL();
    }""")
    a = page.evaluate("""() => {
        const c = document.createElement('canvas');
        const ctx = c.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillText('abcdefghijklmnop', 2, 2);
        return c.toDataURL();
    }""")
    print("canvas readback stable within session:", fp == a)
```

The screenshot bytes are a nonzero PNG of the real page. The canvas readback is stable
within the session (two reads agree, which is the cheap check a detector runs) and
identical across sessions of the same seed, yet it is not the raw host render. Then do
the thing the [bot-detection testing method](how-to-test-bot-detection.md) keeps
insisting on: open the PNG and look at it. A screenshot proves what the page rendered in
a way no text log or hash can, and it is the fastest way to catch a capture that came
back blank because content never loaded.

For a real measurement rather than a claim: the same seed's screenshot, captured with
the window hidden, is a genuine GPU-composited image and matches a visible headful
capture of the same page on the same machine with no fingerprint tell in either. The
hidden-window mechanism that makes that true is its own subject in
[headless vs headful](headless-vs-headful.md); the relevant fact for screenshots is that
`headless=True` here stays on the real rendering path, so your capture is a real render
and not a software-rasterized fallback.

## Conclusion

The screenshot API is plain Playwright, and the only real work is the usual full-page
work: scroll the lazy content in, let the layout settle, then capture. What is worth
carrying away is the guarantee underneath it. Your screenshot comes off the browser's
privileged capture path and is the true page; the site's canvas fingerprint of that same
page comes off the web-facing readback path and is seed-substituted. Two paths, kept
separate and gated, so a captured image and a captured fingerprint can honestly disagree
about the same pixels.

## Short answers to the questions that lead here

**How do I take a full-page screenshot in Playwright?** `page.screenshot(path="p.png",
full_page=True)`. Scroll the page first if it lazy-loads, and wait for the layout to
settle rather than trusting `networkidle` alone.

**Is my screenshot the real page or a spoofed one?** The real page. Screenshots use the
browser's privileged capture path, which is kept clean. Only web-facing canvas readback
is substituted.

**Then why does the site's canvas fingerprint not match my screenshot?** Because it is
read through a different, intercepted path on purpose. The fingerprint is a pure function
of the seed; your screenshot is the actual rendered pixels.

**Why is my full-page capture blank or full of placeholders?** Scroll-triggered content
never loaded. Walk the page to the bottom, scroll back to the top, wait, then capture.

**Can I screenshot just one element?** Yes: `page.locator("#id").screenshot(...)`.
Playwright scrolls it into view and captures its box. Use `clip` for a fixed rectangle
instead.

**Does headless mode give me a worse screenshot?** Not here. `headless=True` stays on the
real GPU rendering path with the window hidden, so the capture is a genuine composited
image rather than a software fallback.

## Sources

- This project's render-readback interception work and its release gates, including the
  privileged-readback check that exists because a dropped patch once corrupted every
  screenshot, and the cross-platform validation (same-seed byte-identical canvas hashes
  on Windows and Linux).
- Playwright's own screenshot documentation for
  [`full_page` and `clip`](https://playwright.dev/python/docs/api/class-page#page-screenshot),
  and locator screenshots, which apply unchanged because the returned browser is a real
  Playwright `Browser`.

**See also:** [canvas and WebGL fingerprints identical across OSes](canvas-webgl-cross-platform-consistency.md)
for the substitution path your fingerprint comes off, and
[headless vs headful](headless-vs-headful.md) for why the hidden-window capture is still
a real render.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The privileged-readback
gate on this page exists because losing that patch once corrupted every screenshot we
took.*
