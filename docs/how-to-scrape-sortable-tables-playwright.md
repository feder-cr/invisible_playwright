---
title: "How to scrape sortable data tables with Playwright"
description: "Scrape sortable data tables with Playwright: read rows once keyed on an identifier, trust aria-sort over a click count, and sort the data yourself instead of the page's own string comparator."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 137
---


# How to scrape sortable data tables with Playwright

To scrape a sortable data table with Playwright, read the rows once in their default
order keyed on a stable row identifier, treat the `aria-sort` attribute (never a count
of clicks) as the only trustworthy signal of the current sort state, and sort the
extracted data yourself in Python rather than trust the page's own comparator, which is
often a plain string compare that gets numeric columns wrong while you watch.

Clicking a header feels like a request for new data. Most of the time it is a request to
repaint rows you already have, in a different order, and every assumption that depends
on order being stable breaks the moment you click. A sortable table looks like the
simplest object on a page: rows, columns, a header you can click. Underneath, that click
touches three things a naive scraper gets wrong at once. It changes which row sits at
which index without changing which rows exist. It runs a comparator you did not write
and cannot audit, and that comparator frequently treats "1,200" as smaller than "99"
because it is comparing text. It can also add a second sort key that no single attribute
on the page reports, and it can reset a paginated list back to page one as a side effect
nobody warned you about. None of this shows up in a quick look at the DOM. It shows up as
data that is subtly wrong, weeks later, in whatever pipeline consumes the scrape.

## A header click reorders rows, it rarely fetches a new dataset

Clicking a sortable column header usually triggers a client-side re-render: the same row
set, repainted in a different sequence. The set of rows the table holds does not change.
The mapping from a row to its position on screen does, and that is the part a scraper
built around row index quietly gets wrong.

The failure is easy to write by accident. Read the table before a sort, remember that
"row 3 is the Acme Corp listing," click a header to sort by price, then read "row 3"
again expecting the same listing with a fresh price attached to it. Row 3 is now
whatever the new order put there. Nothing crashes. The scrape returns a full table with
every price correctly read and every price attached to the wrong name, because the code
paired data by position across two states that do not share a position mapping.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/inventory", wait_until="networkidle")

    def read_rows():
        # Read the identifier alongside every value, never the value alone.
        # data-row-id is one common anchor; a product SKU or a stable href works too.
        return page.locator("table tbody tr").evaluate_all(
            """rows => rows.map(r => ({
                id: r.dataset.rowId,
                name: r.querySelector('.name')?.innerText.trim(),
                price: r.querySelector('.price')?.innerText.trim(),
            }))"""
        )

    before = read_rows()
    page.get_by_role("columnheader", name="Price").click()
    page.wait_for_timeout(300)   # let the client-side re-render settle
    after = read_rows()

    by_id = {r["id"]: r for r in before}
    # comparing by id, not by index, is what makes this comparison mean anything
    reordered = [r["id"] for r in after] != [r["id"] for r in before]
```

`reordered` tells you the click changed order. It says nothing about whether new rows
arrived, and on a client-side sort they never do: `len(before) == len(after)` on every
table of this kind, because the round trip never left the browser.

## String comparison is the default, and it gets numbers wrong in front of you

A client-side sort usually compares the rendered text of a cell, not the number it
represents. That is fine for names and broken for anything formatted: "1,200" sorts
before "99" because the comparison never parses a number, it walks characters, and the
character "1" is smaller than the character "9." A currency symbol makes it worse,
because "$1,200" and "$99" now differ in their first two characters before the digits
even matter.

The table looks sorted. It reads top to bottom in an order a human skimming it would
accept as plausible, right up until they check the actual values. If you scrape the
rendered order uncritically, you inherit that lexical order as if it were numeric,
and nothing in the response tells you it happened; the header even shows the ascending
arrow, honestly reporting that the page did what it was asked.

The fix is not a smarter locator. It is to never depend on the page's sort for numeric
correctness at all, which a later section covers directly. Before that, it helps to know
which kind of sort you are looking at, because a server-side sort does not have this
failure mode; it is applied on a real backend that almost always sorts on the typed
column, not the formatted string.

## Telling a client-side sort from a server-side one

A page sorts a table one of two ways, and the difference matters for exactly the failure
above. Client-side sorting reruns a JavaScript comparator against rows already sitting in
the browser, which is where the string-versus-number problem lives. Server-side sorting
sends a request with a sort parameter and gets back rows the backend already ordered,
typically on the real, typed column instead of its formatted display text.

You can tell which one you are looking at by watching the network, not by reading the
markup: a client-side sort produces no request at all, only a repaint, while a
server-side sort fires a request you can catch mid-click.

```python
from playwright.sync_api import TimeoutError as PlaywrightTimeout

with page.expect_response(lambda r: r.request.method == "GET", timeout=2000) as caught:
    try:
        page.get_by_role("columnheader", name="Price").click()
        response = caught.value
        server_side = "sort" in response.url or "order" in response.url
    except PlaywrightTimeout:   # playwright.sync_api.TimeoutError, not the builtin
        server_side = False   # no matching request fired: this is a client-side sort
```

A client-side table gives you a clean signal for free: no network request follows the
click, only a repaint. A server-side table fires a request carrying a sort parameter,
and that request is worth capturing directly instead of reading the table it produces,
the same way [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
recommends for any repainted list.

## aria-sort and the arrow icon are the only state worth trusting

Do not track sort direction by counting your own clicks. A third click on many tables
does not cycle back to descending; it clears the sort entirely and returns the table to
its original order, and code that assumes a fixed ascending-descending-ascending cycle
will confidently report the wrong direction on that third click, or on any table someone
else's code already clicked before yours ran.

The only signal worth reading is the one the page itself exposes: the `aria-sort`
attribute on the header cell, which takes the values `ascending`, `descending`, or is
absent entirely when nothing is sorted. Where a table skips the ARIA attribute, a
visible arrow icon or an added CSS class on the header is the fallback, but check
`aria-sort` first, because a table built with accessibility in mind keeps it accurate by
construction, while a class name is a styling detail that can drift from the actual
state.

```python
def current_sort(page, column_name):
    header = page.get_by_role("columnheader", name=column_name)
    direction = header.get_attribute("aria-sort")
    if direction in ("ascending", "descending"):
        return direction
    return None   # absent means: not the active sort column, whatever you clicked last
```

Read this after every click that is supposed to change sort, not before it, and read it
from the header itself rather than from a variable you incremented in your own loop.
The page's own state is the ground truth; your click counter is a guess about that
state.

## A second sort key can be active and invisible to a single check

Some tables support shift-click on a header to add a secondary sort key on top of the
primary one, most often seen on data-grid style tables built with a framework rather
than plain HTML. Sort by category, then shift-click price, and rows now sort by category
first and price within each category second. Nothing in the URL changes on a
client-side table, and checking `aria-sort` on the price header alone finds only that
one column's direction, not the fact that category is still active above it.

The practical answer is to check every sortable header's `aria-sort` in one pass rather
than the single column you expect to be active, and to treat more than one non-null
result as a signal that a compound sort is in play:

| Header checked | `aria-sort` read | What it means |
|---|---|---|
| Category | `ascending` | Primary key, applied first |
| Price | `ascending` | Secondary key, applied within each category |
| Date added | `None` | Not part of the current sort |

A scraper that only asks "what is the sort on the column I clicked" answers a narrower
question than the one that matters, which is "what is the sort on the table." If your
own extraction logic does not depend on sort order at all, which is the point of the
next section, this distinction stops being a problem to solve and becomes trivia about
a table you no longer need to interpret.

## The safer extraction: read once, key on identity, sort it yourself

Given all of the above, the least fragile approach is often to sidestep the page's sort
entirely. Read the table once in whatever order it loads in by default, key every row on
a genuine identifier, an id attribute, a SKU, a stable link, anything that survives a
re-render, and sort the resulting list in Python after extraction. You then trust your
own comparator, which you can test, instead of a page's comparator, which you cannot.

This also sidesteps every failure mode above at once. A page that resorts client-side
cannot desync your name-price pairing, because you never read the table a second time
in a different order. A page that compares strings cannot hand you a numerically wrong
result, because your own sort parses the number. A hidden secondary sort key cannot
change what rows you have, because you already have all of them before any header was
clicked.

```python
import re

def parse_price(text):
    # strip currency symbols and thousands separators before comparing
    cleaned = re.sub(r"[^\d.]", "", text or "")
    return float(cleaned) if cleaned else None

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/inventory", wait_until="networkidle")

    rows = page.locator("table tbody tr").evaluate_all(
        """rows => rows.map(r => ({
            id: r.dataset.rowId,
            name: r.querySelector('.name')?.innerText.trim(),
            price_text: r.querySelector('.price')?.innerText.trim(),
        }))"""
    )
    for row in rows:
        row["price"] = parse_price(row["price_text"])

    rows.sort(key=lambda r: (r["price"] is None, r["price"]))
    # rows is now correctly ordered by numeric price, independent of
    # whatever the page's own header click would have produced
```

`(r["price"] is None, r["price"])` pushes unparseable rows to the end instead of
crashing the sort on a `None` comparison, which is the kind of edge case a live table
produces more often than a clean sample suggests. The header click, if you use it at
all, becomes a convenience for a human looking at the page, not a step your extraction
depends on.

## Pagination and sorting share a reset you did not ask for

If the table paginates independently of its sort control, changing the sort frequently
resets the pager back to page one as a side effect. That makes sense from the page's
point of view: a new order means page one is a different set of rows than it was a
moment ago, so starting over is the only consistent behavior. It is not the behavior a
crawler expects if it treats sort and pagination as unrelated settings.

The bug this produces is specific: a multi-page crawl that changes the sort partway
through, for whatever reason, from a config value read mid-run, from a retry that
re-applies a sort you forgot was already active, walks back to page one and re-reads
rows it already collected. Left unhandled, this either duplicates rows in the output or,
if the crawl also de-duplicates by position instead of identity, silently drops the
rows that got pushed out of the range it re-reads.

```python
# wrong: changes sort mid-crawl, which can reset the pager to page one
for page_num in range(1, total_pages + 1):
    if page_num == 5:
        page.get_by_role("columnheader", name="Date").click()   # resets to page 1
    go_to_page(page, page_num)
    collected.extend(read_rows())

# right: fix the sort before pagination starts, never touch it mid-crawl
page.get_by_role("columnheader", name="Date").click()
page.wait_for_timeout(300)
for page_num in range(1, total_pages + 1):
    go_to_page(page, page_num)
    collected.extend(read_rows())
```

Combined with keying rows on identity rather than position, a duplicate page turns into
a harmless overwrite instead of a silent double-count: a `dict` keyed on `row["id"]`
absorbs the same row read twice without complaint, which is one more reason identity
beats position everywhere in this pattern, not only inside a single page's sort.
[Scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) covers the
re-query mechanics a page turn needs on its own; the addition here is specifically to
never change the sort once that loop has started.

## Conclusion

A sortable table asks you to trust three things you should not: that row position is a
stable key, that the page's comparator sorts numbers as numbers, and that a click
counter tells you the current direction. None of those hold in general. The fix for all
three is the same habit: read the rows once, keyed on an identifier that survives a
re-render, and treat the page's sort as a convenience for a human rather than a step
your extraction depends on. Where you do need to read the page's own sort state, read
`aria-sort` fresh every time rather than tracking it yourself, and check every sortable
header, not just the one you clicked, because a second key can be active without a
single obvious signal announcing it. The rows are more durable than any order they
happen to be in.

## Short answers to the questions that lead here

**Does clicking a sort header fetch new rows?** Usually not. Most tables re-render the
same row set in a new order client-side; the row-to-position mapping changes, the rows
themselves do not. Confirm by comparing row count before and after, and by watching for
a matching network request.

**Why does a numeric column sort in the wrong order?** Because the sort is comparing
rendered text, not parsed numbers. "1,200" sorts before "99" under a plain string
compare. This is common enough on client-side tables that you should assume it rather
than check for it, and sort numeric columns yourself after extraction.

**How do I know if a column is currently sorted ascending or descending?** Read the
`aria-sort` attribute on that header cell, fresh, every time. Do not count your own
clicks: a third click on many tables clears the sort back to unsorted instead of
cycling, which breaks any counter that assumes three states in a fixed loop.

**Can a table be sorted by two columns at once?** Yes, usually via shift-click for a
secondary key, and it will not show up if you only check `aria-sort` on the column you
expect to be primary. Check every sortable header in one pass if multi-column sort is a
possibility on the table you are reading.

**Should I trust the page's sort order for my output?** Generally no. Read the table
once in its default order, keyed on a stable identifier, and sort the extracted rows in
Python. You then own the comparator and can test it, instead of inheriting whatever the
page's client-side sort happens to implement.

**Can changing the sort break a paginated crawl?** Yes. Changing sort mid-crawl can reset
the pager to page one, and a loop that does not account for that will re-read rows it
already has. Fix the sort once before pagination starts and leave it alone for the rest
of the crawl.

## Sources

- WAI-ARIA `aria-sort` property definition, which specifies `ascending`, `descending`,
  `other`, and `none` as the only valid states, used exactly as read in the
  `aria-sort` section above.
- Playwright's [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  [`get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute),
  and [`evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all),
  used as documented upstream. The browser this library returns is a real Playwright
  `Browser`, so none of these calls behave differently under it.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  used above to tell a client-side sort apart from a server-side one by the presence or
  absence of a matching request.

**See also:** [scraping HTML tables](how-to-scrape-html-tables-playwright.md) for the
base extraction recipe this page assumes, [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading a server-side sort's request directly instead of the table it produces,
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) for the re-query
mechanics a page turn needs, and [scraping virtual scrolling tables](how-to-scrape-virtual-scrolling-tables-playwright.md)
for the sibling case where rows are recycled DOM nodes instead of a stable set.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of a
table scraper here paired names and prices by row index across a re-sort, and it shipped
for two weeks before anyone noticed every listing above the median price had the wrong
name attached to it.*
