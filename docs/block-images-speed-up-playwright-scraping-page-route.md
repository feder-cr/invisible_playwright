---
title: "Block images to speed up scraping (and when not to)"
description: "Blocking images, fonts and media with route.abort() cuts bandwidth and wall-clock time, but a no-image waterfall is itself a tell. When to use it and when not to."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 92
---


# Block images to speed up scraping (and when not to)

Aborting image, font and media requests is one of the biggest speed wins available to a
Playwright scraper, and it is two lines of code. It is also, on the wrong page, a clean
way to get yourself noticed. A browser that fetches HTML and JavaScript and never a
single image produces a request pattern no human session shows.

So this is not a pure win, and that is the reason it gets its own page. Blocking
resources is the right call for internal test runs and for bulk data pulls where transfer
cost dominates. It is the wrong call on exactly the sessions you most want to look human.
This page covers what it saves, the two lines that do it, the tell it creates, and where
the line between the two cases falls.

## What blocking resources actually saves

On most pages the HTML and the JavaScript are a small fraction of the bytes. Images,
web fonts and any autoplaying media are the rest, and on an image-heavy listing page
they are the large majority of the transfer. If your scraper only reads text, prices or
structured data out of the DOM, every one of those bytes is downloaded and thrown away.

Aborting them changes two things you can measure:

- **Bandwidth.** On an image-heavy page the transfer routinely drops from a few megabytes
  to a few hundred kilobytes. Over tens of thousands of pages that is the difference
  between a proxy bill you notice and one you do not.
- **Wall-clock time.** The page reaches its "loaded" state sooner because it is no longer
  waiting on the slowest large assets, and `page.goto` returns earlier. The saving is
  largest on pages that block rendering on fonts or lazy-load a wall of images.

The mechanic is stock Playwright, and it works identically under invisible_playwright:
the wrapper returns a real Playwright `Browser`, so [`page.route` and `route.abort`](https://playwright.dev/python/docs/network#abort-requests)
behave exactly as documented upstream. There is no special API to learn.

## The two lines that do it

Register a route handler, abort the resource types you do not need, and continue
everything else. The launch is the usual two-line switch from stock Playwright.

```python
from invisible_playwright import InvisiblePlaywright

# resource types that are pure weight for a text/data scrape
BLOCK = {"image", "media", "font"}

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in BLOCK
        else route.continue_(),
    )

    page.goto("https://example.com")
    print(page.title())
```

The async form is the same shape:

```python
from invisible_playwright.async_api import InvisiblePlaywright

BLOCK = {"image", "media", "font"}

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()

    async def router(route):
        if route.request.resource_type in BLOCK:
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", router)
    await page.goto("https://example.com")
    print(await page.title())
```

Two notes that save time later. Register the route **before** `goto`, or the first
navigation's assets are already in flight before the handler exists. And keep
`stylesheet` out of the block set unless you have checked the page: some layouts compute
element positions from CSS, and dropping it can move the very nodes your selectors point
at. `image`, `media` and `font` are the safe core; add `stylesheet` only after you
confirm the DOM you read still resolves.

## The tell: a request waterfall no human produces

A real person's browser loads images, fetches the fonts the page asks for, and pulls the
tracking pixel and the hero media without being told twice - and a scraper that refuses
all three produces a request waterfall no human session shows. That is the honest
caveat, and it is the whole reason this page is not just "block everything, go faster".

The network waterfall of a genuine session has a characteristic shape: a burst of mixed
resource types, images arriving late and out of order, fonts blocking early paint. A
session that fetches the HTML document and a handful of scripts and then goes silent has
none of that shape. It is a request pattern that belongs to a scraper and to almost
nothing else.

This is not a fingerprint that the engine can spoof. invisible_playwright is built to
look like a real browser driven by a real person, and that is why it passes most
detection: the [fingerprint, the TLS handshake and the driver layer read as a genuine
Firefox](does-playwright-support-firefox-stealth.md). But *which requests you choose to
make* is your behaviour, not the browser's identity. If your code refuses every image on
a page a human would load, you have hand-built a behavioural tell that no stealth layer
touches, the same category as a pointer that teleports or a form filled in eighty
milliseconds. The [checklist for being flagged on one site](playwright-detected-as-bot.md)
puts behaviour above the proxy for exactly this reason.

So the block set is a decision about the session, not a global optimisation you turn on
once and forget.

## Where to block, and where not to

Split your work into two buckets and treat them differently.

**Block resources freely when load does not need to look human:**

- **Internal test runs and CI.** You are checking your own selectors and your own
  extraction, not passing anyone's inspection. Speed is the only axis that matters.
- **Bulk data pulls where transfer cost dominates** and the endpoint is not scoring you,
  for example a site you have permission to crawl, an API-backed page, a
  [sitemap walk](how-to-scrape-a-sitemap-playwright.md) over thousands of URLs where the
  proxy bill is the real constraint.

**Leave images loading when the session needs to look human:**

- **The first visit to a page that is clearly scoring the session**, or any flow that
  starts behind a challenge. The seconds you save are not worth the waterfall shape.
- **Anything interactive that a human would look at.** If the run
  [scrolls, clicks and reads](human-mouse-movement.md), it should also be pulling the
  images a reader would see.

A middle path exists and is often the right one: block the heaviest single type only.
Dropping `media` and web `font` requests while still loading `image` keeps most of the
human waterfall shape and still cuts real weight, because autoplaying video and large
font files are frequently the worst offenders. Measure your own pages before deciding the
block set is worth the tell.

## What this does not fix

Blocking resources is a speed and cost lever. It is not a stealth lever, and on its own
it changes none of the things that actually decide whether a session gets through.

- **IP reputation.** A [datacenter or already-flagged
  exit](can-websites-detect-a-datacenter-proxy-ip.md) is visible before the first byte of
  HTML, and no route rule touches it. Supply a clean proxy yourself.
- **Per-account quotas and rate limits.** Fetching fewer bytes per page does not raise a
  cap. If anything, going faster lets you hit it sooner, so pair aggressive blocking with
  [deliberate pacing](how-to-rate-limit-your-scraper-playwright.md).
- **Behaviour and timing.** The waterfall shape above is one behavioural signal; pointer
  motion, dwell time and request cadence are others, and they are your responsibility, not
  the engine's.

invisible_playwright makes the browser read as a real Firefox. The clean proxy and the
human pacing are the parts you bring. Blocking images speeds up the run; it does not move
any of these three.

## Conclusion

`route.abort()` on `image`, `media` and `font` is a large, cheap speed and bandwidth win,
and it works identically under invisible_playwright because the wrapper hands you a real
Playwright browser. The catch is that a no-image request waterfall is a behavioural tell,
so scope it: block freely on test runs and bulk pulls where load is the constraint, and
leave images loading on the sessions you most want to read as human. When in doubt, block
the heaviest type only and measure, rather than switching everything off and hoping the
shape goes unnoticed.

## Short answers to the questions that lead here

**Does blocking images make Playwright faster?** Yes, often substantially, because images,
fonts and media are usually the bulk of a page's bytes and a text scrape throws all of
them away. Wall-clock time drops because the page stops waiting on the slowest assets.

**How do I block images in Playwright?** Register a `page.route("**/*", ...)` handler
before `goto` and call `route.abort()` when `route.request.resource_type` is `image`,
`media` or `font`, and `route.continue_()` otherwise.

**Does blocking images get me detected?** It can. A session that fetches HTML and scripts
but never an image produces a request waterfall no human session shows. That is a
behavioural tell, separate from your fingerprint, and no stealth layer hides it.

**Should I also block CSS and JavaScript?** Usually not. Many pages compute layout and
render content from CSS and JavaScript, so blocking them can move or remove the nodes your
selectors read. Start with `image`, `media` and `font` only.

**Will this make invisible_playwright undetectable?** No, and nothing does. It makes the
browser read as a real Firefox, which is most of the battle, but IP reputation, account
quotas, rate limits and timing are yours to handle with a clean proxy and human pacing.

**When should I not block images?** On the first visit to a page that is clearly scoring
the session, and on any interactive flow a human would actually look at. There, looking
human is worth more than the seconds you save.

## Sources

- Playwright's [request interception API](https://playwright.dev/python/docs/network#abort-requests)
  (`page.route`, `route.abort`, `route.continue_` and
  [`request.resource_type`](https://playwright.dev/python/docs/api/class-request#request-resource-type)),
  read from its own documentation rather than a rendered example.
- This project's own measurements of per-page transfer on image-heavy pages, where images,
  fonts and media are consistently the majority of the bytes.
- The behavioural-tell reasoning in this project's detection notes, where request shape is
  treated as behaviour rather than fingerprint.

**See also:** [pulling images down on purpose when you do want them](how-to-download-images-in-bulk-playwright.md),
[the checklist for being detected on one site](playwright-detected-as-bot.md), and
[testing bot detection without a false pass](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The speed is stock
Playwright; the tell is the part the speed guides leave out.*
