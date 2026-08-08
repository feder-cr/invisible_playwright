---
title: "How to scrape flight prices with Playwright"
description: "Scrape flight prices with Playwright by waiting for the search's results-complete signal instead of networkidle, then reading the settled fare matrix."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 30
---


# How to scrape flight prices with Playwright

To scrape flight prices with Playwright, wait for the search to report itself complete
rather than for the page to load or the network to go idle, then read the settled fare
matrix in one pass. Because a fare scan is many searches, run every one of them as the
same seeded browser, so the endpoint feeding the fares sees one consistent device rather
than a new machine each time.

A flight search is the worst possible fit for the way most people write a scraper. You
submit an origin, a destination and a pair of dates, the navigation settles, and the
page is empty. Nothing you wanted is in the DOM yet. The fares are still arriving.

This page is about that gap: why the results stream in after the page has finished
loading, how to detect the moment they are actually complete rather than the moment the
network went briefly quiet, and how to run a whole scan of dates without every search
looking like a different device to the endpoint that is feeding you the fares.

## Why the results are not there when the page finishes loading

A flight search does not compute a result and hand it back. It opens a search, then
polls or holds a websocket while providers report back one at a time. The first fares
appear within a second or two, more fill in over the next several seconds, and existing
fares update in place as cheaper itineraries arrive. A matrix of fares by departure time
and number of stops fills progressively, cell by cell, and often keeps changing after it
first looks full.

That means the two waits everyone reaches for are both wrong here:

- **[`wait_until="load"`](https://playwright.dev/python/docs/api/class-page#page-goto)**
  fires when the document and its subresources are done. The search has barely started.
  You capture an empty shell.
- **[`wait_until="networkidle"`](https://playwright.dev/python/docs/api/class-page#page-goto)**
  fires after a short window with no network activity. On a page that polls, there are
  natural lulls between poll responses, so networkidle triggers in a gap and you capture
  a matrix that is a third full. It is the more dangerous of the two precisely because it
  sometimes returns something, so it looks like it worked.

The signal you want is the search reporting itself complete: the poll responses settling
to a terminal state, a final XHR that carries a "done" or "no more providers" flag, or
the websocket going quiet after a completion message. You have to wait on that, not on
the transport.

There is a general version of this problem, and the difference between the two waits is
worth understanding on its own terms before you rely on either: see
[how to wait for a page to actually finish loading](how-to-wait-for-page-load-playwright.md).

## Detecting the results-complete signal instead of networkidle

The reliable approach is to watch the responses the search itself produces and wait for
the one that says it is finished. Capturing those responses is the same technique used
for any streamed API, covered in
[how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md);
here you use it to find the terminal message rather than to read the data.

```python
from invisible_playwright import InvisiblePlaywright

def scrape_fares(origin, destination, depart, ret, seed=42):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()

        # Collect the search responses as they stream in.
        completion = {"done": False, "polls": 0}

        def on_response(response):
            url = response.url
            if "/search" not in url and "/poll" not in url:
                return
            completion["polls"] += 1
            try:
                body = response.json()
            except Exception:
                return
            # The search tells you when it has heard from every provider.
            # The exact field name is site-specific; inspect one response
            # to find it. Common shapes: {"status": "complete"} or a
            # remaining-provider count that reaches zero.
            if body.get("status") == "complete" or body.get("providers_pending") == 0:
                completion["done"] = True

        page.on("response", on_response)

        url = (
            "https://example.com/search"
            f"?from={origin}&to={destination}&out={depart}&back={ret}"
        )
        page.goto(url, wait_until="load")

        # Wait on the completion flag the responses set, not on the transport
        # going quiet. Poll our own flag with a hard ceiling so a search that
        # never reports complete cannot hang the run.
        deadline, step, waited = 45000, 500, 0
        while not completion["done"] and waited < deadline:
            page.wait_for_timeout(step)
            waited += step

        return read_matrix(page)
```

The loop above is deliberately explicit so the exit conditions are visible: it ends
either when the search reports complete or when a hard ceiling is reached, and it never
depends on the network being idle. In real code you would fold the wait into a single
`wait_for_function` against a page flag if the site exposes one, or wait directly on the
terminal response with the predicate shown in
[how to wait for a specific API response](wait-for-specific-api-response-playwright.md);
the response-driven version above works even when the page keeps its state private.

One caveat that trips people up: the matrix can look full and still be settling, because
a late provider can undercut a fare that already rendered. If the last few poll responses
still carry price changes, you are early. Waiting for the completion flag, not for the
grid to stop looking empty, is what removes that error.

## Reading the fare matrix once it is settled

With the search complete, the matrix is ordinary DOM and you read it like any table.
Read the whole grid in one pass in the page context so you are not making a round trip
per cell:

```python
def read_matrix(page):
    return page.evaluate(
        """() => {
            const cells = Array.from(document.querySelectorAll("[data-fare-cell]"));
            return cells.map(c => ({
                depart: c.getAttribute("data-depart"),
                stops: Number(c.getAttribute("data-stops")),
                price: Number(c.getAttribute("data-price")),
                currency: c.getAttribute("data-currency"),
            })).filter(r => Number.isFinite(r.price));
        }"""
    )
```

The `filter` on a finite price matters: a cell that has not received a fare yet often
renders a placeholder, and if you read too early you will silently collect rows with
`NaN` prices that look like real data downstream. An empty result from this function is
not "no flights"; it is "you read before the search was done", which is the same failure
as waiting on load, one layer up.

## Running a whole fare scan without looking like a new device each time

Pin one seed for the entire scan and every search derives the same browser identity, so
the whole run looks like one consistent device rather than a new machine on each request.
A single search is rarely the goal: you want a fortnight of departure dates, or the same
route priced every morning, which is dozens or hundreds of searches. This is where the
browser you run the scan with starts to matter as much as the waits.

The streaming endpoint that feeds you fares sees every search you make. If each search
arrives from what looks like a different machine - a different GPU string, a different
canvas hash, a different font list, a different screen - that is a pattern a real user
never produces, and a search that looks like a fresh unfamiliar device is one a
provider-aggregating backend can reasonably choose to deprioritise or serve more slowly.
The fix is not to hide; it is to be the *same* real browser on every search of the scan.

That is what a seed gives you. `InvisiblePlaywright(seed=42)` derives hundreds of
fingerprint fields - GPU, audio, fonts, screen, canvas - from one number, and the same
seed produces the same fields every run. So a scan that reuses one seed is one consistent
Firefox making many searches, which is what a person doing a fare hunt actually looks
like:

```python
import datetime

def scan_route(origin, destination, days=14, seed=42):
    today = datetime.date.today()
    results = {}
    for offset in range(days):
        depart = (today + datetime.timedelta(days=offset)).isoformat()
        ret = (today + datetime.timedelta(days=offset + 7)).isoformat()
        results[depart] = scrape_fares(origin, destination, depart, ret, seed=seed)
    return results
```

Note that `scan_route` opens a fresh browser per search through `scrape_fares`, but pins
the *same* seed, so every one of them is the identical device rather than a new one each
time. If you would rather keep one browser open across the whole scan, hold a single
`with InvisiblePlaywright(seed=seed) as browser:` around the loop and open a new page per
search; the seed does the same job either way.

If the site presents its dates as a single flexible-date calendar grid rather than one
search per date, the read order changes - each month's cells arrive in their own fetch -
and [how to scrape flexible-date fare calendars](how-to-scrape-fare-calendars-playwright.md)
covers that variant with the same one-seed sweep.

If your scan runs long enough that a single exit address becomes the pattern instead,
that is a separate axis from the fingerprint, and
[how to rotate proxies](how-to-rotate-proxies-playwright.md) covers keeping the address
varied without letting the browser identity drift with it.

## The honest caveat: the same search gives different prices

Reproducibility here is a property of the browser, never of the price.

If you run the same route on the same dates twice and diff the two results, the fares
will not match, and that is not a bug in your scraper or in the fingerprint. Fares
genuinely fluctuate: inventory sells, providers re-price, a caching layer expires between
your two searches. A seed guarantees the same GPU and the same canvas hash on both runs.
It guarantees nothing about the number in the cell, because that number is the airline's,
not yours.

This matters for how you validate the scraper. Do not assert that two runs produce equal
prices; they will not, and a test that expects them to will be red for a reason that has
nothing to do with your code. Assert instead that the structure is stable - the same
routes, the same grid shape, well-formed finite prices - and treat the prices themselves
as a moving quantity you are sampling, not a fixed value you are fetching. When a search
does fail transiently, retry it as a sample rather than trusting one attempt; the pattern
is in [how to retry failed requests](how-to-retry-failed-requests-playwright.md).

## Conclusion

Flight scraping breaks the assumption a scraper is usually built on: that the data is in
the page when the page has loaded. It is not. The results stream in over several seconds,
so you wait on the search reporting complete - the poll count settling, the terminal XHR,
the websocket going quiet - and only then read the matrix, filtering the cells that have
not received a fare yet. Because a fare scan is many searches, run them as one consistent
browser rather than a new device each time, which a seed makes automatic. And keep one
thing straight: the browser is reproducible, the price is not, so a re-run diff on the
fares is data, not a defect.

## Short answers to the questions that lead here

**Why is the results table empty right after the page loads?** Because a flight search
returns nothing synchronously. Providers report back over several seconds via polling or
a websocket, and the page has finished loading long before they are done.

**Why does networkidle capture a half-empty result?** Because a polling page has natural
quiet windows between responses, and networkidle fires in one of them. You get a matrix
that is partly filled and looks complete.

**What should I wait for instead?** The search's own completion signal: the poll
responses reaching a terminal state, a final XHR carrying a done flag, or the websocket
going quiet after a completion message. Wait on that, not on the transport.

**Why do I get different prices when I re-run the exact same search?** Because fares
genuinely change between searches as inventory sells and providers re-price. A seed makes
the browser reproducible; it does not, and cannot, make the price reproducible.

**Does the fingerprint affect the fares I see?** Not the fares themselves, but a search
that looks like a brand-new unfamiliar device on every request is a pattern a
provider-aggregating backend can deprioritise. A seed keeps every search the same real
browser.

**How do I run a hundred searches without looking like a hundred machines?** Pin one seed
for the whole scan. Every search then derives the same GPU, fonts, screen and canvas
hash, so the scan is one consistent Firefox rather than a new device each time.

## Sources

- This project's own measurements of streamed-search behaviour: fares arriving over
  several seconds after load, a fare-by-time-and-stops matrix filling progressively, and
  late providers re-pricing cells that had already rendered.
- The wait-strategy comparison behind the load-versus-networkidle distinction, drawn from
  the page-load notes in this set.
- Playwright's own documentation of the `wait_until` navigation options (`load`,
  `networkidle`): https://playwright.dev/python/docs/api/class-page#page-goto

**See also:** [how to wait for a page to actually finish loading](how-to-wait-for-page-load-playwright.md)
for the general form of the streaming-results problem, and
[how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the stream directly instead of the rendered grid.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-table-on-load
mistake, and the networkidle capture that looked complete and was not, are both ones made
here first.*
