---
title: "How to scrape paginated pages with Playwright"
description: "Scrape numbered and next-page pagination in Playwright without the 'Execution context was destroyed' crash: re-query after every page turn, keep one seed."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 9
---


# How to scrape paginated pages with Playwright

To scrape paginated pages with Playwright without the `Execution context was destroyed`
crash, re-query your element handles after every page turn and never reuse a handle you
took before a navigation. Pagination is a re-query problem, not a loop problem: each page
turn is a navigation, and a navigation destroys the context every handle was bound to.

Most pagination tutorials show you a loop that clicks "next", reads the items, clicks
"next" again, and never mentions the thing that breaks it. Each page turn is a
navigation, and a navigation throws away the execution context that every element handle
and locator result you are still holding was bound to. The loop works on page one,
works on page two, and then raises `Execution context was destroyed` the first time you
reuse a reference from before the click.

This page treats pagination as the error class it actually is: a re-query problem, not a
loop problem. The rule underneath all of it is one line. Never carry an element handle
across a navigation. Re-query after every page turn.

## Why the naive loop crashes on page two

When you evaluate anything against a page, the browser runs it inside an execution
context that belongs to that document. Turn the page and the old document is gone, and
so is its context. Anything still referencing it fails, and the message is the same one
whether the navigation was yours or the site's:

```
Error: Execution context was destroyed, most likely because of a navigation
```

Here is the shape that produces it. The handles are grabbed once, before the loop, and
reused after a click that navigates:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog?page=1")

    rows = page.query_selector_all(".item")     # bound to page 1's context
    next_button = page.query_selector(".next")  # also bound to page 1's context

    while next_button:
        next_button.click()                     # navigates: page 1's context is gone
        for row in rows:                         # rows are stale handles now
            print(row.inner_text())              # Execution context was destroyed
```

`rows` and `next_button` both belong to the document that existed before the click. The
click replaces that document. The next line that touches either handle is talking to a
context that no longer exists. This is the same failure that a redirect chain produces
under a held handle: `goto` returns on the first response, the page then bounces to
another URL, and a reference you took before the bounce is already dead. The full
anatomy is in [the note on when this error means detection](execution-context-destroyed.md).

## Re-query after every page turn

The fix is to fetch nothing before the loop that you intend to use after a navigation.
Turn the page, wait for the turn to complete, then query fresh handles against the new
document:

```python
from invisible_playwright import InvisiblePlaywright

def scrape_all(page):
    items = []
    while True:
        # Query AFTER arriving on this page, never before.
        for row in page.query_selector_all(".item"):
            items.append(row.inner_text())

        next_button = page.query_selector(".next:not([disabled])")
        if not next_button:
            break

        # Wait for the navigation to finish so the next iteration
        # queries the new document, not the old one mid-flight.
        with page.expect_navigation():
            next_button.click()

    return items

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog?page=1")
    data = scrape_all(page)
    print(len(data), "items")
```

Two properties make this correct. Every handle is read at the top of the iteration it is
used in, so no reference ever crosses the `click`. And `expect_navigation` blocks until
the new document has committed, so the next `query_selector_all` runs against the page
you turned to rather than racing the load of it. Locators behave the same way if you
prefer them: a locator is lazy and re-resolves on use, but the values you extract from
one still belong to a moment in time, so pull them inside the loop, not before it.

## Numbered pagination and the URL shortcut

Not every paginated site needs clicking at all. When the page number lives in the URL,
`?page=2`, `/p/3`, an offset parameter, you can drive the crawl with `goto` and skip the
stale-handle problem entirely, because each `goto` is a clean navigation to a fresh
document with nothing carried over:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    items = []

    for n in range(1, 41):                       # pages 1 through 40
        page.goto(f"https://example.com/catalog?page={n}")
        page.wait_for_selector(".item")

        rows = page.query_selector_all(".item")
        if not rows:
            break                                # ran past the last page
        for row in rows:
            items.append(row.inner_text())

    print(len(items), "items across", n, "pages")
```

This is the more robust pattern when the URL exposes the page number, because there is no
"next" button to find, no disabled state to detect, and no way to hold a handle across a
boundary because the boundary is a full `goto`. Guard the end with a real signal, an
empty result set or a "no more results" element, not a fixed page count you guessed. And
`wait_for_selector` before you read, so an empty extraction means an empty page and not a
page you read too early. Reading the log alone hides that difference; a page that came
back empty because it had not loaded yet looks identical to a page that was genuinely
empty until you open the screenshot.

## Keep one identity across all forty pages

Here is the part specific to running a patched browser rather than a stock one, and it is
easy to get wrong precisely because pagination is a long crawl. A forty-page walk against
one site is one visitor reading forty pages. It must look like one visitor.

Every session generated by this library derives its whole fingerprint, GPU, canvas hash,
audio context, fonts, screen, from a seed. Pass a seed explicitly and page 40 reports the
identical machine as page 1:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for n in range(1, 41):
        page.goto(f"https://example.com/catalog?page={n}")
        # same GPU, same canvas hash, same fonts on every one of these pages
```

Because the whole crawl runs inside one `with` block and one browser, the identity is
already stable here. The seed matters for the case you do not see coming. If your crawler
retries a failed page in a new process, or [resumes tomorrow from page 18](how-to-resume-an-interrupted-scrape-playwright.md),
or shards pages across workers, a browser launched with no seed draws a fresh random identity each time,
and now the same logical visitor is reporting a different GPU and a different canvas hash
halfway through the same paginated set. A fingerprint that changes mid-crawl is a signal
in its own right, and a cheap one for a site to check. Passing a fixed seed makes every
process that touches this crawl present the same machine. Log it once and
[any page can be replayed against the exact identity](reproducible-agent-browser-identity-seed.md)
that first fetched it. The reproducibility angle for
debugging is covered in [the checklist for being detected on one site](playwright-detected-as-bot.md);
here the point is continuity, that the visitor on page 40 is the visitor from page 1.

## Rate, order, and the tells a crawl adds

Correct handles get you data. They do not get you an undetected crawl, and pagination
adds a couple of signals that a single page visit does not.

- **Fetch rhythm.** Forty `goto` calls at a perfectly uniform interval is a pattern no
  human produces. The mouse motion this library adds shapes clicks, not the cadence of
  your loop, so vary the spacing between page fetches yourself.
- **Never scrolled, never moved.** A crawl that lands, reads the DOM, and leaves without a
  single pointer movement or scroll is visible to a site that watches behaviour rather
  than fingerprints. This overlaps with infinite scroll, where the scrolling itself is the
  data-loading mechanism; [the infinite-scroll page](how-to-scrape-infinite-scroll-playwright.md)
  covers the scroll pattern that reads as human.
- **Wrong page first.** Jumping straight to `?page=39` with no referrer and no earlier
  pages in the session is not how a person reaches page 39. Walk the sequence, or arrive
  through the same links a reader would.

None of these is about handles or contexts. They are about the crawl looking like a crawl,
and they belong to the broader question of
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md), which
is the order to work the problem in once the extraction itself is solid.

## Conclusion

Pagination is not a loop problem, it is a re-query problem. Each page turn destroys the
execution context that your handles were bound to, so read nothing before a navigation
that you plan to use after it: query fresh inside every iteration, and wait for the turn
to finish before you read the next page. When the page number is in the URL, prefer
`goto` per page and let each navigation give you a clean document. And on a patched
browser, pass a fixed seed so a forty-page crawl is one visitor reading forty pages rather
than forty visitors reading one each.

## Short answers to the questions that lead here

**Why does my Playwright pagination loop crash with "Execution context was destroyed"?**
Because you queried element handles before the loop and reused them after a click that
navigated. The click replaced the document, and the old handles point at a context that
no longer exists. Re-query inside each iteration instead.

**How do I click through numbered pages without the stale-element error?** Read the items
and find the next control at the top of each iteration, click inside
`with page.expect_navigation():`, and let the next iteration query the new document. Carry
no handle across the click.

**Should I click "next" or change the URL?** If the page number is in the URL, use
`page.goto` per page. It is more robust, because each navigation is a clean document and
there is no handle to hold across a boundary and no button state to detect.

**How do I know when to stop paginating?** On a real end signal, an empty item set or a
"no more results" element, not a page count you hardcoded. Guessing the last page number
is how a crawl silently misses the tail.

**Why does my fingerprint change halfway through a crawl?** Because a new browser launched
without a seed draws a fresh random identity, so a retried or resumed page looks like a
different visitor. Pass a fixed seed and every process presents the same machine.

**Does waiting for `networkidle` fix the context error?** It changes when a single
navigation is considered finished, which helps some races. It does nothing for a handle
you carried across a page turn, which is the actual bug here.

## Sources

- This project's note on `Execution context was destroyed`, which separates the ordinary
  navigation race from a site redirecting a session mid-visit, and is the same root cause
  a page turn triggers under a held handle.
- The project quickstart for the real API surface used above: `InvisiblePlaywright`
  returns a stock Playwright `Browser`, and a passed seed makes the fingerprint
  reproducible across runs and processes.

**See also:** [when "Execution context was destroyed" means detection](execution-context-destroyed.md),
[scraping infinite scroll pages](how-to-scrape-infinite-scroll-playwright.md), and
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The re-query-after-every-turn
rule is one I relearned the hard way, one page turn at a time.*
