---
title: "How to scrape business directory listings with Playwright"
description: "Scrape business directory listings with Playwright: drive the search form, reveal click-gated phone and email, and walk the filtered pagination as one visitor."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 41
---


# How to scrape business directory listings with Playwright

To scrape business directory listings with Playwright, drive the search form for each
location-and-category pair, wait for the results to render, un-obfuscate the contact fields,
and follow the site's own next control to walk the filtered pagination to its end. Run the
whole city-by-category matrix under one pinned identity so it reads as a single visitor
rather than a fleet of one-page strangers.

A business directory looks like a flat list and behaves like a nested loop. There is no
page you can request that returns every listing. There is a search form that wants a
location and a category, results that only appear once you have chosen both, contact
fields that are deliberately hard to read, and pagination that lives underneath the
filters you set. Getting the data out means driving all four, in that order, and doing it
without looking like a new device on every category.

This page is the crawl written as what it actually is: a matrix of city by category by
page, driven through a form, with the obfuscated contact fields recovered on the way.

## The shape of the crawl is a nested loop

The single most useful thing to understand before writing any code is that the URL is not
the unit of work. The unit of work is a filter combination.

A directory gates its listings behind a search that takes at least two inputs, a location
and a category, and shows nothing useful until both are set. Under any one combination the
results paginate. So the crawl is three loops deep:

```
for city in cities:
    for category in categories:
        for page_number in pages_under_this_filter:
            extract the listings on this page
```

That structure is why a directory is throttled by identity rather than by request rate
alone. A human browsing the plumbers in one city visits a handful of pages. A full sweep
visits every city crossed with every category, and every one of those is the same visitor
asking a slightly different question. If that visitor's fingerprint changes between the
electricians and the plumbers, the site is not watching one busy person any more, it is
watching a fleet of one-page strangers, which is a far cheaper thing to detect.

So the first design decision is to pin the identity for the whole sweep with a seed, and
only vary the exit address deliberately. A fixed seed also makes the crawl replayable: if
the extractor breaks on one category, you re-run the exact same browser rather than a new
random one and get the same page back. That reproducibility is the whole reason
[this project derives every surface from one seed](quickstart.md).

```python
from invisible_playwright import InvisiblePlaywright

CITIES = ["Springfield", "Rivertown", "Lakeside"]
CATEGORIES = ["plumbers", "electricians", "roofers"]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for city in CITIES:
        for category in CATEGORIES:
            crawl_filter(page, city, category)   # defined below
```

## Drive the search form, do not guess its URL

Drive the form instead of guessing the results URL: fill the location and category fields,
submit, wait for the results container to actually appear, and only then read. That holds up
better because it is what a browser does, not a reconstruction of what one did.

It is tempting to reverse-engineer the results URL and skip the form: notice that a search
lands on `?loc=Springfield&cat=plumbers` and just build that string. Sometimes it works.
Often the site signs the query, sets a cookie during the form submit, or reads a hidden
token that only exists once the page has run its own JavaScript, and the hand-built URL
returns an empty result or a challenge.

```python
def crawl_filter(page, city, category):
    page.goto("https://example.com/search", wait_until="domcontentloaded")

    page.fill("input[name='location']", city)
    page.fill("input[name='category']", category)
    page.click("button[type='submit']")

    # a results container appearing is the signal, not a fixed sleep
    page.wait_for_selector(".results-list .listing", timeout=15000)

    walk_pages(page)   # defined below
```

`wait_for_selector` on a real result element, rather than a timed `sleep`, is what keeps
this correct when the network is slow: you are asserting the presence of the thing you
came for. An empty results container that finished loading is a legitimate answer for some
city-category pairs and your loop should treat it as "zero listings here", not as an error,
so wrap the wait and continue on timeout when a combination genuinely has no businesses.

## Reveal the click-gated contact fields

This is the part that separates a directory from an ordinary list. The contact details are
present in the page but not readable in the naive way, on purpose, and each obfuscation
needs a different move.

**Phone numbers rendered as an image.** There is no text to read; the digits are pixels.
The element is an `<img>`, and what you can extract is its `src`, which is either a URL to
fetch or a `data:` URI you decode. Turning those pixels back into digits is an
optical-character step outside the browser, so what the crawl records here is the image
reference, handed to an OCR stage downstream.

```python
img = listing.query_selector(".phone img")
phone_image_src = img.get_attribute("src") if img else None
```

**Phone numbers that are entity-encoded.** Here the digits are real text, written as HTML
entities like `&#52;&#49;&#53;` so a crude byte-level scrape of the raw HTML sees gibberish.
The fix is to read the rendered text rather than the source: the browser has already
decoded the entities, and `inner_text()` gives you the digits a human sees.

```python
phone_text = listing.query_selector(".phone").inner_text().strip()
```

**Emails revealed only after a click.** The address is not in the DOM until you click a
"show email" control, at which point the site fetches or unmasks it. You have to perform a
real click and then wait for the revealed value.

```python
listing.query_selector(".reveal-email").click()
page.wait_for_selector(".email-value", timeout=5000)
email = listing.query_selector(".email-value").inner_text().strip()
```

The click matters more than it looks. A reveal control frequently checks that the click was
user-generated before it hands over the address, and a synthetic event that does not carry
that trust gets ignored while your code waits for a value that never appears. Because this
engine dispatches input through the real browser rather than injecting DOM events, the
reveal fires the same way it does for a person. That specific failure, a click that the
page refuses to trust, has [its own page on why `isTrusted` is the thing that
matters](playwright-clicks-istrusted.md).

**Addresses split across spans.** The street, city and postal code are placed in separate
elements, sometimes in a shuffled visual order fixed up by CSS, so a single selector never
returns the whole thing. Collect the parts and join them, and if the order is set by CSS
rather than DOM order you may need to read the layout, not the markup.

```python
parts = [s.inner_text().strip() for s in listing.query_selector_all(".addr span")]
address = " ".join(p for p in parts if p)
```

## Walk the filtered pagination

Pagination on a directory is stateful: page 2 means "page 2 of the results for this city
and this category", and the filter is held in a cookie, a query parameter, or server-side
session. If you navigate to a bare `?page=2` without the filter in scope you can land on
page 2 of everything, or nothing.

Follow the site's own next control instead, and stop on a real end condition rather than a
guessed page count. A directory rarely tells you the total up front, so the reliable
terminator is "the next control is gone or disabled".

```python
def walk_pages(page):
    while True:
        for listing in page.query_selector_all(".results-list .listing"):
            record(extract_contact(listing, page))   # your extractor + storage

        next_btn = page.query_selector("a.next:not([disabled])")
        if not next_btn:
            break
        next_btn.click()
        page.wait_for_selector(".results-list .listing", timeout=15000)
```

The general mechanics of not-losing and not-duplicating rows across pages, and why a
scroll-loaded list needs a different terminator than a numbered one, are covered in
[the pagination guide](how-to-scrape-paginated-pages-playwright.md). The point specific to
directories is that the loop lives strictly inside the two filter loops above it; a next
click is only meaningful while the search that produced the list is still the active one.

## Keep the sweep coherent, and know what that does not buy you

A stable fingerprint solves the coherence problem, not the volume problem, and keeping the
two apart is the honest division of labour most guides skip.

Across the whole city-by-category matrix a stable fingerprint presents as one consistent
device: the same GPU, the same fonts, the same screen, the same audio stack, session after
session, because they all come from one seed. The site sees a single visitor doing a lot of
browsing, which is an ordinary thing, instead of a thousand devices each doing one search,
which is not. That is real and it is worth having,
and it is also why the reveal clicks land: a coherent, genuinely-driven browser is what a
trusted event requires.

What the fingerprint does nothing about is volume. A full directory sweep is inherently a
lot of requests from one identity, and a coherent identity making ten thousand searches is
still ten thousand searches. The fingerprint keeps you from looking like many suspicious
devices; it cannot keep you from looking like one very busy one. Two things have to come
from outside the browser:

- **Pacing.** Space the filter combinations out. The velocity of a request stream is
  measured independently of anything in the page, and hammering the search endpoint is a
  signal you create no matter how real each individual request looks. We have tripped this
  on our own test harness.
- **Proxy spread with a matching timezone.** Spread the exits so the volume is not all from
  one address, and keep each exit's location consistent with the browser it drives, because
  a mismatch between the two is its own tell. What has to agree, and how the browser
  timezone is derived from the exit, is in [configuration](configuration.md) and
  [the timezone-proxy page](timezone-proxy-mismatch.md).

```python
import random, time

with InvisiblePlaywright(seed=42, proxy=proxy, timezone="auto") as browser:
    page = browser.new_page()
    for city in CITIES:
        for category in CATEGORIES:
            crawl_filter(page, city, category)
            time.sleep(random.uniform(20, 45))   # pace the matrix, do not sprint it
```

The split is worth stating plainly: the browser makes each request look like a person, and
your loop's rhythm decides whether the sequence of requests does. Neither substitutes for
the other.

## Conclusion

A directory crawl is a form you drive, contact fields you un-obfuscate one technique at a
time, and a filtered pagination you walk to its real end, all under a single pinned
identity so the whole matrix reads as one visitor. The fingerprint keeps the sweep coherent
and makes the reveal clicks trustworthy; the pacing and the proxy spread keep the sheer
volume from undoing that. Build it as three honest loops with a seed on the outside and a
sleep on the inside, and the hard parts become mechanical.

## Short answers to the questions that lead here

**Why does the directory show nothing until I search?** Because listings are gated behind a
location-plus-category form, and the results only exist once both filters are set. Drive
the form, wait for a real result element, then read.

**How do I get a phone number that is rendered as an image?** You cannot read it as text
from the DOM. Extract the image `src` or `data:` URI and run an optical-character step
outside the browser. If instead the digits are HTML entities, read `inner_text()`, which is
already decoded.

**The email only appears after clicking, and my click does nothing.** The reveal control is
checking that the click was user-generated. A real browser dispatching a trusted event
gets the address; a synthetic DOM event is ignored. See the note on `isTrusted`.

**How do I paginate without losing the filter?** Follow the site's own next control instead
of building `?page=N` by hand, and keep the pagination loop strictly inside the city and
category loops so the active search is never lost.

**Does a stable fingerprint let me crawl the whole directory safely?** It makes the whole
sweep look like one consistent visitor, which is the coherence problem solved. It does
nothing about volume: pacing and proxy spread still have to come from your loop.

**How do I make the crawl reproducible when it breaks on one category?** Pass a fixed
`seed`. The same seed gives the same browser every run, so you replay the exact failing
session instead of hoping a new random identity reproduces it.

## Sources

- This project's own API for launching a seed-reproducible browser and driving stock
  Playwright, as documented on the quickstart and configuration pages.
- Playwright documentation, [Auto-waiting](https://playwright.dev/python/docs/actionability),
  retrieved 2026-08-28, for the actionability checks a real click and a `wait_for_selector`
  call both rely on.
- The behaviour of trusted versus synthetic input events, from this project's notes on why
  a reveal click needs `isTrusted`.
- The release gate that flagged our own harness for request velocity, which is where the
  pacing caveat comes from.

**See also:** [walking paginated result sets](how-to-scrape-paginated-pages-playwright.md),
[scraping content that changes by location](how-to-scrape-geotargeted-content-playwright.md),
and [why a click needs to be trusted](playwright-clicks-istrusted.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The nested-loop shape and the
honest split between fingerprint and volume are both mistakes I made before I wrote them
down.*
