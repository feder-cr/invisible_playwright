---
title: "How to scrape product reviews with Playwright"
description: "Scrape product reviews with Playwright: the reviews load from a separate endpoint behind a tab, so drive the widget, walk its pages, and dedupe on review id."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 38
---


# How to scrape product reviews with Playwright

To scrape product reviews with Playwright, open the reviews widget, confirm a real row
rendered, drive its own sort and pagination, and read each review into plain Python
before turning the page. The product HTML itself carries only a count and a star average,
so every individual review has to come from the separate endpoint the widget renders.

The mistake that wastes the first afternoon is scraping the product page and expecting
the reviews to be in it. They are not. The product HTML almost always holds a count and
an average, and nothing else. The reviews themselves live somewhere the initial markup
does not reach.

This page is about that gap: where the reviews actually come from, how to open their
view and drive its own controls, how to pull each one into plain Python before turning
the page, and the one honest problem you hit on a deep set that no browser fixes for you.

## Why the reviews are not in the product HTML

The reviews are not in the product HTML because they load from a separate widget on its
own endpoint; the initial markup carries only a count and a star average painted from an
aggregate. Open the product page, view source, and search it for the text of a review you
can see rendered. On most storefronts it is not there. What is there is a number, "412
reviews", and that aggregate star value.

The reviews are a separate widget. They sit behind a tab or a "load more" button, they
are paginated on their own endpoint, and they are sortable and filterable by rating. The
star value, the "verified purchase" badge and the date on each one are written by client
JavaScript after that endpoint answers. So a plain HTTP fetch of the product URL gets you
the count and stops, and even a fetch of the review endpoint gets you a payload that
still has to be run through the widget's rendering to become the rows a human sees.

Two consequences follow, and they shape the rest of this page:

- **You need a real browser**, because the review widget is JavaScript that has to
  execute to produce the fields. This is the same reason
  [waiting for the right load signal](how-to-wait-for-page-load-playwright.md) matters
  more here than on a static page.
- **A deep set is many loads from one session.** A product with a few thousand reviews is
  a few thousand rows across tens of pages, fetched sequentially, all from the same
  browser. That is a long, single-origin walk against one endpoint, which is exactly the
  shape that gets a session cut off partway if the machine driving it does not stay
  consistent.

## Open the reviews view first

Before any pagination, get the widget on screen and confirm it rendered. On most layouts
the reviews are behind a tab or a button, and the first content only loads when you
activate it.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/some-item", wait_until="domcontentloaded")

    # The product markup carries only the count. Read it if you want a target,
    # then open the actual reviews view.
    count_text = page.text_content(".reviews-summary .count")
    print("reported reviews:", count_text)

    # Activate the tab or button that mounts the widget.
    page.click("button#reviews-tab")

    # Assert the reviews rendered - an empty container is a failure, not "no reviews".
    page.wait_for_selector(".review-item", state="visible")
```

The `wait_for_selector` on a real review row is the important line. A verdict-style
check ("no error thrown") would pass on a container that never filled. Assert that a
review is actually present, the same principle as
[testing for the signal you want rather than the absence of one you do not](how-to-test-bot-detection.md).

## Drive the widget's own pagination and sort

The reviews endpoint takes its own parameters. It knows how to sort by most recent or by
rating, and how to filter to a single star value. You drive those through the controls the
page already exposes, then walk the pages one at a time.

The pattern is the same shape as [ordinary numbered pagination](how-to-scrape-paginated-pages-playwright.md),
with one difference: the page turn usually swaps the widget's contents in place rather
than navigating, so you wait for the row set to change rather than for a new document.

```python
def extract_visible_reviews(page):
    """Pull each rendered review into plain dicts before turning the page."""
    rows = page.query_selector_all(".review-item")
    out = []
    for row in rows:
        rid = row.get_attribute("data-review-id")
        stars_el = row.query_selector(".stars")
        out.append({
            "id": rid,
            "stars": stars_el.get_attribute("aria-label") if stars_el else None,
            "verified": row.query_selector(".verified-badge") is not None,
            "date": row.get_attribute("data-date"),
            "body": (row.text_content(".review-body") or "").strip()
                    if hasattr(row, "text_content") else
                    (row.query_selector(".review-body").text_content() or "").strip(),
        })
    return out


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/some-item", wait_until="domcontentloaded")
    page.click("button#reviews-tab")
    page.wait_for_selector(".review-item", state="visible")

    # Sort newest-first through the widget's own control.
    page.select_option("select#review-sort", "most-recent")
    page.wait_for_selector(".review-item", state="visible")

    all_reviews = {}
    while True:
        # Snapshot the id of the first row so we can detect the page turn.
        first_before = page.get_attribute(".review-item", "data-review-id")

        for r in extract_visible_reviews(page):
            if r["id"]:
                all_reviews[r["id"]] = r   # dedupe on review id, see below

        next_button = page.query_selector("button.next-reviews:not([disabled])")
        if not next_button:
            break
        next_button.click()

        # Wait for the widget to swap in a different first row, not for a nav.
        page.wait_for_function(
            "(prev) => document.querySelector('.review-item')"
            "?.getAttribute('data-review-id') !== prev",
            arg=first_before,
        )

    print("collected", len(all_reviews), "unique reviews")
```

Everything is read into a Python dict on each page before the turn, keyed by review id.
That keying is not decoration; the next section is why it is load-bearing.

## The honest caveat: sort-by-recent windows shift

Here is the problem no browser solves for you, and pretending otherwise would be
dishonest.

When you sort by most recent and walk a deep set slowly, new reviews keep landing while
you paginate. Each new arrival pushes everything back by one. A review that was the last
row on page 3 when you fetched it can become the first row of page 4 by the time you get
there. So a long walk over a live, recency-sorted list will show you **duplicates**, and
if reviews are removed while you walk it can show you **gaps**.

This is a property of the data moving under you, not a detection event and not a bug in
the browser. The fix is at your end:

- **Dedupe on a stable review id**, which is why the loop above stores into a dict keyed
  by `data-review-id` rather than appending to a list. Duplicates collapse onto the same
  key.
- **Prefer a sort that does not move** when the site offers one. Sorting by rating, or by
  "oldest first", gives a far more stable window than "most recent", because the front of
  the list is not where new rows appear.
- **Accept that a gap is possible** on a very deep, very active product, and if
  completeness matters, do a second pass and merge by id.

A real browser gets you every page the widget will serve. It does not, and cannot, freeze
the ordering of a list that other people are still writing to.

## Why the fingerprint has to stay put for the whole walk

A deep review set is dozens of sequential loads to the same endpoint from the same
session. That is precisely the access pattern where a session that looks like one machine
on request 1 and a subtly different machine on request 40 draws attention, because the
machine is supposed to be a constant and only the page number is supposed to change.

Passing a `seed` pins every fingerprint field for the life of the session: the GPU, the
canvas hash, the audio context, the fonts, the screen. Request 1 and request 40 present
the same machine to the reviews endpoint, so a deep paginate reads as one shopper reading
a lot of reviews rather than a rotating cast that changes identity mid-list.

```python
# One seed, one machine, for the entire pagination run.
with InvisiblePlaywright(seed=42) as browser:
    ...
```

The seed has a second payoff while you build the scraper. Review widgets differ per site
and your selectors will break on some of them. With a fixed seed the failing run is
reproducible: the same seed gives the same browser, so when page 12 throws you can replay
the exact identity and fix the selector instead of guessing whether the site or the
machine changed between runs. That is the same reproducibility argument that makes
[a detection failure a bisect rather than a guess](playwright-detected-as-bot.md).

## Conclusion

Product reviews are their own application bolted onto the product page. Treat them that
way: open their view, confirm a real row rendered, drive the widget's own sort and
pagination, and read each review into plain Python before you turn the page. Dedupe on the
review id because a recency-sorted deep walk will show you the same review twice, and keep
one seeded identity for the whole run so the endpoint sees one machine from the first page
to the last. The browser's job is to run the widget and fetch every page; keeping the
bookkeeping honest is yours.

## Short answers to the questions that lead here

**Why are the reviews not in the product page HTML?** Because they are a separate widget
loaded from its own endpoint. The product markup carries a count and an average; the
individual reviews, their stars, badges and dates are written by client JavaScript after a
separate request.

**Can I just fetch the review endpoint directly?** Sometimes for the raw payload, but the
star value, "verified purchase" flag and date are rendered by the widget's JavaScript, so
you often need a real browser to turn that payload into the fields you see.

**Why do I get the same review twice on a deep scrape?** Because sort-by-most-recent moves
as new reviews land, so rows shift back a page while you walk. Dedupe on the review id, or
sort by rating or oldest-first for a stabler window.

**How do I turn the page when clicking next does not navigate?** The widget swaps its
contents in place. Wait for the first row's id to change rather than for a new document,
as in the loop above.

**Why does a deep paginate get cut off partway?** A long single-origin walk from one
session is a distinctive pattern, and an identity that drifts across those requests draws
attention. A seeded, stable fingerprint keeps the whole walk looking like one machine.

**Do I need a headless browser or is requests enough?** If the fields are rendered by the
widget, a plain HTTP client gets you a count and a payload but not the rendered rows. Run
the widget in a real browser.

## Sources

- Stock Playwright's `wait_for_selector`, `wait_for_function`, `query_selector_all` and
  `select_option`, from the upstream
  [Playwright for Python API](https://playwright.dev/python/docs/api/class-page).
- This project's own measurements of how review widgets load: the product HTML carrying
  only a count while the reviews paginate on a separate, sortable, filterable endpoint
  whose fields are client-rendered.
- Our notes on deep single-session walks and why a seed-stable fingerprint keeps one
  identity across many sequential loads to the same endpoint.

**See also:** [how to scrape reviews and ratings](how-to-scrape-reviews-and-ratings-playwright.md)
for the star-rating and "read more" fields on each row, [how to scrape paginated pages with
Playwright](how-to-scrape-paginated-pages-playwright.md) for the general page-turn pattern,
[how to scrape infinite scroll pages](how-to-scrape-infinite-scroll-playwright.md) for review
sets that grow on scroll instead of paging, and
[how to scrape HTML tables](how-to-scrape-html-tables-playwright.md) once each review is a
row you want to write out.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The duplicate-on-a-live-list
problem above is one I shipped a scraper without handling first.*
