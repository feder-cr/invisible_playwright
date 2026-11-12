---
title: "How to scrape virtual scrolling tables with Playwright"
description: "Scrape virtual scrolling tables with Playwright: measure the row pitch, step by less than one window, extract on sight because the nodes are recycled, dedupe on the row id, and stop on the declared total."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 104
---


# How to scrape virtual scrolling tables with Playwright

**To scrape a virtual scrolling table with Playwright, treat the DOM as a moving
window instead of the dataset: measure the row pitch from the rendered rows, scroll
the container by fewer pixels than one window is tall, extract every visible row into
plain data on each step before the nodes are recycled, dedupe on a row identifier read
from a data attribute, and stop when the collected count reaches the total the grid
declares in `aria-rowcount` or in the response that fed it.**

A virtualised table renders only the rows you can see, plus a few above and below, and
reuses those same nodes for every row that scrolls into place. Twelve thousand records,
twenty-four `<tr>` elements, for the whole run. The record in the fourth row now is not
the one that sat there a moment ago.

Every habit from an ordinary table breaks against that. Counting rows measures the
viewport. Holding an element reference hands you back somebody else's data. Scrolling in
round pixel numbers skips records that never render, and nothing raises an error when it
does.

## The row count in the DOM is the window, not the dataset

One call settles whether a table is virtualised. In an ordinary table the scroll
container's `scrollHeight` is close to the rendered rows times their height. In a
virtualised one the container is sized to the whole dataset while the rows inside it are
a handful, because a spacer or a `translateY` offset holds open the space for records
that do not exist yet.

```python
from invisible_playwright import InvisiblePlaywright

GEOMETRY = """
(container) => {
    const rows = [...container.querySelectorAll('tbody tr, [role="row"]')];
    const tops = rows.map(r => r.getBoundingClientRect().top).sort((a, b) => a - b);
    let pitch = 0;
    for (let i = 1; i < tops.length; i++) {
        const gap = tops[i] - tops[i - 1];
        if (gap > 0) { pitch = pitch ? Math.min(pitch, gap) : gap; }
    }
    const grid = container.closest('[role="grid"], [role="treegrid"]') || container;
    return {
        rendered: rows.length,
        rowPitch: pitch,
        clientHeight: container.clientHeight,
        scrollHeight: container.scrollHeight,
        declaredTotal: parseInt(grid.getAttribute('aria-rowcount') || '-1', 10),
    };
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/records", wait_until="domcontentloaded")

    container = page.locator("div.grid-scroller")
    container.wait_for(state="visible")
    geometry = container.evaluate(GEOMETRY)
    print(geometry)
    # {'rendered': 24, 'rowPitch': 36, 'clientHeight': 720,
    #  'scrollHeight': 442800, 'declaredTotal': 12300}
```

A 442800 pixel scroller at a 36 pixel pitch is a 12300 row table, and the DOM is holding
two rows in every thousand. `rows.count()` returns 24 at the top, in the middle and at
the end: it is the window size, not a progress figure and not a stop. If `scrollHeight`
and the rendered rows agree instead, nothing is virtualised and the one-call extraction
in [scraping HTML tables](how-to-scrape-html-tables-playwright.md) is all you need.

The rendered count is not even the visible count, because virtualisers keep an overscan
buffer above and below the viewport to cover the frame where the window moves. So the
number that matters is `clientHeight / pitch`, and the pitch comes from the smallest
positive gap between row tops rather than from `offsetHeight`, which misses the borders
the next row is offset by.

## Recycled nodes lie before they throw

A row reference fails in two ways, and the harmless one throws. When the virtualiser
removes the node, an `ElementHandle` captured earlier raises an error about an element
not attached to the document.

The other way is silent. Many virtualisers never destroy a row: they keep the same
`<tr>`, rewrite its cells, and move it with a transform. The handle resolves, returns a
value, raises nothing, and that value belongs to a record you have not seen. The damage
surfaces later, as one record's id beside another record's cells.

A `Locator` fixes the first failure and not the second. It re-resolves on every action so
it never goes stale, but `rows.nth(3)` means the fourth row in the window right now,
which after a scroll is a different record. The
[load-more button loop](how-to-scrape-load-more-button-playwright.md) covers those
semantics; here the rule is stronger. Hold no element reference across a scroll, harvest
the whole window in one call, and let the values cross the boundary as JSON.

```python
HARVEST = """
(rows) => rows
    .filter(row => !row.querySelector('th, [role="columnheader"]'))
    .map(row => ({
        row_id: row.getAttribute('data-id')
             || row.getAttribute('data-row-key')
             || row.getAttribute('aria-rowindex'),
        values: [...row.querySelectorAll('td, [role="gridcell"]')]
                    .map(cell => cell.innerText.trim()),
    }))
"""

def harvest(container):
    rows = container.locator('tbody tr, [role="row"]')
    return rows.evaluate_all(HARVEST)
```

One round trip per step, and nothing survives it that can rot. The filter is there
because in an ARIA grid the header row sits in the same collection as the data.

## Scroll by the measured pitch, not by a pixel guess

A fixed step costs you records without ever failing. A window 720 pixels tall at a 36
pixel pitch survives a 500 pixel step. Take a denser grid, 28 pixel rows in a 336 pixel
viewport, keep the same 500, and six rows between the two windows never render. Rows
that never render are rows you never see, and the loop finishes green.

Step by the pitch times a count smaller than the window so consecutive windows overlap.
Seventy percent of the visible count leaves three rows of overlap in a twelve row
window, enough to absorb a lagging repaint, and the overlap costs nothing once you
dedupe.

```python
STEP = """
(container, distance) => { container.scrollTop += distance; return container.scrollTop; }
"""

MOVED = """
(a) => {
    const container = document.querySelector(a.sel);
    if (!container) return false;
    const rows = [...container.querySelectorAll('tbody tr, [role="row"]')];
    const body = rows.find(r => !r.querySelector('th, [role="columnheader"]'));
    if (!body) return false;
    const id = body.getAttribute('data-id')
            || body.getAttribute('data-row-key')
            || body.getAttribute('aria-rowindex');
    return id !== a.anchor;
}
"""

pitch = geometry["rowPitch"] or 1
visible = max(1, int(geometry["clientHeight"] // pitch))
step_pixels = max(int(pitch), int(pitch * visible * 0.7))
```

Scroll the container, not the page. Where the scroller is an inner element with
`overflow: auto`, `window.scrollBy` moves nothing at all, and `page.mouse.wheel` after
`container.hover()` is the alternative for grids that only repaint on a real wheel
event. Then wait for the window to move, comparing the first row id before and after,
the way an [infinite scroll loop](how-to-scrape-infinite-scroll-playwright.md) waits for
growth instead of sleeping.

## Dedupe on the row identifier, not on position or text

The same visual slot holds different records over time, and the overlap you just built
hands you the same record twice. So the key has to identify the record, not the position:
`data-id`, `data-row-key`, whatever the grid stamps on the row for its own bookkeeping.
Read it in the call that reads the cells, as `row_id` does above.

`aria-rowindex` is a position, and it is a safe fallback only while the ordering holds
still. Sort a column and row 4000 is a different record under the same index, so an
index-keyed set discards rows it never collected. Scope it to the sort state and treat it
as fragile.

A hash of the cell text is the last resort. It merges genuine duplicates in silence: two
records with identical visible fields collapse into one, which looks exactly like a
legitimate overlap hit. Persist the seen set when a run has to survive an interruption,
the same bookkeeping an
[incremental scrape](how-to-scrape-only-new-items-incremental-playwright.md) keeps
between runs.

## The declared total is the only honest stopping condition

Three popular stops are wrong, starting with a fixed number of steps. "No new rows this
step" fires on the first lagging repaint or the first fetch in flight, which is what a
grid that loads its tail on demand produces while more data is coming. Reaching the
bottom means nothing either: the spacer is sized from a count the grid may still revise.

The number to trust is the one the page states. `aria-rowcount` exists for this case: it
declares the size of the full set when only a subset is rendered, and `-1` means unknown,
so treat that as absent rather than as a number. Failing that, the response that
populated the grid nearly always carries a total.

```python
def declared_total(page, grid_selector):
    raw = page.locator(grid_selector).get_attribute("aria-rowcount")
    if raw is not None and int(raw) >= 0:
        return int(raw)
    return None

def total_from_response(page, url):
    with page.expect_response(
        lambda r: "/api/" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.goto(url, wait_until="domcontentloaded")
    payload = caught.value.json()
    for key in ("total", "totalCount", "recordsTotal", "count"):
        if isinstance(payload.get(key), int):
            return payload[key]
    return None
```

Capture that response even when `aria-rowcount` is present, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) has
the hooks. The status line reading "1-50 of 12,345" is the last resort: it is localised
text, and both the separator and the word between the numbers move with the locale.
Whichever source you use, compare the collected count against it at the end and report
the shortfall out loud.

## Sorting and filtering restart the collection under you

Any sort or filter resets the virtual window to the top and re-keys every position, which
is obvious when you trigger it. The problem is the resets you did not intend: a saved
view applied a second after load, a live refresh, a stray keypress, a hover that lands
on a column header while you position the cursor to wheel.

Then the dedupe hides the damage. Rows keep arriving, all of them already in the seen
set, so the collected count stops growing while the loop works perfectly. Any "no new
rows" rule reads that as exhaustion and stops, and the result is a partial dataset that
looks complete.

Detect it with a signature taken from the grid before each step. `aria-sort` on the
header cells carries `ascending`, `descending` or `none`, the declared total moves when
a filter narrows the set, and `scrollTop` jumping backwards without you is the third
witness.

```python
class GridReset(RuntimeError):
    pass

SIGNATURE = """
(grid) => ({
    total: grid.getAttribute('aria-rowcount'),
    sort: [...grid.querySelectorAll('[aria-sort]')]
              .map(h => h.getAttribute('aria-sort')).join('|'),
})
"""
```

Treat a change as the end of the pass, not as something to recover from mid-loop. Each
sort or filter state is its own collection, with its own seen set and its own total.

## The whole loop, and where it gives up

The pieces assemble into one pass. The harvest at the top of each round serves twice, as
the data and as the anchor for the scroll wait, so no reference outlives a step.

```python
import random
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from invisible_playwright import InvisiblePlaywright

def scrape_virtual_table(url, scroller, grid, seed=42, max_steps=4000):
    rng = random.Random(seed)
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")

        container = page.locator(scroller)
        container.wait_for(state="visible")
        geometry = container.evaluate(GEOMETRY)
        pitch = geometry["rowPitch"] or 1
        visible = max(1, int(geometry["clientHeight"] // pitch))
        step_pixels = max(int(pitch), int(pitch * visible * 0.7))

        total = declared_total(page, grid)
        signature = page.locator(grid).evaluate(SIGNATURE)
        seen, collected, stalled = set(), [], 0

        for _ in range(max_steps):
            window_rows = harvest(container)
            for record in window_rows:
                if record["row_id"] and record["row_id"] not in seen:
                    seen.add(record["row_id"])
                    collected.append(record)

            if total is not None and len(seen) >= total:
                break

            anchor = window_rows[0]["row_id"] if window_rows else None
            container.evaluate(STEP, step_pixels)
            page.wait_for_timeout(rng.randint(140, 520))

            if page.locator(grid).evaluate(SIGNATURE) != signature:
                raise GridReset(f"sort or filter changed after {len(seen)} rows")

            try:
                page.wait_for_function(
                    MOVED, arg={"sel": scroller, "anchor": anchor}, timeout=8000
                )
                stalled = 0
            except PlaywrightTimeout:
                stalled += 1
                if stalled >= 3:
                    break

        return collected, total
```

The pause before each step is not politeness. A virtual grid usually fetches a page of
records per step, so a loop at frame speed is both a request pattern the backend notices
and a uniform cadence in an interaction log. Drawing it from the browser's own seed keeps
the run reproducible while no two gaps match.

Now the cases where none of this helps. Some grids paint their cells into a `<canvas>`,
so there is no row markup at any scroll position and the
[canvas extraction](how-to-extract-data-from-canvas-charts-playwright.md) path or the
network response is all that is left. Wide grids virtualise columns too, so a row
harvested at horizontal offset zero comes back short of cells, and the missing ones are
absent rather than empty. And when a paged API feeds the grid, that response beats the
DOM in every respect: it carries the total and the ids, and it recycles nothing.

## Conclusion

A virtualised table punishes the assumption that the DOM holds the data. Measure the
geometry so you know the pitch and the window. Step by less than one window so nothing
slips past unrendered. Harvest each window immediately, because the nodes underneath are
reused and a stale reference answers with the wrong record instead of an error. Key the
results on a row identifier and stop on a declared total, not on a symptom. The scrolling
is easy. Knowing which of the twenty-four rows in front of you have already been counted
is the whole job.

## Short answers to the questions that lead here

**Why does my scraper only return the last twenty rows?** Because the table is
virtualised and those twenty are the entire DOM. Rows are recycled as you scroll, so an
extraction that runs after the loop can only see the window still on screen. Extract on
every step instead.

**Why does the row count never increase while I scroll?** It is the window size, not the
dataset size. A virtual grid keeps a fixed number of nodes and rewrites them, so
`count()` stays flat from the first frame to the last.

**Do Locators fix the stale element problem here?** They fix the crash, not the mistake.
A Locator re-resolves and never goes stale, but `nth(3)` means the fourth row currently
in the window, which after a scroll is a different record.

**How far should each scroll step move?** By the measured row pitch times a count smaller
than the visible rows, around seventy percent, so consecutive windows overlap. A fixed
pixel step skips rows on any grid denser than the one you tuned it on.

**How do I know when to stop?** Read the total the page declares, in `aria-rowcount` on
the grid or in the response that fed it, and stop when the deduped count reaches it.
Treat `aria-rowcount="-1"` as no total, not as a number.

**My collection stops early and the data looks fine. What happened?** Something resorted
or refiltered the grid, which reset the window to the top. Every row after that was
already in your seen set, so growth stopped while the loop kept running. Take a signature
of the sort state and the declared total before each step.

## Sources

- Playwright's [`evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all),
  which hands the whole matched set to one JavaScript call, retrieved 2026-08-28.
- Playwright's [`wait_for_function`](https://playwright.dev/python/docs/api/class-page#page-wait-for-function)
  and [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  used for the window-moved condition and the total in the feed response, retrieved
  2026-08-28.
- Playwright's [ElementHandle documentation](https://playwright.dev/python/docs/api/class-elementhandle)
  and [`mouse.wheel`](https://playwright.dev/python/docs/api/class-mouse#mouse-wheel),
  retrieved 2026-08-28.
- The WAI-ARIA attributes this article reads rather than infers: `aria-rowcount`, which
  declares the full row count when only a subset is rendered and uses `-1` for unknown,
  `aria-rowindex`, and `aria-sort` on header cells.

**See also:** [scraping HTML tables](how-to-scrape-html-tables-playwright.md) for the
table that is not virtualised,
[scraping infinite scroll](how-to-scrape-infinite-scroll-playwright.md) for the
viewport-driven sibling that appends instead of recycling,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for
the feed that carries the total and the ids, and
[the load-more button loop](how-to-scrape-load-more-button-playwright.md) for the
locator-versus-handle rule in its simpler form.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The recycled-node read is
the one that cost a whole dataset here: the references never threw, they resolved against
reused rows, and the run wrote one record's id beside another record's cells.*
