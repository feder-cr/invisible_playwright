---
title: "How to scrape flexible-date fare calendars with Playwright"
description: "Scrape a flexible-date fare calendar with Playwright: wait for each month's price XHR before reading the grid, then page forward across a seed-stable sweep."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 37
---


# How to scrape flexible-date fare calendars with Playwright

To scrape a flexible-date fare calendar with Playwright, wait for each month's price XHR
to land before you read the grid, wrap every month-turn's click in that same wait, and run
the whole multi-month sweep on one pinned identity so the host sees one browser instead of
a new device on every turn. A screenshot-then-parse approach fails here because it captures
a grid that is still filling in.

A flexible-date fare calendar looks like a static month of prices, and it is the
opposite. The grid is a shell that arrives empty, each day cell gets its number from a
separate request, and turning to the next month fires a fresh batch. The unit of work is
not "the page loaded" but "this month's price fetch came back", and the rest of this page
is that order in code.

## Why the calendar is not in the served HTML

Open the page source of a fare calendar and the day cells are there but the prices are
not. The document ships a grid of empty cells and a script; the script then issues one or
more XHR calls that return the prices for the visible month, and the cells fill in after
the first paint.

That has three consequences for a scraper:

- **The served HTML has no prices.** A plain `page.content()` right after `goto` returns
  the empty shell. So does a screenshot taken too early.
- **The prices arrive asynchronously.** There is a window, sometimes a second or more on a
  slow exit, where the grid is half filled. Read it then and you get some real numbers and
  some blanks, with no error to tell you which is which.
- **Each month is a separate fetch.** Moving to the next month does not reload the page.
  It fires another batch of XHR calls and rewrites the same cells in place.

So the unit of work is not "the page loaded", it is "this month's price fetch came back".
That is the thing to wait on.

## Wait for the month's prices before you read the grid

The reliable signal is the network response, not a fixed sleep and not `networkidle` on a
page that keeps a socket open. Stock Playwright's
[`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
lets you name the call that carries the prices and block until it lands. The `browser`
object below is a real Playwright `Browser`, so every method is the documented one.

```python
from invisible_playwright import InvisiblePlaywright

# match the request your target uses for calendar prices; read it once from the
# network tab and pin the path fragment. This is a generic example endpoint.
PRICE_CALL = "/calendar/prices"

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    # the first month's batch fires on load, so wrap the navigation
    with page.expect_response(lambda r: PRICE_CALL in r.url and r.ok):
        page.goto("https://example.com/flights/calendar", wait_until="domcontentloaded")

    # the response landing does not guarantee the DOM finished painting the cells,
    # so also wait until every cell that should carry a price actually has text
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.day-cell'))"
        ".every(c => c.querySelector('.price') && c.querySelector('.price').textContent.trim())"
    )

    prices = page.eval_on_selector_all(
        ".day-cell",
        "cells => cells.map(c => ({"
        "  day: c.getAttribute('data-date'),"
        "  price: c.querySelector('.price') ? c.querySelector('.price').textContent.trim() : null"
        "}))",
    )
    print(prices)
```

Two waits, because they catch different failures. The response wait catches "the data has
not come back yet". The `wait_for_function` catches "the data came back but the cells are
still being written". Reading between those two moments is the classic half-filled grid.

If you would rather skip the DOM entirely, the price call returns the same numbers as
JSON, and you can [read them straight from the XHR body](how-to-capture-xhr-api-responses-playwright.md)
instead of scraping cells that a redraw can move under you. When the wait itself is the
tricky part, the mechanics of [waiting for the page to actually finish](how-to-wait-for-page-load-playwright.md)
cover why `networkidle` misleads on pages like these.

## Page forward one month at a time

To find a cheap date you sweep months, and each month advance is its own fetch to wait on.
Wrap the click that turns the month in the same `expect_response`, so the click and the
wait are one atomic step and you never read the new month against the old month's data.

```python
def read_current_month(page):
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.day-cell'))"
        ".every(c => c.querySelector('.price') && c.querySelector('.price').textContent.trim())"
    )
    return page.eval_on_selector_all(
        ".day-cell",
        "cells => cells.map(c => ({"
        "  day: c.getAttribute('data-date'),"
        "  price: c.querySelector('.price') ? c.querySelector('.price').textContent.trim() : null"
        "}))",
    )

def advance_one_month(page, price_call):
    # click "next" and wait for the batch it triggers, as one step
    with page.expect_response(lambda r: price_call in r.url and r.ok):
        page.click("[data-testid='next-month']")
    return read_current_month(page)
```

Note what this is not: it is not `click` then `sleep(2)` then read. A fixed sleep is wrong
in both directions - too short on a slow exit and you read blanks, too long on a fast one
and a twelve-month sweep wastes twenty seconds it did not need. The response is the truth,
so wait on the response.

## Sweep the year on one identity

Now the loop. Collect N months, keep the cheapest cell per month, and let the seed hold the
identity steady for the whole sweep.

```python
from invisible_playwright import InvisiblePlaywright

PRICE_CALL = "/calendar/prices"
MONTHS_TO_SWEEP = 12

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    with page.expect_response(lambda r: PRICE_CALL in r.url and r.ok):
        page.goto("https://example.com/flights/calendar", wait_until="domcontentloaded")

    calendar = [read_current_month(page)]
    for _ in range(MONTHS_TO_SWEEP - 1):
        calendar.append(advance_one_month(page, PRICE_CALL))

    # flatten and find the cheapest priced day across the whole sweep
    days = [d for month in calendar for d in month if d["price"]]
    cheapest = min(days, key=lambda d: float(d["price"].lstrip("$").replace(",", "")))
    print("cheapest day in the sweep:", cheapest)
```

The `seed=42` is doing real work across a twelve-month sweep, and it is the part most
guides skip. A sweep is a dozen or more round trips to the same host in a row. If the
browser's fingerprint - its GPU string, its fonts, its canvas hash, its screen - changed
between month-turns, the host would see one visitor become a new device every few seconds,
which is a stronger signal than any single request carries. A seed-consistent fingerprint
keeps the whole sweep one real browser: the same machine on month one and month twelve. If
a single month-turn comes back with a batch that never fires, treat it like any other
transient and [retry that month rather than restarting the sweep](how-to-retry-failed-requests-playwright.md).

Because the identity is pinned, a sweep that fails on month nine is reproducible: rerun
with the same seed and you get the same machine, so you can tell the site changing from
your browser changing. That is the difference between debugging this and guessing at it.

## The honest caveat: calendar prices are a shortlist, not a quote

This is the calendar grid, and the calendar grid is not the full fare search. The numbers
in the cells are indicative "from" prices for a whole day, computed cheaply so the grid can
render fast. The moment a real user selects a date, the site runs a deeper query - a
hovered or selected cell drives its own request - and the actual, bookable fare for that
date comes back from that second call, often different from the cell.

So treat the calendar output as a shortlist. It is excellent for "which week is cheapest",
which is exactly what a flexible-date search is for, and it is the wrong source for a price
you intend to quote to anyone. Once the sweep hands you the two or three cheapest candidate
dates, drive the selection for each and read the re-quote from that deeper call - and if you
are running through a proxy, make sure the browser's timezone still agrees with the exit so
the re-quote is priced in the right market, which is [its own common mismatch](timezone-proxy-mismatch.md).

## Conclusion

A fare calendar is an async grid wearing a static face. The served HTML has no prices, each
month is a separate fetch, and a screenshot-then-parse approach reads a grid mid-fill. The
method that works is small: wait for the month's price call before reading, wrap each
month-turn's click in that same wait, and sweep the year on one pinned identity so the host
sees one browser instead of a new device every month-turn. Then remember the numbers are a
shortlist, and re-quote the winners.

## Short answers to the questions that lead here

**Why is my scraped fare calendar full of blanks?** You read the grid before its price XHR
came back, or while the cells were still being written. Wait for the response, then wait
until every cell has price text, then read.

**Can I just screenshot the calendar and OCR it?** Not reliably. The screenshot captures
whatever had rendered at that instant, which on a fare calendar is often a half-filled
grid. Read the data after the fetch lands, or read the XHR body directly.

**How do I sweep several months without getting flagged?** Advance one month at a time,
wait for each month's batch, and keep the fingerprint constant across the whole sweep so
the host sees one visitor rather than a new device every few seconds. A pinned seed does
that.

**Should I use a fixed sleep after clicking next month?** No. It is wrong in both
directions - too short and you read blanks, too long and a year-long sweep wastes seconds
per month. Wait on the response instead.

**Are the calendar prices the price I will pay?** No. They are indicative day-level "from"
prices. Selecting a date triggers a deeper query that returns the bookable fare, which can
differ. Use the calendar as a shortlist.

**Why pin a seed for a scrape at all?** So a failing sweep is reproducible. Same seed, same
machine, so you can replay month nine's failure instead of hoping the next random draw
reproduces it.

## Sources

- This project's own release gates and quickstart, for the seed-reproducible fingerprint
  behaviour described above.
- Direct observation of how flexible-date calendars fetch and repaint prices per month,
  read from the network activity rather than the rendered grid.
- Playwright's documented
  [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
  primitive, used above to wait on each month's price call rather than a fixed sleep.

**See also:** [pinning specific fingerprint fields](pinning.md) when you want a fixed screen
or GPU across the sweep rather than a fully seed-derived one, and
[capturing the XHR responses](how-to-capture-xhr-api-responses-playwright.md) for reading
the prices straight from the wire.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed that holds one
machine steady across a twelve-month sweep is the same seed that makes a failed month
replayable.*
