---
title: "How to scrape HTML tables with Playwright"
description: "Scrape HTML tables with Playwright: pull the whole table in one evaluate_all call, then extract before you navigate so a multi-page table never drops rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 20
---


# How to scrape HTML tables with Playwright

To scrape an HTML table with Playwright, read the whole table into plain Python in one
call with `locator.evaluate_all` or `page.evaluate`, and do it before you navigate to the
next page. That single ordering is the difference between a scrape that comes back whole
and one that silently loses rows. A table is the easiest thing on a page to scrape and the
easiest thing to scrape wrongly: the naive loop works on a single page and then quietly
drops rows the moment the table spans more than one, and the reason is not the extraction
code at all, it is that the thing you extracted into stopped existing when you turned the
page.

This page is the extraction recipe that pulls a whole table into Python in one call, the
reason that recipe adds nothing a detector can see, and the one navigation gotcha that
turns a multi-page table scrape into a silent data-loss bug.

All examples use the real API. `InvisiblePlaywright` returns a stock Playwright
`Browser`, so every method below - `locator`, `evaluate_all`, `evaluate`,
`expect_navigation` - is the upstream Playwright method, behaving exactly as documented.

## The cell-by-cell loop, and why it is the slow way

The first thing everyone writes walks the rows and reads each cell:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")

    rows = page.locator("table tbody tr")
    data = []
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")
        data.append([cells.nth(j).inner_text() for j in range(cells.count())])

    print(data)
```

This is correct and it works. It is also one round trip between Python and the browser
for every single cell, so a two hundred row table is a few thousand messages across the
wire. On a small table you will not notice. On a large one, or across many pages, it is
most of your runtime.

The fix is to do the walking inside the page and hand back the finished result once.

## Pull the whole table in one call

[`locator.evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all)
runs a single JavaScript function over every element the locator matched and returns the
result to Python in one message. That is the whole table in one call:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")

    table = page.locator("table tbody tr").evaluate_all(
        "rows => rows.map(r => "
        "Array.from(r.querySelectorAll('td, th'), c => c.innerText.trim()))"
    )

    print(table)   # list[list[str]], already plain Python
```

If you want the header separately, or you want to key each row by its column name,
[`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate) gives
you the same one-message round trip with room for a little more logic:

```python
    table = page.evaluate("""() => {
        const headers = Array.from(
            document.querySelectorAll('table thead th'),
            th => th.innerText.trim()
        );
        const rows = Array.from(
            document.querySelectorAll('table tbody tr'),
            tr => Array.from(tr.querySelectorAll('td'), td => td.innerText.trim())
        );
        return { headers, rows };
    }""")

    records = [dict(zip(table["headers"], row)) for row in table["rows"]]
```

Either way the crossing happens once and what comes back is ordinary Python: strings,
lists, dicts. That last part matters more than it looks, and the rest of this page is why.

Which call to reach for depends on the shape you want back and on whether the table needs
an interaction to appear at all:

| Method | Round trips | Reach for it when |
|---|---|---|
| Cell-by-cell `locator` loop | one per cell | a handful of cells; never a large table |
| `locator.evaluate_all` over the rows | one | you want the rows as a list of lists |
| `page.evaluate` over the whole table | one | you want the header separately, or rows keyed by column name |
| `pandas.read_html` | none (parses static HTML) | a static page that needs no click, wait, login or pagination |

## Why reading the DOM this way adds no automation surface

There is a reasonable worry here: does running JavaScript in the page to read it make the
session look more automated? For this class of read, no, and the reason is where the code
runs.

`page.evaluate` and `locator.evaluate_all` execute inside the page's own execution
context, against the same document the site's own scripts see, with the native DOM
getters intact. Calling `innerText` or `querySelectorAll` is exactly what the page's own
code does thousands of times a load. There is no injected global, no overridden
prototype, no wrapper for a script to trip over. Reading a table is indistinguishable
from the page reading itself.

That is a property of the engine, not of how carefully you write the selector. Because
the fingerprint is applied in the browser at the C++ level rather than by a script
injected into the page, the automation channel and the page are separate, and a
[patched engine and a page-level stealth plugin do not contradict each other](playwright-detected-as-bot.md)
the way two script-based layers can. The read stays quiet because there is nothing
page-side doing the reading.

The behavioural surface - pointer motion, timing - is a different question and not one
that a table read touches; that lives with
[how the cursor moves between clicks](human-mouse-movement.md), not with how you pull
text out of the DOM.

## The gotcha: a handle dies when the page navigates

An element handle - or anything you got from `query_selector` - is bound to the document
it came from, and navigating away destroys that document, which is the bug that costs
people a day. Navigate - a click that loads a new page, a "next" link, a form submit - and
the document is gone, and every handle into it is now pointing at nothing:

```python
    handle = page.query_selector("table")   # bound to THIS document
    page.click("a[rel=next]")                # navigates to page 2
    handle.inner_text()                      # Execution context was destroyed
```

The error reads `Execution context was destroyed, most likely because of a navigation`,
and it is accurate: the context the handle belonged to no longer exists. It is one of the
most searched Playwright errors, and
[it has an ordinary cause and a detection cause that look identical](execution-context-destroyed.md);
this one is the ordinary cause, and it is entirely yours to avoid.

The rule that avoids it: extract the table into plain Python **before** you navigate, not
after. A `list` of `str` is not bound to any document. Once the data has crossed back out
of the page it survives every page turn you make afterwards, because it is no longer
living in the browser at all.

## Extract before you paginate, not after

Put the two halves together and a multi-page table scrape becomes: read this page fully,
append the plain-Python result, then and only then turn the page.

```python
all_rows = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report?page=1")

    while True:
        # read the whole current table into plain Python FIRST
        page_rows = page.locator("table tbody tr").evaluate_all(
            "rows => rows.map(r => "
            "Array.from(r.querySelectorAll('td'), c => c.innerText.trim()))"
        )
        all_rows.extend(page_rows)   # now safe across any navigation

        # only now look for the next page and turn it
        next_link = page.locator("a[rel='next']")
        if next_link.count() == 0:
            break
        with page.expect_navigation():
            next_link.click()

print(len(all_rows), "rows collected across all pages")
```

The ordering is the whole point. If you carried a handle or a locator result across the
`click`, the row it referenced would be dead on the far side and you would either crash or
- worse, because it is silent - read a re-evaluated locator against the wrong document and
lose the rows you thought you had. Extracting first means the click never touches anything
you still need. The same discipline governs
[numbered and next-page pagination in general](how-to-scrape-paginated-pages-playwright.md):
own the data before you turn the page.

## Make a failed crawl reproducible

Large table scrapes fail partway. Page 7 of 40 returns a challenge, or the selector
misses because that one page nests its table differently, and you are left guessing
whether the site changed or your machine did.

That guess is what the `seed` argument removes. Every field the browser presents - GPU,
canvas, fonts, screen, the roughly four hundred that make up the fingerprint - is derived
from that one number, so the same seed is the same machine, byte for byte, run after run:

```python
with InvisiblePlaywright(seed=42) as browser:
    ...   # identical fingerprint every single run
```

Re-run the failed crawl with the seed that produced it and you are debugging the same
browser that failed, not a fresh random one that might not reproduce the problem at all. A
bisect stays a bisect. That is a measured property, not a claim: pass the same seed twice
and the canvas hash, the WebGL renderer and the audio fingerprint come back identical each
time, which is [the reproducibility the quickstart demonstrates](quickstart.md).

## Conclusion

Scraping a table well is two habits. Pull the whole table in one call with `evaluate_all`
or `page.evaluate` instead of a round trip per cell, because that read runs in the page's
own context with native getters and adds nothing a detector sees. And extract into plain
Python before you navigate, never after, because a handle held across a page turn is
already dead and the failure is silent when it is not loud. Get those two right and a
forty-page table comes back whole and a failing run replays exactly.

When the table is a schedule rather than a grid, the row is not the visible line: see
[how to scrape course catalogs with Playwright](how-to-scrape-course-catalogs-playwright.md).

## Short answers to the questions that lead here

**What is the fastest way to scrape a table with Playwright?** `locator.evaluate_all` over
the rows, or `page.evaluate` over the whole table. Both do the walking inside the page and
return the finished result in one message, instead of one round trip per cell.

**Why does my multi-page table scraper lose rows or crash on page 2?** You are holding an
element handle or a locator result across a navigation. It dies when the page changes.
Extract the table into a plain Python list before you click "next", not after.

**What causes "Execution context was destroyed" when scraping a table?** Reading a handle
after the document it belonged to navigated away. Read the data out first, then navigate.

**Does running JavaScript in the page to read a table make me look automated?**
Not for a DOM read. `page.evaluate` runs in the page's own context with the native getters
intact, which is exactly what the site's own scripts do.

**Should I use pandas.read_html instead?** For a static single page it is fine. It cannot
click, wait, log in or paginate, so once the table needs an interaction to appear you are
back to driving a browser and reading it with `evaluate_all`.

**How do I get column headers as dict keys?** Read `thead th` and `tbody tr td` in the
same `page.evaluate`, return both, then `zip` the header row against each data row in
Python.

## Sources

- The upstream Playwright locator and page API:
  [`locator.evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all),
  [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate),
  `page.expect_navigation`, `query_selector`, read from their own documentation rather
  than from a rendered example.
- This project's notes on the execution-context error and on where in-page evaluation
  runs, linked throughout.

**See also:** [scraping numbered and next-page pagination](how-to-scrape-paginated-pages-playwright.md)
for the general page-turn pattern,
[the execution-context-destroyed page](execution-context-destroyed.md) for the case where
the same error means the site moved you rather than your own code racing, and
[scraping into a pandas DataFrame](how-to-scrape-into-a-pandas-dataframe-playwright.md)
for the typed-output step once the table is out as plain Python.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The extract-before-you-
paginate rule is a mistake I shipped before I documented it.*
