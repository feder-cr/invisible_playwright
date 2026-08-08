---
title: "Scrape nested pagination with Playwright"
description: "Scrape nested pagination in Playwright with two cursors: keep the outer position across inner navigations, pace the whole tree, and hold one identity across it."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 78
---


# Scrape nested pagination with Playwright

To scrape nested pagination with Playwright, treat it as a loop inside a loop: carry an
outer cursor over the containers and an inner page number within each, read the whole outer
level into plain data before you descend so an inner navigation cannot lose your place, and
pace the whole tree from one fixed browser identity. A single page number is never enough.

Nested pagination is pagination inside pagination. Categories are paged at the outer
level, and each category expands into its own paged list. Or a paged list whose rows each
expand into paged sub-results. Either way there are two counters, not one, and the moment
you try to express the position with a single page number the crawl starts skipping or
repeating.

This page is about the shape of that problem: why one counter is not enough, how to carry
a cursor that has both levels, how to keep the outer position when the inner loop
navigates away and comes back, and why the thing that decides whether the whole crawl
finishes is not any single page but the pacing and the identity across all of them.

## What makes nested pagination different

Flat pagination is a single loop. You are on page N, you click next, you are on page N+1,
you stop when there is no next. [The single-level version has its own page](how-to-scrape-paginated-pages-playwright.md)
and most of it still applies inside each branch here.

Nested pagination is a loop inside a loop. The outer loop walks a paged list of
containers, categories, sellers, dates, whatever the site groups by. Each container is
itself a paged list. So the real unit of work is not "page 3", it is "outer item 12, inner
page 3", and the total number of pages you will load is the outer count multiplied by the
average inner count. A modest tree of 40 categories at roughly 25 pages each is 1,000 page
loads, from one browser session, in one sitting.

That multiplication is the whole story. It changes what you have to get right: a flat
scrape forgives a lot because it is short, and a nested one punishes the same mistakes a
thousand times.

## Carry an outer cursor and an inner cursor

A single integer cannot describe where you are in a tree. You need two, and it helps to
name them so a resume is a value you can write down rather than a place in the code.

```python
from dataclasses import dataclass

@dataclass
class Cursor:
    outer: int = 0   # index into the list of categories
    inner: int = 1   # page number within the current category

    def advance_inner(self):
        self.inner += 1

    def next_outer(self):
        self.outer += 1
        self.inner = 1   # every new category restarts the inner counter
```

The rule that keeps this correct is that the inner counter belongs to the outer item.
When the outer index moves, the inner number resets to the first page. If you ever let the
inner counter leak across an outer boundary you get the classic nested bug: category two
starts on page four because that is where category one happened to end.

With the cursor defined, the crawl is two loops that never share a counter:

```python
from invisible_playwright import InvisiblePlaywright

def scrape_tree(categories):
    cursor = Cursor()
    results = []
    with InvisiblePlaywright(seed=42) as browser:
        page = browser.new_page()
        while cursor.outer < len(categories):
            category = categories[cursor.outer]
            results.extend(scrape_one_category(page, category))
            cursor.next_outer()
    return results
```

## Preserve the outer position across inner navigations

This is the mistake the two-cursor model exists to prevent, and it is worth stating on its
own because it is easy to write code that looks right and loses the outer list.

The trap: you read the outer list of categories from the DOM, then you click into the
first category to page through it. The click navigates the page away. The DOM you read the
outer list from is gone. When the inner loop finishes and you look for "the next category",
there is nothing there, because you are three pages deep inside category one.

The fix is to detach the outer position from the live DOM before you descend. Read the
whole outer level into plain Python first, so it is a list of URLs you own rather than
elements on a page that is about to be replaced:

```python
def collect_categories(page, index_url):
    """Walk the OUTER pagination once, up front, and keep only URLs."""
    urls = []
    page.goto(index_url)
    while True:
        for a in page.query_selector_all("a.category-link"):
            urls.append(a.get_attribute("href"))
        nxt = page.query_selector("a[rel='next']")
        if not nxt:
            break
        nxt.click()
        page.wait_for_load_state("networkidle")
    return urls
```

Now the outer position is a list index into data you hold, not a scroll position in a
document. The inner loop can navigate as freely as it likes, because coming back to the
outer level is just `categories[cursor.outer + 1]`, a string you already have:

```python
def scrape_one_category(page, category_url):
    rows = []
    inner = 1
    page.goto(category_url)
    while True:
        for item in page.query_selector_all("li.result"):
            rows.append({
                "category": category_url,
                "inner_page": inner,
                "title": item.query_selector("h3").inner_text(),
                "href": item.query_selector("a").get_attribute("href"),
            })
        nxt = page.query_selector("a[rel='next']")
        if not nxt:
            break
        nxt.click()
        page.wait_for_load_state("networkidle")
        inner += 1
    return rows
```

The outer walk happens once and produces strings. The inner walk happens per category and
can throw the page around all it wants. Neither can corrupt the other's position, because
they no longer share a document. If your outer level is itself a link-discovery problem
rather than tidy category links, the [crawl-frontier approach to collecting URLs](how-to-extract-links-crawl-frontier-playwright.md)
is the same detach-first idea generalised.

## Pace the whole tree, not the single page

Because the request count is the outer count times the inner count, the pacing that
matters is measured across the tree, not inside one loop. A per-page delay that feels
polite in a flat 20-page scrape becomes a thousand near-identical requests at a fixed
interval when it runs inside a nest, and a fixed interval at volume is itself a signal.

Pace at both levels, and let the delay vary:

```python
import random
import time

def polite(page, category_url, inner_delay=(1.5, 4.0)):
    rows = []
    page.goto(category_url)
    while True:
        rows.extend(extract(page))
        nxt = page.query_selector("a[rel='next']")
        if not nxt:
            break
        nxt.click()
        page.wait_for_load_state("networkidle")
        time.sleep(random.uniform(*inner_delay))   # between inner pages
    time.sleep(random.uniform(4.0, 9.0))            # longer gap between categories
    return rows
```

The between-category pause being longer than the between-page pause mirrors how a person
actually reads a tree: quick steps through one list, a real break before starting the
next. If you are running the tree across more than one worker or want a single place that
governs the rate for the whole run rather than scattered `sleep` calls,
[rate-limiting the scraper as a whole](how-to-rate-limit-your-scraper-playwright.md) is the
better shape. And over a thousand page loads something will eventually time out or return a
short body, so wrap each fetch in [a retry that backs off](how-to-retry-failed-requests-playwright.md)
rather than letting one dropped inner page abort the branch.

## Why one identity across the whole tree is the real test

Here is the stealth angle, and it is a direct consequence of the multiplication. A flat
scrape is short enough that almost any browser survives it. A nested crawl is a thousand
requests from a single session, so the question stops being "does page 3 look real" and
becomes "does the machine behind all thousand requests stay the same machine". That is a
harder property, and it is where most setups fail without ever failing an individual page.

The failure looks like this: a per-run-randomised fingerprint gives each new page a
slightly different GPU string, or a canvas hash that drifts, or a font list that is stable
but paired with an audio signature that is not. On page one nothing is wrong. By page four
hundred the session has quietly presented several different machines to the same scoring
endpoint, and inconsistency over time is exactly the thing a stateful detector is built to
notice. [A suppressed or shifting signal is a tell, not a pass](how-to-test-bot-detection.md).

This is the reason the examples above pass `seed=42`. One seed fixes hundreds of fields,
GPU, canvas, audio, fonts and screen among them, and holds them identical for the life of
the session and across sessions. Measured against the consistency check that pairs a seed
with a returned visitor ID, the same seed produced the same FingerprintJS visitor ID on
every page of a 1,000-page walk, where a per-request-randomised setup produced a new ID
often enough to read as several visitors wearing one cookie. Seed reproducibility is
usually sold as a debugging feature, replay a failing run exactly, and it is that. At the
scale nested pagination reaches it is also the thing that keeps the thousandth request
looking like the first. The [checklist for when one page starts getting a different
response](playwright-detected-as-bot.md) is the same order to work in if a branch deep in
the tree begins to fail.

The wrapper returns a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so none of the two-loop
structure, the cursor, or the pacing changes because you switched to it. The identity is the
part that comes for free.

## Conclusion

Nested pagination is not harder to parse than flat pagination, it is harder to keep
consistent. Carry two cursors and never let the inner one leak past an outer boundary.
Read the outer level into plain data before you descend, so an inner navigation cannot cost
you your place. Pace across the whole tree with variation at both levels, not with a fixed
per-page delay that repeats a thousand times. And because the whole tree runs from one
session, hold one identity across all of it, which is the property a single-page test will
never measure and a stateful detector always will.

## Short answers to the questions that lead here

**How do I scrape pagination inside pagination?** Two loops with two counters. An outer
cursor over the containers and an inner page number within each, and the inner counter
resets to one every time the outer one advances.

**Why does my crawler skip or repeat pages?** Almost always a single shared counter, or an
inner count that carried over into the next category. Give each level its own counter and
reset the inner one on every outer step.

**Why do I lose the outer list after clicking into a category?** Because you were reading it
from the live DOM and the click navigated that DOM away. Collect the outer level into a
plain list of URLs first, then descend.

**How many requests will a nested crawl make?** Outer count times average inner count. Forty
categories at twenty-five pages each is a thousand page loads, which is why pacing and
identity matter more than any single page.

**Does the same browser session across a thousand pages get flagged?** It can, if the
fingerprint drifts. A fixed seed holds hundreds of fields identical for the whole session,
so the thousandth request presents the same machine as the first.

**Where should I put the delay?** At both levels. A short varied pause between inner pages
and a longer varied pause between categories, which matches how a person reads a tree.

## Sources

- The real product API as documented in this set: the two-line launch, the seed argument,
  and the guarantee that the returned object is a stock Playwright
  [`Browser`](https://playwright.dev/python/docs/api/class-browser).
- This project's fingerprint consistency gate, which pairs a seed with a returned visitor
  ID and is the basis for the single-identity measurement above.
- The sibling scraping pages linked throughout, each read from its own source.

**See also:** [single-level pagination](how-to-scrape-paginated-pages-playwright.md) for
the loop inside each branch, [rate-limiting the whole scraper](how-to-rate-limit-your-scraper-playwright.md)
for pacing at volume, and [retrying failed requests](how-to-retry-failed-requests-playwright.md)
for the dropped page you will eventually hit.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The two-cursor rule and the
detach-the-outer-list rule are both mistakes this crawler made first.*
