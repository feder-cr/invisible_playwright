---
title: "Crawl list pages to detail pages with Playwright"
description: "Crawl list-to-detail pages with Playwright in two phases: collect the card links, visit each detail URL, re-associate the data, and pace the fan-out."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 70
---


# Crawl list pages to detail pages with Playwright

To crawl from list pages to detail pages with Playwright, split the crawl into two phases:
first read every summary card on the list into plain Python values, each carrying its
detail-page link, then loop over those links, load each detail page, and merge its full
record back into the row it came from. Keeping the two phases separate is what stops element
handles from dying mid-navigation; keeping the whole run under one seeded identity is what
stops the fan-out of detail loads from reading as a machine.

Almost every real crawl has this shape. A list page shows summary cards, one per record,
and the full record lives somewhere else: on each item's own URL. The card has a title, a
price, a thumbnail, and a link. Everything you actually came for, the description, the
attributes, the fields nobody bothered to duplicate onto the card, is one navigation away.

This page is how to do that without losing the association, how to decide new-tab versus
same-tab and how many detail pages to open at once, and the one stealth fact that matters
more than any single page's fingerprint: the shape of this crawl is a fan-out, and the
fan-out is the signal.

## The two-phase shape of a list-to-detail crawl

Phase one reads the list and produces a set of rows. Each row is what the card told you
plus the link to the detail page. Phase two consumes those rows, loads each link, reads
the full record, and merges it back into the row.

The mistake that makes this painful later is collapsing the two phases into one loop that
navigates away from the list mid-iteration. The moment you call `goto` on a detail URL,
the list document is gone, and so is every element handle and locator result you were
still holding from it. This is the same
[execution-context destruction](execution-context-destroyed.md) that
[breaks a naive pagination loop](how-to-scrape-paginated-pages-playwright.md): a handle
does not survive the navigation that replaces the document it was bound to.

The fix is to read everything you need from the list into plain Python values first, close
the door on the list document, and only then navigate. Strings survive navigation; handles
do not.

## Phase one: collect the links and keep the row they came from

Read the cards into a list of dictionaries. Extract the `href` as a string here, on the
list page, while the elements still exist. Do not keep the element around to click later.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listings")

    rows = []
    for card in page.locator("li.card").all():
        rows.append({
            "title": card.locator(".title").inner_text(),
            "price": card.locator(".price").inner_text(),
            "detail_url": card.locator("a").get_attribute("href"),
        })

    print(f"collected {len(rows)} rows from the list")
```

`rows` is now pure data. The list page can be navigated away, reloaded, or closed and
nothing in `rows` is affected. Each entry already carries the summary fields and the URL,
so when the detail data comes back there is a row waiting to receive it. That is the whole
trick to not losing the association: the association is a key you built in phase one, not
something you reconstruct in phase two.

If the list itself spans several pages, run this collection across every page first, using
the [pagination re-query pattern](how-to-scrape-paginated-pages-playwright.md), and let
phase two start only once the full row set exists.

## Phase two: visit each detail page and re-associate

Now walk the rows and enrich each one in place. The detail page is a fresh document each
time; read from it into the row's dictionary and move on.

```python
from urllib.parse import urljoin

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    for row in rows:
        page.goto(urljoin("https://example.com/", row["detail_url"]))
        row["description"] = page.locator("#description").inner_text()
        row["sku"] = page.locator("[data-sku]").get_attribute("data-sku")
        # the row now holds both the card summary and the full record
```

Because the row is mutated in place, the summary you read in phase one and the detail you
read in phase two stay attached to the same object. There is no join step, no matching by
title, no fragile re-lookup. The originating list row is the record; the detail page just
fills in its blanks.

One page, reused across every detail URL, is the simplest correct version. It is also
serial: one navigation finishes before the next begins. That is fine for a few dozen
records and too slow for a few hundred, which is where the next two questions come in.

## New tab or same tab, and how many at once

Reuse one tab by default, and open a new tab only when you need the list and a detail page
alive at the same time. Treat how many detail pages to open at once as a pacing decision
rather than a hardware one. These are two independent decisions that get conflated here.

**Same tab versus new tab.** Reusing one page, as above, means each `goto` replaces the
previous detail document. You never hold two detail pages at once, memory stays flat, and
there is nothing to clean up. This is the right default. Open a new tab only when you
genuinely need the list and a detail page alive at the same time, for example when the
detail URL is behind a click that must originate from the list. When you do, open the tab,
read it, and close it, so tabs do not accumulate.

```python
with InvisiblePlaywright(seed=42) as browser:
    for row in rows:
        detail = browser.new_page()          # a second tab in the same identity
        detail.goto(urljoin("https://example.com/", row["detail_url"]))
        row["description"] = detail.locator("#description").inner_text()
        detail.close()                        # close it before opening the next
```

Every page opened this way shares the browser's fingerprint, because the fingerprint
belongs to the browser, not the tab. That matters for the concurrency decision below and
is the reason [new_page and new_context behave differently](playwright-new-page-vs-new-context.md):
a new context is a new identity, which is not what you want inside one crawl.

**How many at once.**
[Opening detail pages concurrently](how-to-scrape-multiple-pages-in-parallel-playwright.md)
is faster and easy to overdo. Cap
it with a semaphore so the fan-out has a fixed width, and keep the list rows as the unit of
work so the association survives concurrency untouched.

```python
import asyncio
from urllib.parse import urljoin
from invisible_playwright.async_api import InvisiblePlaywright

async def enrich(browser, row, limiter):
    async with limiter:                       # at most N detail pages open at once
        page = await browser.new_page()
        try:
            await page.goto(urljoin("https://example.com/", row["detail_url"]))
            row["description"] = await page.locator("#description").inner_text()
        finally:
            await page.close()

async def main(rows):
    async with InvisiblePlaywright(seed=42) as browser:
        limiter = asyncio.Semaphore(4)        # width of the fan-out
        await asyncio.gather(*(enrich(browser, row, limiter) for row in rows))

asyncio.run(main(rows))
```

Four is a starting point, not a recommendation. The right width is bounded less by your
machine than by the next section.

## The fan-out is the signal, not the fingerprint

The strongest signal a list-to-detail crawl emits is its request velocity, not any single
page's fingerprint: hundreds of detail loads from one session in a few minutes describe a
machine no matter how real each page looks. This is the part that most list-to-detail guides
never mention, and the reason a fingerprint-perfect crawl still gets blocked.

Look at the traffic this shape produces. One session, one identity, and then hundreds of
detail-URL loads in a few minutes, all radiating from the same origin, all in the same
navigation pattern, at a rate no human reading listings would ever produce. That volume
and its regularity is a stronger signal than anything on any single page. You can match a
real browser's canvas hash, WebGL renderer, fonts and audio context exactly, on every one
of those requests, and the request-rate curve alone still describes a machine.

This is worth stating plainly because it inverts where people spend effort. A great deal
of energy goes into the per-page disguise, which the earlier phases and this project's
seeded identity already handle. Almost none goes into the shape of the fan-out, which is
what a velocity-based system is actually scoring. We measured this on our own release
gates: a stealth check that hammered one scoring endpoint from one address produced a
velocity flag, and the flag was correct. It belonged to the pace of the harness, not to
the browser it was testing. The disguise was clean; the rate was the tell.

So the defense splits in two. Keep the identity consistent across the whole fan-out, so
the hundreds of loads read as one coherent session rather than a fingerprint that flickers
request to request. Then pace that fan-out so its rate does not describe a machine.

## Pace the fan-out under one seeded identity

Consistency first. Passing a seed fixes the identity for the entire crawl:

```python
with InvisiblePlaywright(seed=42) as browser:
    ...   # every page and every tab shares this one identity for the whole run
```

Every detail page you open under this browser reports the same GPU, the same canvas hash,
the same audio context, the same fonts. A detector correlating requests sees one session
doing a lot, which is coherent, rather than many near-identical sessions, which is not.
Reusing one seeded browser for the whole fan-out is what makes the volume legible as a
single visitor instead of a swarm. The [configuration page](configuration.md) covers how
that identity is derived and how the timezone follows the exit.

Pace second. Add jitter between loads and keep the concurrency width modest, so the rate
curve is not a flat line at machine speed. This is not politeness; request velocity is a
scored detection input, which is why it has [its own page](how-to-rate-limit-your-scraper-playwright.md).

```python
import asyncio, random
from urllib.parse import urljoin
from invisible_playwright.async_api import InvisiblePlaywright

async def enrich(browser, row, limiter):
    async with limiter:
        await asyncio.sleep(random.uniform(0.8, 2.5))   # jittered spacing, not a fixed interval
        page = await browser.new_page()
        try:
            await page.goto(urljoin("https://example.com/", row["detail_url"]))
            row["description"] = await page.locator("#description").inner_text()
        finally:
            await page.close()

async def main(rows):
    async with InvisiblePlaywright(seed=42) as browser:
        limiter = asyncio.Semaphore(3)
        await asyncio.gather(*(enrich(browser, row, limiter) for row in rows))

asyncio.run(main(rows))
```

A fixed sleep is itself a pattern; a uniform interval between loads is as machine-shaped as
no interval at all. Jitter the gap, cap the width, and the fan-out stops looking like a
burst and starts looking like someone working through a list.

## Conclusion

A list-to-detail crawl is two phases with a key between them. Read the cards into plain
rows in phase one while the list document still exists, then in phase two load each detail
URL and merge its record back into the row it came from, so the association is something
you built rather than something you reconstruct. Default to reusing one tab, open a new one
only when you need two documents alive, and cap concurrency with a semaphore.

Then remember what the shape actually broadcasts. Hundreds of detail loads from one session
in minutes is a volume signal that outweighs any single page's fingerprint. Keep the
identity consistent across the whole fan-out so the volume reads as one visitor, and pace
it with jitter so the rate does not describe a machine. The per-page disguise is the part
that is already handled; the fan-out is the part you have to shape yourself.

## Short answers to the questions that lead here

**How do I scrape data that is only on the detail page, not the list?** In two phases.
Collect the detail links from the list into plain Python strings first, then loop over them
and navigate to each one, merging the full record back into the list row it came from.

**How do I keep the list-page data attached to the detail-page data?** Build the row in
phase one as a dictionary that already holds the summary fields and the detail URL, then
mutate that same dictionary in place when the detail page loads. The association is a key,
not a later join.

**Should I open detail pages in a new tab or the same tab?** Same tab by default: reuse one
page and let each `goto` replace the last detail document. Open a new tab only when you
need the list and a detail page alive at once, and close it before opening the next.

**How many detail pages can I open at the same time?** Cap it with a semaphore and treat
the width as a pacing decision, not a hardware one. A modest width with jitter between loads
is bounded by the velocity signal you are trying not to produce, not by your machine.

**Why do I still get blocked when every page looks like a real browser?** Because the
fan-out itself is a signal. Hundreds of loads from one identity in minutes is a velocity
pattern that a per-page fingerprint cannot hide. Pace the crawl.

**Does one seed cover the whole crawl or do I need a new one per page?** One seed for the
whole crawl. Every page and tab under one seeded browser shares the identity, which is
exactly what makes the volume read as a single coherent session instead of a swarm.

## Sources

- This project's [Quickstart](quickstart.md) and [Configuration](configuration.md) pages
  for the real API surface used above: `InvisiblePlaywright(seed=...)` returning a stock
  Playwright `Browser`, sync and async.
- This project's release gates, including the velocity flag that a stealth check produced
  against its own scoring endpoint, where the flag belonged to the pace of the harness
  rather than to the browser it was testing.

**See also:** [how to scrape paginated pages](how-to-scrape-paginated-pages-playwright.md)
for collecting the list across many pages, [new_page vs new_context](playwright-new-page-vs-new-context.md)
for why one browser is one identity, and [how to rate limit your own scraper](how-to-rate-limit-your-scraper-playwright.md)
for shaping the fan-out.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The two-phase split and the
velocity flag above are both mistakes this project made before writing them down.*
